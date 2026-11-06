package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

const (
	officialCatalogURL = "https://github.com/art-shier/deployctl/releases/latest/download/update-index.json"
	catalogMaxBytes    = 1024 * 1024
	catalogMaxReleases = 1000
	catalogInterval    = 10 * time.Minute
	catalogRetry       = time.Minute
	catalogTimeout     = 15 * time.Second
)

type CatalogSnapshot struct {
	Releases      []domain.SystemRelease `json:"releases"`
	Source        string                 `json:"source"`
	GeneratedAt   *time.Time             `json:"generated_at"`
	LastSuccessAt *time.Time             `json:"last_success_at"`
	LastAttemptAt *time.Time             `json:"last_attempt_at"`
	NextSyncAt    *time.Time             `json:"next_sync_at"`
	Stale         bool                   `json:"stale"`
	SyncError     string                 `json:"sync_error"`
}

type releaseManifest struct {
	SchemaVersion int               `json:"schema_version"`
	GeneratedAt   string            `json:"generated_at"`
	Releases      []manifestRelease `json:"releases"`
}
type manifestRelease struct {
	Version      string `json:"version"`
	PublishedAt  string `json:"published_at"`
	CLISHA256    string `json:"cli_sha256"`
	ServerSHA256 string `json:"server_sha256"`
}

func (m *releaseManifest) UnmarshalJSON(raw []byte) error {
	type manifestValue releaseManifest
	return decodeCatalogObject(raw, []string{"schema_version", "generated_at", "releases"}, (*manifestValue)(m))
}
func (r *manifestRelease) UnmarshalJSON(raw []byte) error {
	type releaseValue manifestRelease
	return decodeCatalogObject(raw, []string{"version", "published_at", "cli_sha256", "server_sha256"}, (*releaseValue)(r))
}

// encoding/json accepts case-insensitive and duplicate struct fields by default.
// Manifests require one occurrence of each exact, documented field.
func decodeCatalogObject(raw []byte, fields []string, value any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return domain.ErrInvalid
	}
	remaining := make(map[string]bool, len(fields))
	for _, field := range fields {
		remaining[field] = true
	}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return domain.ErrInvalid
		}
		name, ok := key.(string)
		if !ok || !remaining[name] {
			return domain.ErrInvalid
		}
		delete(remaining, name)
		var field json.RawMessage
		if decoder.Decode(&field) != nil {
			return domain.ErrInvalid
		}
	}
	if len(remaining) != 0 {
		return domain.ErrInvalid
	}
	return decodeCatalogJSON(raw, value)
}

type cachedCatalog struct {
	Manifest      json.RawMessage `json:"manifest"`
	LastSuccessAt time.Time       `json:"last_success_at"`
}
type catalogFlight struct {
	done   chan struct{}
	result CatalogSnapshot
}

// SystemReleaseCatalog serves local snapshots. Only Sync and the explicitly
// started lifecycle worker contact the official static manifest endpoint.
type SystemReleaseCatalog struct {
	client    *http.Client
	cachePath string
	mu        sync.Mutex
	snapshot  CatalogSnapshot
	manifest  releaseManifest
	flight    *catalogFlight
	started   bool
	lifecycle context.Context
	wake      chan struct{}
}

func NewSystemReleaseCatalog(artifactsDir, seedPath string) *SystemReleaseCatalog {
	if seedPath == "" {
		seedPath = "/app/update-index.seed.json"
	}
	client := &http.Client{Timeout: catalogTimeout, CheckRedirect: func(r *http.Request, via []*http.Request) error {
		if len(via) > 5 || r.URL.Scheme != "https" || r.URL.User != nil || (r.URL.Host != "github.com" && r.URL.Host != "release-assets.githubusercontent.com") {
			return errors.New("release redirect rejected")
		}
		return nil
	}}
	return newSystemReleaseCatalog(artifactsDir, seedPath, client)
}

