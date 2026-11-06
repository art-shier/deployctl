package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type fixedSystemReleases struct{ calls int }

func (*fixedSystemReleases) Artifact(_ context.Context, job domain.UpdateJob) ([]byte, error) {
	return []byte("verified fixture:" + job.ID), nil
}

func TestNodeCreatesOnlyOwnUpdaterEnrollment(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	node, invite, _, err := db.CreateNode(ctx, domain.NodeInput{Name: "node", Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, token, err := db.EnrollNode(ctx, auth.HashToken(invite), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	s := New(db, Options{OwnerHash: auth.HashToken("owner")})
	call := func(path, credential, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+credential)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	path := "/api/v1/node/updater-enrollment"
	if w := call(path, "owner", `{}`); w.Code != 401 {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, body := range []string{`{"kind":"center"}`, `{"target_id":"` + domain.NewID() + `"}`} {
		if w := call(path, token, body); w.Code != 400 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	w := call(path, token, `{}`)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	var enrollment struct {
		Token string `json:"enrollment_token"`
	}
	json.Unmarshal(w.Body.Bytes(), &enrollment)
	body := `{"helper_version":"v1.15.0","platform":"linux","protocol":1}`
	w = call("/api/v1/updater/enroll", enrollment.Token, body)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var identity struct {
		Kind   string `json:"kind"`
		Target string `json:"target_id"`
	}
	json.Unmarshal(w.Body.Bytes(), &identity)
	if identity.Kind != "node" || identity.Target != node.ID {
		t.Fatal(w.Body.String())
	}
	if w = call("/api/v1/updater/enroll", enrollment.Token, body); w.Code != 401 {
		t.Fatal("reused enrollment", w.Code)
	}
}

func (f *fixedSystemReleases) Releases(context.Context) ([]domain.SystemRelease, error) {
	f.calls++
	return []domain.SystemRelease{{Version: "v1.16.0", PublishedAt: time.Now().UTC(), CLISHA256: strings.Repeat("a", 64), ServerSHA256: strings.Repeat("b", 64)}}, nil
}
func TestUpdaterHTTPIdentityAndFrozenCatalog(t *testing.T) {
	db := testutil.Store(t)
	provider := &fixedSystemReleases{}
	s := New(db, Options{OwnerHash: auth.HashToken("owner"), SystemReleases: provider})
	call := func(method, path, token, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	w := call("POST", "/api/v1/system-updates/enrollments", "owner", `{"kind":"center","target_id":"center"}`)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	var enrollment struct {
		Token string `json:"enrollment_token"`
	}
	json.Unmarshal(w.Body.Bytes(), &enrollment)
	if w = call("POST", "/api/v1/updater/enroll", "owner", `{"helper_version":"v1.15.0","platform":"linux","protocol":1}`); w.Code != 401 {
		t.Fatal("owner enrolled", w.Code, w.Body.String())
	}
	w = call("POST", "/api/v1/updater/enroll", enrollment.Token, `{"helper_version":"v1.15.0","platform":"linux","protocol":1}`)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var identity struct {
		ID    string `json:"updater_id"`
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &identity)
	for _, path := range []string{"/api/v1/me", "/api/v1/system-updates", "/api/v1/nodes"} {
		if w = call("GET", path, identity.Token, ""); w.Code != 401 {
			t.Fatal("privilege", path, w.Code)
		}
	}
	if w = call("POST", "/api/v1/updater/heartbeat", identity.Token, `{"helper_version":"v1.15.0","current_version":"v1.14.0","platform":"linux","protocol":1,"ready":true,"blocked_reason":""}`); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	body := `{"request_id":"` + domain.NewID() + `","updater_ids":["` + identity.ID + `"],"version":"v1.16.0","parallelism":1,"backup_confirmed":true}`
	w = call("POST", "/api/v1/system-updates/jobs", "owner", body)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("POST", "/api/v1/system-updates/jobs", "owner", body); w.Code != 201 || provider.calls != 1 {
		t.Fatal("retry refetched", provider.calls, w.Code, w.Body.String())
	}
	w = call("POST", "/api/v1/updater/poll", identity.Token, `{}`)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var poll struct {
		Job domain.UpdateJob `json:"job"`
	}
	json.Unmarshal(w.Body.Bytes(), &poll)
	if poll.Job.Status != "leased" {
		t.Fatal(w.Body.String())
	}
	if w = call("GET", "/api/v1/updater/jobs/"+poll.Job.ID+"/package", identity.Token, ""); w.Code != 403 {
		t.Fatal("unstarted package", w.Code)
	}
	if w = call("GET", "/api/v1/updater/jobs/"+poll.Job.ID+"/package", "owner", ""); w.Code != 401 {
		t.Fatal("owner used updater transport", w.Code)
	}
	if w = call("POST", "/api/v1/updater/jobs/"+poll.Job.ID+"/start", identity.Token, `{}`); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", "/api/v1/updater/jobs/"+poll.Job.ID+"/package", identity.Token, ""); w.Code != 200 || w.Body.String() != "verified fixture:"+poll.Job.ID {
		t.Fatal("package", w.Code, w.Body.String())
	}
	if w = call("POST", "/api/v1/updater/jobs/"+poll.Job.ID+"/report", identity.Token, `{"status":"failed","stage":"failed","result_code":"raw secret output","current_version":"v1.14.0"}`); w.Code != 400 {
		t.Fatal("raw logs", w.Code, w.Body.String())
	}
}

func TestSystemReleaseProviderFixedOfficialURLs(t *testing.T) {
	var paths []string
	transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() != "https://github.com/art-shier/deployctl/releases/latest/download/update-index.json" {
			t.Fatal("unofficial", r.URL)
		}
		paths = append(paths, r.URL.String())
		body := catalogFixture("2026-11-01T00:00:00Z", "v1.16.0")
		return &http.Response{StatusCode: 200, Body: ioNopCloser{strings.NewReader(body)}, Header: make(http.Header)}, nil
	})
	p := newSystemReleaseCatalog(t.TempDir(), "", &http.Client{Transport: transport})
	p.Sync(context.Background())
	releases, err := p.Releases(context.Background())
	if err != nil || len(releases) != 1 || releases[0].Version != "v1.16.0" {
		t.Fatal(releases, err)
	}
	if _, err = p.Releases(context.Background()); err != nil || len(paths) != 1 {
		t.Fatal("catalog cache", paths, err)
	}
}

type ioNopCloser struct{ *strings.Reader }

func (ioNopCloser) Close() error { return nil }

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type unavailableSystemReleases struct{}

func (unavailableSystemReleases) Releases(context.Context) ([]domain.SystemRelease, error) {
	return nil, fmt.Errorf("fixture unavailable")
}
func TestUpdateCatalogUnavailableIsExplicit(t *testing.T) {
	s := New(nil, Options{OwnerHash: auth.HashToken("owner"), SystemReleases: unavailableSystemReleases{}})
	req := httptest.NewRequest("GET", "/api/v1/system-updates/releases", nil)
	req.Header.Set("Authorization", "Bearer owner")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"system_releases_unavailable"`) {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestUpdaterOwnerOnlyAndNodeCredentialBoundaries(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	raw, _ := auth.NewToken()
	if _, err := db.CreateToken(ctx, domain.Token{Name: "publisher", Role: "publisher", Groups: []string{"default"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner"); err != nil {
		t.Fatal(err)
	}
	node, invite, _, err := db.CreateNode(ctx, domain.NodeInput{Name: "node", Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, nodeToken, err := db.EnrollNode(ctx, auth.HashToken(invite), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	s := New(db, Options{OwnerHash: auth.HashToken("owner"), SystemReleases: &fixedSystemReleases{}})
	for _, route := range []struct{ method, path string }{{"GET", "/api/v1/system-updates"}, {"GET", "/api/v1/system-updates/releases"}, {"GET", "/api/v1/system-updates/catalog"}, {"POST", "/api/v1/system-updates/catalog/sync"}, {"POST", "/api/v1/system-updates/enrollments"}, {"POST", "/api/v1/system-updates/jobs"}, {"POST", "/api/v1/system-updates/jobs/" + node.ID + "/cancel"}, {"POST", "/api/v1/system-updates/jobs/" + node.ID + "/resolve"}} {
		r := httptest.NewRequest(route.method, route.path, strings.NewReader(`{}`))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+raw)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 403 {
			t.Fatal(route, w.Code, w.Body.String())
		}
	}
	for _, token := range []string{"owner", nodeToken, raw, invite} {
		for _, path := range []string{"/api/v1/updater/heartbeat", "/api/v1/updater/poll", "/api/v1/updater/jobs/" + node.ID + "/start", "/api/v1/updater/jobs/" + node.ID + "/report"} {
			r := httptest.NewRequest("POST", path, strings.NewReader(`{}`))
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("Authorization", "Bearer "+token)
			w := httptest.NewRecorder()
			s.ServeHTTP(w, r)
			if w.Code != 401 {
				t.Fatal("updater identity boundary", path, w.Code, w.Body.String())
			}
		}
	}
}
