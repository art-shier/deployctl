package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func catalogFixture(generated string, versions ...string) string {
	items := make([]string, 0, len(versions))
	for _, v := range versions {
		items = append(items, `{"version":"`+v+`","published_at":"2026-11-01T00:00:00Z","cli_sha256":"`+strings.Repeat("a", 64)+`","server_sha256":"`+strings.Repeat("b", 64)+`"}`)
	}
	return `{"schema_version":1,"generated_at":"` + generated + `","releases":[` + strings.Join(items, ",") + `]}`
}

func catalogClient(fn func(*http.Request) (*http.Response, error)) *http.Client {
	return &http.Client{Transport: roundTripFunc(fn)}
}
func catalogResponse(body string, status int) *http.Response {
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}
}

func TestCatalogSeedImmediateOfflineAndPersistentRestart(t *testing.T) {
	dir := t.TempDir()
	seed := filepath.Join(t.TempDir(), "seed.json")
	if err := os.WriteFile(seed, []byte(catalogFixture("2026-11-01T00:00:00Z", "v1.15.1")), 0600); err != nil {
		t.Fatal(err)
	}
	var calls int
	client := catalogClient(func(*http.Request) (*http.Response, error) { calls++; return nil, errors.New("offline") })
	p := newSystemReleaseCatalog(dir, seed, client)
	snap := p.Snapshot()
	if calls != 0 || snap.Source != "bundled" || !snap.Stale || len(snap.Releases) != 1 || snap.LastSuccessAt != nil {
		t.Fatalf("seed %#v calls=%d", snap, calls)
	}
	snap = p.Sync(context.Background())
	if snap.SyncError != "network_error" || len(snap.Releases) != 1 || snap.Source != "bundled" || snap.LastAttemptAt == nil || snap.NextSyncAt == nil {
		t.Fatalf("offline %#v", snap)
	}
	p.client = catalogClient(func(*http.Request) (*http.Response, error) {
		return catalogResponse(catalogFixture("2026-11-03T00:00:00Z", "v1.16.0", "v1.15.1"), 200), nil
	})
	snap = p.Sync(context.Background())
	if snap.SyncError != "" || snap.Source != "remote" || snap.Stale || snap.LastSuccessAt == nil || len(snap.Releases) != 2 {
		t.Fatalf("remote %#v", snap)
	}
	restarted := newSystemReleaseCatalog(dir, seed, client).Snapshot()
	if restarted.Source != "cache" || len(restarted.Releases) != 2 || restarted.Releases[0].Version != "v1.16.0" || restarted.LastSuccessAt == nil {
		t.Fatalf("restart %#v", restarted)
	}
	// A newer seed wins over an older persisted manifest, without inventing a live success.
	os.WriteFile(seed, []byte(catalogFixture("2026-11-04T00:00:00Z", "v1.17.0")), 0600)
	newer := newSystemReleaseCatalog(dir, seed, client).Snapshot()
	if newer.Source != "bundled" || !newer.Stale || newer.LastSuccessAt != nil || newer.Releases[0].Version != "v1.17.0" {
		t.Fatalf("newer seed %#v", newer)
	}
}