func newSystemReleaseCatalog(artifactsDir, seedPath string, client *http.Client) *SystemReleaseCatalog {
	p := &SystemReleaseCatalog{client: client, wake: make(chan struct{}, 1), snapshot: CatalogSnapshot{Releases: []domain.SystemRelease{}, Source: "none", Stale: true}}
	if artifactsDir != "" {
		p.cachePath = filepath.Join(artifactsDir, "update-index.cache.json")
	}
	if raw, err := readCatalogFile(seedPath, catalogMaxBytes); err == nil {
		if manifest, items, generated, err := parseReleaseManifest(raw); err == nil {
			p.manifest = manifest
			p.snapshot.Releases = items
			p.snapshot.GeneratedAt = &generated
			p.snapshot.Source = "bundled"
		}
	}
	if raw, err := readCatalogFile(p.cachePath, catalogMaxBytes+1024); err == nil {
		var cache cachedCatalog
		if decodeCatalogJSON(raw, &cache) == nil && !cache.LastSuccessAt.IsZero() {
			if manifest, items, generated, err := parseReleaseManifest(cache.Manifest); err == nil && (p.snapshot.GeneratedAt == nil || !generated.Before(*p.snapshot.GeneratedAt)) {
				p.manifest = manifest
				p.snapshot.Releases = items
				p.snapshot.GeneratedAt = &generated
				p.snapshot.Source = "cache"
				p.snapshot.LastSuccessAt = &cache.LastSuccessAt
				next := cache.LastSuccessAt.Add(catalogInterval)
				p.snapshot.NextSyncAt = &next
			}
		}
	}
	return p
}

