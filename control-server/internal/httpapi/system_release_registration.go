package httpapi

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type RegisteredSystemCatalog struct {
	Store        *store.Store
	ArtifactsDir string
	mu           sync.Mutex
	client       *http.Client
}

func NewRegisteredSystemCatalog(db *store.Store, dir string) *RegisteredSystemCatalog {
	return &RegisteredSystemCatalog{Store: db, ArtifactsDir: dir, client: &http.Client{Timeout: 60 * time.Second, CheckRedirect: func(r *http.Request, via []*http.Request) error {
		if len(via) > 5 || !domain.ValidUpdateURL(r.URL.String()) {
			return errors.New("invalid update download redirect")
		}
		return nil
	}}}
}
func (p *RegisteredSystemCatalog) Releases(ctx context.Context) ([]domain.SystemRelease, error) {
	return p.Store.RegisteredSystemReleases(ctx)
}
func (p *RegisteredSystemCatalog) Catalog(ctx context.Context) CatalogSnapshot {
	releases, err := p.Releases(ctx)
	snapshot := CatalogSnapshot{Source: "registered", Releases: releases}
	if err != nil {
		snapshot.Releases = []domain.SystemRelease{}
		snapshot.SyncError = "database_error"
		snapshot.Stale = true
	}
	return snapshot
}
func (p *RegisteredSystemCatalog) Artifact(ctx context.Context, job domain.UpdateJob) ([]byte, error) {
	cli, server := domain.OfficialUpdateURLs(job.Version)
	if job.CLIURL != "" {
		cli = job.CLIURL
	}
	if job.ServerURL != "" {
		server = job.ServerURL
	}
	address, digest, limit := cli, job.CLISHA256, int64(10*1024*1024)
	if job.Kind == "center" {
		address, digest, limit = server, job.ServerSHA256, 512*1024
	}
	if !domain.StableUpdateVersion(job.Version) || !domain.ValidUpdateURL(address) || !catalogDigest(digest) || (job.Kind != "node" && job.Kind != "center") {
		return nil, domain.ErrInvalid
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	valid := func(raw []byte) bool {
		return int64(len(raw)) <= limit && fmt.Sprintf("%x", sha256.Sum256(raw)) == digest
	}
	cache := ""
	if p.ArtifactsDir != "" {
		dir := filepath.Join(p.ArtifactsDir, "system-update-packages")
		if err := os.MkdirAll(dir, 0700); err != nil {
			return nil, err
		}
		cache = filepath.Join(dir, digest)
		if raw, err := readCatalogFile(cache, limit); err == nil && valid(raw) {
			return raw, nil
		}
	}
	request, err := http.NewRequestWithContext(ctx, "GET", address, nil)
	if err != nil {
		return nil, err
	}
	response, err := p.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, errSystemReleasesUnavailable
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if !valid(raw) {
		return nil, domain.ErrInvalid
	}
	if cache != "" {
		file, err := os.CreateTemp(filepath.Dir(cache), ".download-")
		if err != nil {
			return nil, err
		}
		defer os.Remove(file.Name())
		if _, err = file.Write(raw); err != nil {
			file.Close()
			return nil, err
		}
		if err = file.Close(); err != nil {
			return nil, err
		}
		if err = os.Rename(file.Name(), cache); err != nil {
			return nil, err
		}
	}
	return raw, nil
}
func (s *Server) releaseRegistrationRoutes() {
	s.mux.HandleFunc("POST /api/v1/system-updates/releases", s.wrap(s.manualRegisterRelease))
	s.mux.HandleFunc("GET /api/v1/system-updates/registration-token", s.wrap(s.registrationTokenStatus))
	s.mux.HandleFunc("POST /api/v1/system-updates/registration-token", s.wrap(s.rotateRegistrationToken))
	s.mux.HandleFunc("DELETE /api/v1/system-updates/registration-token", s.wrap(s.revokeRegistrationToken))
	s.mux.HandleFunc("POST /api/v1/system-updates/registrations", func(w http.ResponseWriter, r *http.Request) {
		token, err := nodeBearer(r)
		if err != nil {
			failure(w, err)
			return
		}
		if err = s.registerRelease(w, r, "release-pipeline", "pipeline", auth.HashToken(token)); err != nil {
			failure(w, err)
		}
	})
}
func (s *Server) registerRelease(w http.ResponseWriter, r *http.Request, actor, source, hash string) error {
	// Do not accept client-supplied provenance or registration timestamps.
	var in struct {
		Version      string    `json:"version"`
		PublishedAt  time.Time `json:"published_at"`
		CLIURL       string    `json:"cli_url"`
		ServerURL    string    `json:"server_url"`
		CLISHA256    string    `json:"cli_sha256"`
		ServerSHA256 string    `json:"server_sha256"`
	}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	release, created, err := s.store.RegisterSystemRelease(r.Context(), domain.SystemRelease{Version: in.Version, PublishedAt: in.PublishedAt, CLIURL: in.CLIURL, ServerURL: in.ServerURL, CLISHA256: in.CLISHA256, ServerSHA256: in.ServerSHA256}, actor, source, hash)
	if err != nil {
		return err
	}
	status := 200
	if created {
		status = 201
	}
	reply(w, status, release)
	return nil
}
func (s *Server) manualRegisterRelease(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	return s.registerRelease(w, r, p.ID, "manual", "")
}
func (s *Server) registrationTokenStatus(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	created, err := s.store.ReleaseRegistrationCredential(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"configured": created != nil, "created_at": created})
	return nil
}
func (s *Server) rotateRegistrationToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	token, err := s.store.RotateReleaseRegistrationToken(r.Context(), p.ID)
	if err != nil {
		return err
	}
	w.Header().Set("Cache-Control", "no-store")
	reply(w, 201, map[string]string{"token": token})
	return nil
}
func (s *Server) revokeRegistrationToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	if err := s.store.RevokeReleaseRegistrationToken(r.Context(), p.ID); err != nil {
		return err
	}
	w.WriteHeader(204)
	return nil
}
