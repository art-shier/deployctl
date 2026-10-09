package httpapi

import (
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
		if p.CanInGroup("project.read", item.Slug, item.Group, "") {
			out = append(out, item)
		}
	}
	reply(w, 200, out)
	return nil
}
func (s *Server) createProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if p.Role != "owner" && p.Role != "publisher" {
		return errForbidden
	}
	var project domain.Project
	if err := decode(w, r, &project); err != nil {
		return err
	}
	if project.ImageRepository == "" {
		project.ImageRepository = s.options.RegistryPublicHost + "/" + project.Slug
	}
	if p.Role == "publisher" && project.ImageRepository != s.options.RegistryPublicHost+"/"+project.Slug {
		return errForbidden
	}
	result, err := s.store.CreateProjectAuthorized(r.Context(), project, p)
	if err != nil {
		return err
	}
	reply(w, 201, result)
	return nil
}
func (s *Server) getProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	project, err := s.store.GetProjectAuthorized(r.Context(), slug, p)
	if err != nil {
		return err
	}
	reply(w, 200, project)
	return nil
}
func (s *Server) updateProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if p.Role != "owner" && p.Role != "publisher" {
		return errForbidden
	}
	var project domain.Project
	if err := decode(w, r, &project); err != nil {
		return err
	}
	if project.Slug != r.PathValue("slug") {
		return domain.ErrInvalid
	}
	result, err := s.store.UpdateProjectAuthorized(r.Context(), project, p)
	if err != nil {
		return err
	}
	reply(w, 200, result)
	return nil
}
func (s *Server) environments(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	items, err := s.store.ListEnvironmentsAuthorized(r.Context(), r.PathValue("slug"), p)
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

func masked(vars map[string]domain.Variable, reveal ...bool) []maskedVariable {
	keys := []string{}
	for key := range vars {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	out := []maskedVariable{}
	for _, key := range keys {
		v := vars[key]
		item := maskedVariable{Key: key, Secret: v.Secret, Configured: true}
		if !v.Secret || (len(reveal) > 0 && reveal[0]) {
			value := v.Value
			item.Value = &value
		}
		out = append(out, item)
	}
	return out
}
func environmentReply(w http.ResponseWriter, rev domain.Revision, reveal ...bool) {
	reply(w, 200, map[string]any{"id": rev.ID, "environment": rev.Environment, "revision": rev.Revision, "target_version": rev.TargetVersion, "runtime_env": masked(rev.Configuration.RuntimeEnv, reveal...), "install_params": masked(rev.Configuration.InstallParams, reveal...), "deployment_defaults": rev.Configuration.DeploymentDefaults, "created_at": rev.CreatedAt, "inherited_runtime_env": masked(rev.InheritedConfiguration.RuntimeEnv, reveal...), "inherited_install_params": masked(rev.InheritedConfiguration.InstallParams, reveal...), "group_source": rev.GroupSource})
}
func (s *Server) environment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	reveal := r.URL.Query().Get("reveal") == "true"
	rev, err := s.store.GetRevisionAuthorized(r.Context(), r.PathValue("slug"), r.PathValue("env"), p, reveal)
	if err != nil {
		return err
	}
	environmentReply(w, rev, reveal)
	return nil
}
func (s *Server) saveEnvironment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if p.Role != "owner" && p.Role != "publisher" {
		return errForbidden
	}
	var body domain.ConfigurationPatch
	if err := decode(w, r, &body); err != nil {
		return err
	}
	next, err := s.store.SaveConfigurationAuthorized(r.Context(), r.PathValue("slug"), r.PathValue("env"), body, p)
	if err != nil {
		return err
	}
	environmentReply(w, next)
	return nil
}
func (s *Server) deleteProject(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	if err := s.store.DeleteProject(r.Context(), r.PathValue("slug"), p.ID); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}