func readCatalogFile(path string, limit int64) ([]byte, error) {
	if path == "" {
		return nil, os.ErrNotExist
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > limit {
		return nil, domain.ErrInvalid
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, domain.ErrInvalid
	}
	return raw, nil
}

func decodeCatalogJSON(raw []byte, value any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return domain.ErrInvalid
	}
	return nil
}
func catalogTime(value string) (time.Time, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || !strings.HasSuffix(value, "Z") || parsed.IsZero() {
		return time.Time{}, domain.ErrInvalid
	}
	return parsed, nil
}
func parseReleaseManifest(raw []byte) (releaseManifest, []domain.SystemRelease, time.Time, error) {
	var manifest releaseManifest
	invalid := func() (releaseManifest, []domain.SystemRelease, time.Time, error) {
		return releaseManifest{}, nil, time.Time{}, domain.ErrInvalid
	}
	if len(raw) > catalogMaxBytes || decodeCatalogJSON(raw, &manifest) != nil || manifest.SchemaVersion != 1 || manifest.Releases == nil || len(manifest.Releases) > catalogMaxReleases {
		return invalid()
	}
	generated, err := catalogTime(manifest.GeneratedAt)
	if err != nil {
		return invalid()
	}
	seen := map[string]bool{}
	items := []domain.SystemRelease{}
	for _, r := range manifest.Releases {
		published, err := catalogTime(r.PublishedAt)
		if err != nil || !domain.StableUpdateVersion(r.Version) || seen[r.Version] || !catalogDigest(r.CLISHA256) || !catalogDigest(r.ServerSHA256) {
			return invalid()
		}
		seen[r.Version] = true
		if domain.CompatibleUpdateVersion(r.Version) {
			items = append(items, domain.SystemRelease{Version: r.Version, PublishedAt: published, CLISHA256: r.CLISHA256, ServerSHA256: r.ServerSHA256})
		}
	}
	sort.Slice(items, func(i, j int) bool { return domain.UpdateVersionNewer(items[i].Version, items[j].Version) })
	return manifest, items, generated, nil
}
func catalogDigest(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, c := range value {
		if !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func cloneCatalog(snapshot CatalogSnapshot) CatalogSnapshot {
	snapshot.Releases = append([]domain.SystemRelease{}, snapshot.Releases...)
	copyTime := func(value *time.Time) *time.Time {
		if value == nil {
			return nil
		}
		copy := *value
		return &copy
	}
	snapshot.GeneratedAt = copyTime(snapshot.GeneratedAt)
	snapshot.LastSuccessAt = copyTime(snapshot.LastSuccessAt)
	snapshot.LastAttemptAt = copyTime(snapshot.LastAttemptAt)
	snapshot.NextSyncAt = copyTime(snapshot.NextSyncAt)
	snapshot.Stale = snapshot.Source == "bundled" || snapshot.Source == "none" || snapshot.SyncError != "" || snapshot.LastSuccessAt == nil || !time.Now().Before(snapshot.LastSuccessAt.Add(catalogInterval))
	return snapshot
}
func (p *SystemReleaseCatalog) Snapshot() CatalogSnapshot {
	p.mu.Lock()
	defer p.mu.Unlock()
	return cloneCatalog(p.snapshot)
}
func (p *SystemReleaseCatalog) Releases(context.Context) ([]domain.SystemRelease, error) {
	return p.Snapshot().Releases, nil
}

// Start attaches the background worker to the process lifetime. Construction
// remains offline so local data is immediately usable during startup.
func (p *SystemReleaseCatalog) Start(ctx context.Context) {
	p.mu.Lock()
	if p.started {
		p.mu.Unlock()
		return
	}
	p.started = true
	p.lifecycle = ctx
	p.mu.Unlock()
	go func() {
		first := true
		for ctx.Err() == nil {
			p.sync(ctx, first)
			first = false
			snapshot := p.Snapshot()
			delay := catalogRetry
			if snapshot.NextSyncAt != nil {
				delay = time.Until(*snapshot.NextSyncAt)
				if delay < 0 {
					delay = 0
				}
			}
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-p.wake:
				timer.Stop()
			case <-timer.C:
			}
		}
	}()
}
func (p *SystemReleaseCatalog) Sync(ctx context.Context) CatalogSnapshot { return p.sync(ctx, true) }
func (p *SystemReleaseCatalog) sync(ctx context.Context, force bool) CatalogSnapshot {
	p.mu.Lock()
	if ctx.Err() != nil {
		snapshot := cloneCatalog(p.snapshot)
		p.mu.Unlock()
		return snapshot
	}
	if flight := p.flight; flight != nil {
		p.mu.Unlock()
		select {
		case <-flight.done:
			return cloneCatalog(flight.result)
		case <-ctx.Done():
			return p.Snapshot()
		}
	}
	now := time.Now().UTC()
	if !force && p.snapshot.NextSyncAt != nil && now.Before(*p.snapshot.NextSyncAt) {
		snapshot := cloneCatalog(p.snapshot)
		p.mu.Unlock()
		return snapshot
	}
	flight := &catalogFlight{done: make(chan struct{})}
	p.flight = flight
	p.snapshot.LastAttemptAt = &now
	lifecycle := p.lifecycle
	p.mu.Unlock()
	requestCtx, cancel := context.WithTimeout(ctx, catalogTimeout)
	if lifecycle != nil {
		stop := context.AfterFunc(lifecycle, cancel)
		defer stop()
	}
	defer cancel()
	raw, code := p.fetch(requestCtx)
	var manifest releaseManifest
	var items []domain.SystemRelease
	var generated time.Time
	if code == "" {
		var err error
		manifest, items, generated, err = parseReleaseManifest(raw)
		if err != nil {
			code = "invalid_manifest"
		}
	}
	p.mu.Lock()
	if code == "" && !p.acceptManifest(manifest, generated) {
		code = "invalid_manifest"
	}
	success := time.Now().UTC()
	if code == "" && p.persist(raw, success) != nil {
		code = "cache_write_failed"
	}
	if code == "" {
		p.manifest = manifest
		p.snapshot.Releases = items
		p.snapshot.GeneratedAt = &generated
		p.snapshot.LastSuccessAt = &success
		p.snapshot.Source = "remote"
	}
	p.snapshot.SyncError = code
	next := success.Add(catalogInterval)
	if code != "" {
		next = success.Add(catalogRetry)
	}
	p.snapshot.NextSyncAt = &next
	flight.result = cloneCatalog(p.snapshot)
	p.flight = nil
	close(flight.done)
	snapshot := cloneCatalog(flight.result)
	p.mu.Unlock()
	select {
	case p.wake <- struct{}{}:
	default:
	}
	return snapshot
}

func (p *SystemReleaseCatalog) acceptManifest(manifest releaseManifest, generated time.Time) bool {
	if p.snapshot.GeneratedAt != nil && generated.Before(*p.snapshot.GeneratedAt) {
		return false
	}
	previous := make(map[string]manifestRelease, len(p.manifest.Releases))
	for _, r := range p.manifest.Releases {
		previous[r.Version] = r
	}
	for _, r := range manifest.Releases {
		if old, exists := previous[r.Version]; exists && (old.CLISHA256 != r.CLISHA256 || old.ServerSHA256 != r.ServerSHA256) {
			return false
		}
	}
	return true
}
func (p *SystemReleaseCatalog) fetch(ctx context.Context) ([]byte, string) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, officialCatalogURL, nil)
	if err != nil {
		return nil, "network_error"
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "deployctl-system-updates")
	response, err := p.client.Do(req)
	if err != nil {
		var timeout net.Error
		if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
			return nil, "timeout"
		}
		return nil, "network_error"
	}
	defer response.Body.Close()
	switch response.StatusCode {
	case 200:
	case 403, 429:
		return nil, "rate_limited"
	case 404:
		return nil, "not_found"
	default:
		return nil, "http_error"
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, catalogMaxBytes+1))
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, "timeout"
		}
		return nil, "network_error"
	}
	if len(raw) > catalogMaxBytes {
		return nil, "invalid_manifest"
	}
	return raw, ""
}
func (p *SystemReleaseCatalog) persist(raw []byte, success time.Time) error {
	if p.cachePath == "" {
		return errors.New("artifact directory required for catalog cache")
	}
	data, err := json.Marshal(cachedCatalog{Manifest: raw, LastSuccessAt: success})
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(p.cachePath), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(p.cachePath), ".update-index-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), p.cachePath)
}