func TestCatalogRejectsInvalidManifestAndRetainsLastGood(t *testing.T) {
	good := catalogFixture("2026-11-03T00:00:00Z", "v1.16.0")
	tooMany := make([]string, 1001)
	for i := range tooMany {
		tooMany[i] = fmt.Sprintf("v1.15.%d", i)
	}
	invalid := map[string]string{
		"truncated": good[:len(good)-1], "unknown": strings.Replace(good, `"schema_version":1`, `"schema_version":1,"other":true`, 1),
		"schema":               strings.Replace(good, `"schema_version":1`, `"schema_version":2`, 1),
		"case variant":         strings.Replace(good, `"schema_version":1`, `"SCHEMA_VERSION":1`, 1),
		"duplicate field":      strings.Replace(good, `"schema_version":1`, `"schema_version":1,"schema_version":1`, 1),
		"case variant release": strings.Replace(good, `"version":`, `"VERSION":`, 1),
		"checksum":             strings.Replace(good, strings.Repeat("a", 64), strings.Repeat("A", 64), 1),
		"duplicate version":    catalogFixture("2026-11-03T00:00:00Z", "v1.16.0", "v1.16.0"),
		"prerelease":           catalogFixture("2026-11-03T00:00:00Z", "v1.16.0-rc.1"),
		"non UTC":              strings.Replace(good, "2026-11-03T00:00:00Z", "2026-11-03T00:00:00+00:00", 1),
		"missing releases":     `{"schema_version":1,"generated_at":"2026-11-03T00:00:00Z"}`,
		"oversized":            strings.Repeat(" ", 1024*1024) + good,
		"too many":             catalogFixture("2026-11-03T00:00:00Z", tooMany...),
		"trailing":             good + ` {}`,
	}
	for name, bad := range invalid {
		t.Run(name, func(t *testing.T) {
			body := good
			dir := t.TempDir()
			p := newSystemReleaseCatalog(dir, "", catalogClient(func(*http.Request) (*http.Response, error) { return catalogResponse(body, 200), nil }))
			p.Sync(context.Background())
			before, err := os.ReadFile(filepath.Join(dir, "update-index.cache.json"))
			if err != nil {
				t.Fatal(err)
			}
			body = bad
			snap := p.Sync(context.Background())
			if snap.SyncError != "invalid_manifest" || len(snap.Releases) != 1 || !snap.Stale || snap.Releases[0].Version != "v1.16.0" {
				t.Fatalf("invalid %#v", snap)
			}
			after, _ := os.ReadFile(filepath.Join(dir, "update-index.cache.json"))
			if string(before) != string(after) {
				t.Fatal("failed validation changed disk cache")
			}
		})
	}
}

func TestCatalogEmptyAndProtocolFiltering(t *testing.T) {
	body := catalogFixture("2026-11-03T00:00:00Z", "v1.15.1", "v2.0.0", "v1.16.0", "v1.14.0")
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) { return catalogResponse(body, 200), nil }))
	empty := p.Snapshot()
	raw, _ := json.Marshal(empty)
	if empty.Source != "none" || !empty.Stale || !strings.Contains(string(raw), `"releases":[]`) || !strings.Contains(string(raw), `"generated_at":null`) {
		t.Fatalf("empty %s", raw)
	}
	snap := p.Sync(context.Background())
	if len(snap.Releases) != 2 || snap.Releases[0].Version != "v1.16.0" || snap.Releases[1].Version != "v1.15.1" {
		t.Fatalf("filter %#v", snap)
	}
}

func TestCatalogRejectsDigestMutationAndRollback(t *testing.T) {
	good := catalogFixture("2026-11-03T00:00:00Z", "v1.16.0")
	for name, body := range map[string]string{
		"CLI digest":    strings.Replace(good, strings.Repeat("a", 64), strings.Repeat("c", 64), 1),
		"server digest": strings.Replace(good, strings.Repeat("b", 64), strings.Repeat("c", 64), 1),
		"rollback":      catalogFixture("2026-11-02T00:00:00Z", "v1.15.1"),
	} {
		t.Run(name, func(t *testing.T) {
			response := good
			p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) { return catalogResponse(response, 200), nil }))
			first := p.Sync(context.Background())
			response = body
			after := p.Sync(context.Background())
			if after.SyncError != "invalid_manifest" || !after.LastSuccessAt.Equal(*first.LastSuccessAt) || after.Releases[0] != first.Releases[0] {
				t.Fatalf("mutated %#v", after)
			}
		})
	}
}

func TestCatalogFreshnessRetryAndForcedBypass(t *testing.T) {
	var calls int
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) { calls++; return nil, errors.New("offline") }))
	first := p.Sync(context.Background())
	p.sync(context.Background(), false)
	if calls != 1 || first.NextSyncAt.Sub(*first.LastAttemptAt) < 59*time.Second || first.NextSyncAt.Sub(*first.LastAttemptAt) > 61*time.Second {
		t.Fatal("failure retry", calls, first)
	}
	p.Sync(context.Background())
	if calls != 2 {
		t.Fatal("forced request delayed", calls)
	}
	p.client = catalogClient(func(*http.Request) (*http.Response, error) {
		calls++
		return catalogResponse(catalogFixture("2026-11-03T00:00:00Z", "v1.16.0"), 200), nil
	})
	fresh := p.Sync(context.Background())
	p.sync(context.Background(), false)
	if calls != 3 || fresh.Stale || fresh.NextSyncAt.Sub(*fresh.LastSuccessAt) != 10*time.Minute {
		t.Fatal("fresh retry", calls, fresh)
	}
	old := time.Now().UTC().Add(-11 * time.Minute)
	if err := p.persist([]byte(catalogFixture("2026-11-03T00:00:00Z", "v1.16.0")), old); err != nil {
		t.Fatal(err)
	}
	restart := newSystemReleaseCatalog(filepath.Dir(p.cachePath), "", p.client).Snapshot()
	if !restart.Stale || restart.Source != "cache" {
		t.Fatal("old disk cache looks fresh", restart)
	}
}

