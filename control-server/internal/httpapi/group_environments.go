package httpapi

import (
	"errors"
	"net/http"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Server) groupEnvironments(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.store.ListGroupEnvironments(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	reply(w, http.StatusOK, items)
	return nil
}

func groupEnvironmentReply(w http.ResponseWriter, rev domain.GroupRevision) {
	reply(w, http.StatusOK, map[string]any{"id": rev.ID, "environment": rev.Environment, "revision": rev.Revision, "runtime_env": masked(rev.Configuration.RuntimeEnv), "install_params": masked(rev.Configuration.InstallParams), "created_at": rev.CreatedAt})
}

func (s *Server) groupEnvironment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	rev, err := s.store.GetGroupRevision(r.Context(), r.PathValue("slug"), r.PathValue("env"))
	if err != nil {
		return err
	}
	groupEnvironmentReply(w, rev)
	return nil
}

func (s *Server) saveGroupEnvironment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var body struct {
		ExpectedRevision int64           `json:"expected_revision"`
		RuntimeEnv       []domain.Change `json:"runtime_env"`
		InstallParams    []domain.Change `json:"install_params"`
	}
	if err := decode(w, r, &body); err != nil {
		return err
	}
	slug, env := r.PathValue("slug"), r.PathValue("env")
	if _, err := s.store.GetGroup(r.Context(), slug); err != nil {
		return err
	}
	previous, err := s.store.GetGroupRevision(r.Context(), slug, env)
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return err
	}
	if previous.Revision != body.ExpectedRevision {
		return domain.ErrConflict
	}
	cfg := previous.Configuration
	cfg.RuntimeEnv, err = domain.ApplyChanges(cfg.RuntimeEnv, body.RuntimeEnv, true)
	if err != nil {
		return err
	}
	cfg.InstallParams, err = domain.ApplyChanges(cfg.InstallParams, body.InstallParams, false)
	if err != nil {
		return err
	}
	next, err := s.store.SaveGroupRevision(r.Context(), slug, env, body.ExpectedRevision, cfg, p.ID)
	if err != nil {
		return err
	}
	groupEnvironmentReply(w, next)
	return nil
}