type systemCatalogProvider interface {
	Snapshot() CatalogSnapshot
	Sync(context.Context) CatalogSnapshot
}

func (s *Server) releaseCatalog(ctx context.Context, force bool) CatalogSnapshot {
	if provider, ok := s.options.SystemReleases.(systemCatalogProvider); ok {
		if force {
			return provider.Sync(ctx)
		}
		return provider.Snapshot()
	}
	// Existing injected providers keep working without acquiring new methods.
	releases, err := s.options.SystemReleases.Releases(ctx)
	snapshot := CatalogSnapshot{Releases: append([]domain.SystemRelease{}, releases...), Source: "none", Stale: true}
	if err != nil {
		snapshot.SyncError = "network_error"
	}
	return snapshot
}
func (s *Server) systemUpdateCatalog(w http.ResponseWriter, r *http.Request, principal auth.Principal) error {
	if err := owner(principal); err != nil {
		return err
	}
	reply(w, http.StatusOK, s.releaseCatalog(r.Context(), false))
	return nil
}
func (s *Server) syncSystemUpdateCatalog(w http.ResponseWriter, r *http.Request, principal auth.Principal) error {
	if err := owner(principal); err != nil {
		return err
	}
	var input struct{}
	if err := decode(w, r, &input); err != nil {
		return err
	}
	reply(w, http.StatusOK, s.releaseCatalog(r.Context(), true))
	return nil
}
