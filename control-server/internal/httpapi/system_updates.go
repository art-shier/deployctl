package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
)

var errSystemReleasesUnavailable = errors.New("system releases unavailable")

type SystemReleaseProvider interface {
	Releases(context.Context) ([]domain.SystemRelease, error)
}

type githubSystemReleases struct {
	client *http.Client
	mu     sync.Mutex
	cached []domain.SystemRelease
	until  time.Time
}

func officialSystemReleases() SystemReleaseProvider {
	return &githubSystemReleases{client: &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(r *http.Request, via []*http.Request) error {
		if len(via) > 5 || r.URL.Scheme != "https" || (r.URL.Host != "github.com" && r.URL.Host != "release-assets.githubusercontent.com") {
			return errors.New("release redirect rejected")
		}
		return nil
	}}}
}
func (p *githubSystemReleases) get(ctx context.Context, url string, limit int64) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "deployctl-system-updates")
	res, err := p.client.Do(req)
	if err != nil {
		return nil, errors.New("official release catalog unavailable")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("official release catalog returned HTTP %d", res.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(res.Body, limit+1))
	if err != nil || int64(len(b)) > limit {
		return nil, errors.New("official release response exceeds limit")
	}
	return b, nil
}
func (p *githubSystemReleases) Releases(ctx context.Context) ([]domain.SystemRelease, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if time.Now().Before(p.until) {
		return append([]domain.SystemRelease{}, p.cached...), nil
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	b, err := p.get(ctx, "https://api.github.com/repos/art-shier/deployctl/releases?per_page=100", 2*1024*1024)
	if err != nil {
		return nil, err
	}
	var releases []struct {
		Tag        string    `json:"tag_name"`
		Published  time.Time `json:"published_at"`
		Draft      bool      `json:"draft"`
		Prerelease bool      `json:"prerelease"`
	}
	if json.Unmarshal(b, &releases) != nil {
		return nil, errors.New("invalid official release catalog")
	}
	out := []domain.SystemRelease{}
	for _, r := range releases {
		if r.Draft || r.Prerelease || !domain.CompatibleUpdateVersion(r.Tag) {
			continue
		}
		if len(out) == 10 {
			break
		}
		sums, e := p.get(ctx, "https://github.com/art-shier/deployctl/releases/download/"+r.Tag+"/SHA256SUMS", 64*1024)
		if e != nil {
			return nil, e
		}
		assets := map[string]string{}
		for _, line := range strings.Split(string(sums), "\n") {
			fields := strings.Fields(line)
			if len(fields) != 2 {
				continue
			}
			name := strings.TrimPrefix(fields[1], "*")
			if _, exists := assets[name]; exists {
				return nil, errors.New("duplicate official release checksum")
			}
			assets[name] = fields[0]
		}
		item := domain.SystemRelease{Version: r.Tag, PublishedAt: r.Published, CLISHA256: assets["deployctl.pyz"], ServerSHA256: assets["ctl-platform-"+r.Tag+".tar.gz"]}
		if item.Validate() != nil {
			return nil, errors.New("official release checksums missing or invalid")
		}
		out = append(out, item)
	}
	p.cached = out
	p.until = time.Now().Add(30 * time.Second)
	return append([]domain.SystemRelease{}, out...), nil
}
func (s *Server) systemUpdateRoutes() {
	m := s.mux
	m.HandleFunc("GET /api/v1/system-updates/releases", s.wrap(s.systemUpdateReleases))
	m.HandleFunc("GET /api/v1/system-updates", s.wrap(s.systemUpdates))
	m.HandleFunc("POST /api/v1/system-updates/enrollments", s.wrap(s.createUpdateEnrollment))
	m.HandleFunc("POST /api/v1/system-updates/jobs", s.wrap(s.createUpdateJobs))
	m.HandleFunc("POST /api/v1/system-updates/jobs/{id}/cancel", s.wrap(s.cancelUpdateJob))
	m.HandleFunc("POST /api/v1/system-updates/jobs/{id}/resolve", s.wrap(s.resolveUpdateJob))
	m.HandleFunc("POST /api/v1/updater/enroll", func(w http.ResponseWriter, r *http.Request) {
		if err := s.enrollUpdater(w, r); err != nil {
			failure(w, err)
		}
	})
	m.HandleFunc("POST /api/v1/updater/heartbeat", s.updaterWrap(s.heartbeatUpdater))
	m.HandleFunc("POST /api/v1/updater/poll", s.updaterWrap(s.pollUpdater))
	m.HandleFunc("POST /api/v1/updater/jobs/{id}/start", s.updaterWrap(s.startUpdaterJob))
	m.HandleFunc("POST /api/v1/updater/jobs/{id}/report", s.updaterWrap(s.reportUpdaterJob))
}
func (s *Server) systemUpdateReleases(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.options.SystemReleases.Releases(r.Context())
	if err != nil {
		return errSystemReleasesUnavailable
	}
	reply(w, 200, items)
	return nil
}
func (s *Server) systemUpdates(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	updaters, jobs, err := s.store.ListSystemUpdates(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"updaters": updaters, "jobs": jobs})
	return nil
}
func (s *Server) createUpdateEnrollment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in domain.UpdateEnrollment
	if err := decode(w, r, &in); err != nil {
		return err
	}
	token, expiry, err := s.store.CreateUpdateEnrollment(r.Context(), in, p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"enrollment_token": token, "expires_at": expiry})
	return nil
}
func (s *Server) createUpdateJobs(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in domain.UpdateRequest
	if err := decode(w, r, &in); err != nil {
		return err
	}
	jobs, err := s.store.CreateUpdateJobs(r.Context(), in, p.ID, func(ctx context.Context, version string) (domain.SystemRelease, error) {
		releases, err := s.options.SystemReleases.Releases(ctx)
		if err != nil {
			return domain.SystemRelease{}, errSystemReleasesUnavailable
		}
		for _, release := range releases {
			if release.Version == version {
				return release, nil
			}
		}
		return domain.SystemRelease{}, domain.ErrInvalid
	})
	if err != nil {
		return err
	}
	reply(w, 201, jobs)
	return nil
}
func (s *Server) cancelUpdateJob(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	job, err := s.store.CancelUpdateJob(r.Context(), r.PathValue("id"), p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, job)
	return nil
}
func (s *Server) enrollUpdater(w http.ResponseWriter, r *http.Request) error {
	token, err := nodeBearer(r)
	if err != nil {
		return err
	}
	var in struct {
		HelperVersion string `json:"helper_version"`
		Platform      string `json:"platform"`
		Protocol      int    `json:"protocol"`
	}
	if err = decode(w, r, &in); err != nil {
		return err
	}
	u, raw, err := s.store.EnrollUpdater(r.Context(), auth.HashToken(token), in.HelperVersion, in.Platform, in.Protocol)
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"updater_id": u.ID, "token": raw, "kind": u.Kind, "target_id": u.TargetID})
	return nil
}
func (s *Server) updaterWrap(h func(http.ResponseWriter, *http.Request, string) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token, err := nodeBearer(r)
		if err != nil {
			failure(w, err)
			return
		}
		hash := auth.HashToken(token)
		id, err := s.store.AuthenticateUpdater(r.Context(), hash)
		if err != nil {
			failure(w, err)
			return
		}
		r = r.WithContext(store.WithUpdaterIdentity(r.Context(), hash))
		if err = h(w, r, id); err != nil {
			failure(w, err)
		}
	}
}
func (s *Server) heartbeatUpdater(w http.ResponseWriter, r *http.Request, id string) error {
	var in domain.UpdaterHeartbeat
	if err := decode(w, r, &in); err != nil {
		return err
	}
	if err := s.store.HeartbeatUpdater(r.Context(), id, in); err != nil {
		return err
	}
	reply(w, 200, map[string]bool{"enabled": true})
	return nil
}
func (s *Server) pollUpdater(w http.ResponseWriter, r *http.Request, id string) error {
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	job, err := s.store.PollUpdater(r.Context(), id)
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"job": job})
	return nil
}
func (s *Server) startUpdaterJob(w http.ResponseWriter, r *http.Request, id string) error {
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	job, err := s.store.StartUpdateJob(r.Context(), id, r.PathValue("id"))
	if err != nil {
		return err
	}
	reply(w, 200, job)
	return nil
}
func (s *Server) reportUpdaterJob(w http.ResponseWriter, r *http.Request, id string) error {
	var in domain.UpdateReport
	if err := decode(w, r, &in); err != nil {
		return err
	}
	job, err := s.store.ReportUpdateJob(r.Context(), id, r.PathValue("id"), in)
	if err != nil {
		return err
	}
	reply(w, 200, job)
	return nil
}
func (s *Server) resolveUpdateJob(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	job, err := s.store.ResolveUpdateJob(r.Context(), r.PathValue("id"), p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, job)
	return nil
}
