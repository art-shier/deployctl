package httpapi

import (
	"errors"
	"net/http"
	"slices"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Server) projects(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	projects, err := s.store.ListProjects(r.Context())
	if err != nil {
		return err
	}
	out := []domain.Project{}
	for _, item := range projects {
		if p.Can("project.read", item.Slug, "") {
			out = append(out, item)
		}
	}
	reply(w, 200, out)
	return nil
}
func (s *Server) createProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var project domain.Project
	if err := decode(w, r, &project); err != nil {
		return err
	}
	if project.ImageRepository == "" {
		project.ImageRepository = s.options.RegistryPublicHost + "/" + project.Slug
	}
	result, err := s.store.CreateProject(r.Context(), project, p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, result)
	return nil
}
func (s *Server) getProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "project.read", slug, ""); err != nil {
		return err
	}
	project, err := s.store.GetProject(r.Context(), slug)
	if err != nil {
		return err
	}
	reply(w, 200, project)
	return nil
}
func (s *Server) updateProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var project domain.Project
	if err := decode(w, r, &project); err != nil {
		return err
	}
	if project.Slug != r.PathValue("slug") {
		return domain.ErrInvalid
	}
	result, err := s.store.UpdateProject(r.Context(), project, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, result)
	return nil
}
func (s *Server) environments(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.store.ListEnvironments(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	reply(w, 200, items)
	return nil
}

type maskedVariable struct {
	Key        string  `json:"key"`
	Secret     bool    `json:"secret"`
	Configured bool    `json:"configured"`
	Value      *string `json:"value,omitempty"`
}

func masked(vars map[string]domain.Variable) []maskedVariable {
	keys := []string{}
	for key := range vars {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	out := []maskedVariable{}
	for _, key := range keys {
		v := vars[key]
		item := maskedVariable{Key: key, Secret: v.Secret, Configured: true}
		if !v.Secret {
			value := v.Value
			item.Value = &value
		}
		out = append(out, item)
	}
	return out
}
func environmentReply(w http.ResponseWriter, rev domain.Revision) {
	reply(w, 200, map[string]any{"id": rev.ID, "environment": rev.Environment, "revision": rev.Revision, "target_version": rev.TargetVersion, "runtime_env": masked(rev.Configuration.RuntimeEnv), "install_params": masked(rev.Configuration.InstallParams), "deployment_defaults": rev.Configuration.DeploymentDefaults, "created_at": rev.CreatedAt, "inherited_runtime_env": masked(rev.InheritedConfiguration.RuntimeEnv), "inherited_install_params": masked(rev.InheritedConfiguration.InstallParams), "group_source": rev.GroupSource})
}
func (s *Server) environment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	rev, err := s.store.GetRevision(r.Context(), r.PathValue("slug"), r.PathValue("env"))
	if err != nil {
		return err
	}
	environmentReply(w, rev)
	return nil
}
func (s *Server) saveEnvironment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var body struct {
		ExpectedRevision   int64                      `json:"expected_revision"`
		RuntimeEnv         []domain.Change            `json:"runtime_env"`
		InstallParams      []domain.Change            `json:"install_params"`
		DeploymentDefaults *domain.DeploymentDefaults `json:"deployment_defaults"`
		TargetVersion      *string                    `json:"target_version"`
	}
	if err := decode(w, r, &body); err != nil {
		return err
	}
	slug, env := r.PathValue("slug"), r.PathValue("env")
	if _, err := s.store.GetProject(r.Context(), slug); err != nil {
		return err
	}
	previous, err := s.store.GetRevision(r.Context(), slug, env)
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return err
	}
	if errors.Is(err, domain.ErrNotFound) {
		previous = domain.Revision{TargetVersion: "stable"}
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
	if body.DeploymentDefaults != nil {
		cfg.DeploymentDefaults = *body.DeploymentDefaults
	}
	target := previous.TargetVersion
	if body.TargetVersion != nil {
		target = *body.TargetVersion
	}
	_, err = s.store.SaveRevision(r.Context(), slug, env, body.ExpectedRevision, cfg, target, p.ID)
	if err != nil {
		return err
	}
	next, err := s.store.GetRevision(r.Context(), slug, env)
	if err != nil {
		return err
	}
	environmentReply(w, next)
	return nil
}
