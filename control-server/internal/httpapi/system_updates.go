package httpapi

import (
	"context"
	"errors"
	"net/http"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
)

var errSystemReleasesUnavailable = errors.New("system releases unavailable")

type SystemReleaseProvider interface {
	Releases(context.Context) ([]domain.SystemRelease, error)
}

func (s *Server) systemUpdateRoutes() {
	m := s.mux
	m.HandleFunc("GET /api/v1/system-updates/releases", s.wrap(s.systemUpdateReleases))
	m.HandleFunc("GET /api/v1/system-updates/catalog", s.wrap(s.systemUpdateCatalog))
	m.HandleFunc("POST /api/v1/system-updates/catalog/sync", s.wrap(s.syncSystemUpdateCatalog))
	m.HandleFunc("GET /api/v1/system-updates", s.wrap(s.systemUpdates))
	m.HandleFunc("POST /api/v1/system-updates/enrollments", s.wrap(s.createUpdateEnrollment))
	m.HandleFunc("POST /api/v1/node/updater-enrollment", s.nodeWrap(s.createNodeUpdateEnrollment))
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
func (s *Server) createNodeUpdateEnrollment(w http.ResponseWriter, r *http.Request, node string) error {
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	// nodeWrap binds the credential to the node lock taken by enrollment.
	token, expiry, err := s.store.CreateUpdateEnrollment(r.Context(), domain.UpdateEnrollment{Kind: "node", TargetID: node}, node)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"enrollment_token": token, "expires_at": expiry})
	return nil
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