func TestCatalogCorruptCacheFallsBackToSeed(t *testing.T) {
	dir := t.TempDir()
	seed := filepath.Join(dir, "seed.json")
	os.WriteFile(seed, []byte(catalogFixture("2026-11-01T00:00:00Z", "v1.15.1")), 0600)
	os.WriteFile(filepath.Join(dir, "update-index.cache.json"), []byte(`{"manifest":`), 0600)
	p := newSystemReleaseCatalog(dir, seed, catalogClient(func(*http.Request) (*http.Response, error) { t.Fatal("unexpected network"); return nil, nil }))
	if snap := p.Snapshot(); snap.Source != "bundled" || len(snap.Releases) != 1 {
		t.Fatal(snap)
	}
}

func TestCatalogSnapshotDuringInflightSyncAndLeaderCancellation(t *testing.T) {
	dir := t.TempDir()
	seed := filepath.Join(dir, "seed.json")
	os.WriteFile(seed, []byte(catalogFixture("2026-11-01T00:00:00Z", "v1.15.1")), 0600)
	started := make(chan struct{})
	p := newSystemReleaseCatalog(dir, seed, catalogClient(func(r *http.Request) (*http.Response, error) {
		close(started)
		<-r.Context().Done()
		return nil, r.Context().Err()
	}))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan CatalogSnapshot, 1)
	go func() { done <- p.Sync(ctx) }()
	<-started
	snapshot := make(chan CatalogSnapshot, 1)
	go func() { snapshot <- p.Snapshot() }()
	select {
	case snap := <-snapshot:
		if len(snap.Releases) != 1 || snap.LastAttemptAt == nil {
			t.Fatal(snap)
		}
	case <-time.After(time.Second):
		t.Fatal("snapshot blocked on network")
	}
	cancel()
	select {
	case snap := <-done:
		if len(snap.Releases) != 1 || snap.SyncError != "network_error" {
			t.Fatal(snap)
		}
	case <-time.After(time.Second):
		t.Fatal("sync ignored cancellation")
	}
}

func TestCatalogCookieSyncRequiresSameOrigin(t *testing.T) {
	db := testutil.Store(t)
	const cookie = "session-for-catalog"
	if err := db.CreateSession(context.Background(), auth.HashToken(cookie)); err != nil {
		t.Fatal(err)
	}
	var calls int
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) { calls++; return nil, errors.New("offline") }))
	s := New(db, Options{OwnerHash: auth.HashToken("owner"), PublicOrigin: "https://ctl.example", SystemReleases: p})
	for _, tc := range []struct {
		origin string
		want   int
	}{{"", 403}, {"https://attacker.example", 403}, {"https://ctl.example", 200}} {
		r := httptest.NewRequest("POST", "/api/v1/system-updates/catalog/sync", strings.NewReader(`{}`))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Origin", tc.origin)
		r.AddCookie(&http.Cookie{Name: "ctl_session", Value: cookie})
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatal(tc.origin, w.Code, w.Body.String())
		}
	}
	if calls != 1 {
		t.Fatal("rejected mutation reached provider", calls)
	}
}

func TestCatalogClassifiesFailures(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		err    error
		want   string
	}{
		{"network", 0, errors.New("secret upstream URL"), "network_error"}, {"timeout", 0, context.DeadlineExceeded, "timeout"},
		{"rate", 429, nil, "rate_limited"}, {"forbidden rate", 403, nil, "rate_limited"}, {"not found", 404, nil, "not_found"}, {"server", 503, nil, "http_error"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) {
				if tc.err != nil {
					return nil, tc.err
				}
				return catalogResponse("", tc.status), nil
			}))
			snap := p.Sync(context.Background())
			if snap.SyncError != tc.want || snap.LastAttemptAt == nil || !snap.Stale {
				t.Fatalf("failure %#v", snap)
			}
		})
	}
}

func TestCatalogWriteFailureKeepsPreviousData(t *testing.T) {
	dir := t.TempDir()
	seed := filepath.Join(dir, "seed.json")
	os.WriteFile(seed, []byte(catalogFixture("2026-11-01T00:00:00Z", "v1.15.1")), 0600)
	blocked := filepath.Join(dir, "not-a-directory")
	os.WriteFile(blocked, []byte("x"), 0600)
	p := newSystemReleaseCatalog(blocked, seed, catalogClient(func(*http.Request) (*http.Response, error) {
		return catalogResponse(catalogFixture("2026-11-03T00:00:00Z", "v1.16.0"), 200), nil
	}))
	snap := p.Sync(context.Background())
	if snap.SyncError != "cache_write_failed" || snap.Source != "bundled" || snap.Releases[0].Version != "v1.15.1" {
		t.Fatalf("write %#v", snap)
	}
}

func TestCatalogOverlappingSyncJoinsAndWaiterCancellation(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	var calls atomic.Int32
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		close(started)
		select {
		case <-release:
			return catalogResponse(catalogFixture("2026-11-03T00:00:00Z", "v1.16.0"), 200), nil
		case <-r.Context().Done():
			return nil, r.Context().Err()
		}
	}))
	done := make(chan CatalogSnapshot, 1)
	go func() { done <- p.Sync(context.Background()) }()
	<-started
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	cancelled := p.Sync(ctx)
	if len(cancelled.Releases) != 0 {
		t.Fatal(cancelled)
	}
	waiting := make(chan struct{}, 8)
	joined := make(chan CatalogSnapshot, 8)
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			joined <- p.Sync(&catalogWaitContext{Context: context.Background(), waiting: waiting})
		}()
	}
	for range 8 {
		select {
		case <-waiting:
		case <-time.After(time.Second):
			t.Fatal("sync did not join active request")
		}
	}
	close(release)
	wg.Wait()
	result := <-done
	if calls.Load() != 1 || result.Source != "remote" || len(result.Releases) != 1 {
		t.Fatalf("join calls=%d %#v", calls.Load(), result)
	}
	for range 8 {
		if snapshot := <-joined; snapshot.Source != "remote" || len(snapshot.Releases) != 1 {
			t.Fatal("joined result", snapshot)
		}
	}
}

// Signal when a caller has entered the cancellable in-flight wait. This avoids
// releasing the transport before all concurrent calls can join the same fetch.
type catalogWaitContext struct {
	context.Context
	waiting chan<- struct{}
	once    sync.Once
}

func (c *catalogWaitContext) Done() <-chan struct{} {
	c.once.Do(func() { c.waiting <- struct{}{} })
	return c.Context.Done()
}

func TestCatalogWorkerCancelsRequestAndStops(t *testing.T) {
	started := make(chan struct{})
	stopped := make(chan struct{})
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(r *http.Request) (*http.Response, error) {
		close(started)
		<-r.Context().Done()
		close(stopped)
		return nil, r.Context().Err()
	}))
	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("worker did not sync")
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("request did not cancel")
	}
}

func TestCatalogHTTPReadOnlyAndSync(t *testing.T) {
	var calls int
	p := newSystemReleaseCatalog(t.TempDir(), "", catalogClient(func(*http.Request) (*http.Response, error) { calls++; return nil, errors.New("offline") }))
	s := New(nil, Options{OwnerHash: auth.HashToken("owner"), SystemReleases: p})
	call := func(method, path, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer owner")
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	w := call("GET", "/api/v1/system-updates/catalog", "")
	if w.Code != 200 || calls != 0 || !strings.Contains(w.Body.String(), `"source":"none"`) {
		t.Fatal(w.Code, w.Body.String(), calls)
	}
	w = call("POST", "/api/v1/system-updates/catalog/sync", `{"force":true}`)
	if w.Code != 400 || calls != 0 {
		t.Fatal(w.Code, w.Body.String(), calls)
	}
	w = call("POST", "/api/v1/system-updates/catalog/sync", `{}`)
	if w.Code != 200 || calls != 1 || !strings.Contains(w.Body.String(), `"sync_error":"network_error"`) {
		t.Fatal(w.Code, w.Body.String(), calls)
	}
	w = call("GET", "/api/v1/system-updates/releases", "")
	if w.Code != 200 || strings.TrimSpace(w.Body.String()) != "[]" || calls != 1 {
		t.Fatal(w.Code, w.Body.String(), calls)
	}
}
