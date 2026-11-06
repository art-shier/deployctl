package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
)

func (s *Server) remoteRoutes() {
	m := s.mux
	m.HandleFunc("GET /api/v1/nodes", s.wrap(s.nodes))
	m.HandleFunc("POST /api/v1/nodes", s.wrap(s.createNode))
	m.HandleFunc("POST /api/v1/node-enrollments", s.wrap(s.createNodeEnrollment))
	m.HandleFunc("PATCH /api/v1/nodes/{id}", s.wrap(s.updateNode))
	m.HandleFunc("POST /api/v1/nodes/{id}/enrollment", s.wrap(s.rotateNodeEnrollment))
	m.HandleFunc("GET /api/v1/projects/{slug}/nodes", s.wrap(s.projectNodes))
	m.HandleFunc("GET /api/v1/projects/{slug}/instances", s.wrap(s.projectInstances))
	m.HandleFunc("GET /api/v1/projects/{slug}/tasks", s.wrap(s.projectTasks))
	m.HandleFunc("POST /api/v1/projects/{slug}/tasks", s.wrap(s.createRemoteTask))
	m.HandleFunc("POST /api/v1/projects/{slug}/tasks/{id}/cancel", s.wrap(s.cancelRemoteTask))
	m.HandleFunc("POST /api/v1/node/enroll", func(w http.ResponseWriter, r *http.Request) {
		if err := s.enrollNode(w, r); err != nil {
			failure(w, err)
		}
	})
	m.HandleFunc("POST /api/v1/node/heartbeat", s.nodeWrap(s.heartbeatNode))
	m.HandleFunc("POST /api/v1/node/poll", s.nodeWrap(s.pollNode))
	m.HandleFunc("POST /api/v1/node/tasks/{id}/start", s.nodeWrap(s.startNodeTask))
	m.HandleFunc("POST /api/v1/node/tasks/{id}/report", s.nodeWrap(s.reportNodeTask))
	m.HandleFunc("GET /api/v1/node/tasks/{id}/package", s.nodeWrap(s.nodeTaskPackage))
	m.HandleFunc("GET /api/v1/node/tasks/{id}/registry", s.nodeWrap(s.nodeTaskRegistry))
}
func (s *Server) nodes(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	nodes, err := s.store.ListNodes(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, nodes)
	return nil
}
func (s *Server) createNodeEnrollment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	token, expires, err := s.store.CreateNodeEnrollment(r.Context(), p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"enrollment_token": token, "expires_at": expires})
	return nil
}
func (s *Server) createNode(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in domain.NodeInput
	if err := decode(w, r, &in); err != nil {
		return err
	}
	in.Enabled = true
	n, token, expires, err := s.store.CreateNode(r.Context(), in, p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"node": n, "enrollment_token": token, "expires_at": expires})
	return nil
}
func (s *Server) updateNode(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in domain.NodeInput
	if err := decode(w, r, &in); err != nil {
		return err
	}
	n, err := s.store.UpdateNode(r.Context(), r.PathValue("id"), in, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, n)
	return nil
}
func (s *Server) rotateNodeEnrollment(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	token, expires, err := s.store.RotateNodeEnrollment(r.Context(), r.PathValue("id"), p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"enrollment_token": token, "expires_at": expires})
	return nil
}
func (s *Server) projectNodes(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	env := r.URL.Query().Get("environment")
	if domain.ValidateName(env, 32) != nil {
		return domain.ErrInvalid
	}
	if !p.CanInGroup("resolve", project.Slug, project.Group, env) {
		return domain.ErrForbidden
	}
	nodes, err := s.store.ListNodes(r.Context())
	if err != nil {
		return err
	}
	out := []domain.Node{}
	for _, n := range nodes {
		if n.Allows(project, env) {
			out = append(out, n)
		}
	}
	reply(w, 200, out)
	return nil
}
func (s *Server) projectInstances(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	if !p.CanInGroup("project.read", project.Slug, project.Group, "") {
		return domain.ErrForbidden
	}
	list, err := s.store.ListInstances(r.Context(), project.Slug)
	if err != nil {
		return err
	}
	out := []domain.Instance{}
	for _, i := range list {
		if p.CanInGroup("configuration.read", project.Slug, project.Group, i.Environment) {
			out = append(out, i)
		}
	}
	reply(w, 200, out)
	return nil
}
func (s *Server) projectTasks(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	if !p.CanInGroup("project.read", project.Slug, project.Group, "") {
		return domain.ErrForbidden
	}
	list, err := s.store.ListTasks(r.Context(), project.Slug)
	if err != nil {
		return err
	}
	out := []domain.Task{}
	for _, t := range list {
		if p.CanInGroup("resolve", project.Slug, project.Group, t.Environment) {
			out = append(out, t)
		}
	}
	reply(w, 200, out)
	return nil
}
func (s *Server) createRemoteTask(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	var in domain.TaskRequest
	if err := decode(w, r, &in); err != nil {
		return err
	}
	task, err := s.store.CreateTask(r.Context(), r.PathValue("slug"), in, p)
	if err != nil {
		return err
	}
	reply(w, 201, task)
	return nil
}
func (s *Server) cancelRemoteTask(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	task, err := s.store.CancelTask(r.Context(), r.PathValue("slug"), r.PathValue("id"), p)
	if err != nil {
		return err
	}
	reply(w, 200, task)
	return nil
}
func nodeBearer(r *http.Request) (string, error) {
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || token == "" || len(token) > 1024 {
		return "", domain.ErrUnauthorized
	}
	return token, nil
}
func (s *Server) enrollNode(w http.ResponseWriter, r *http.Request) error {
	token, err := nodeBearer(r)
	if err != nil {
		return err
	}
	var in struct {
		AgentVersion string `json:"agent_version"`
		Platform     string `json:"platform"`
		Name         string `json:"name"`
	}
	if err = decode(w, r, &in); err != nil {
		return err
	}
	id, raw, err := s.store.EnrollNodeNamed(r.Context(), auth.HashToken(token), in.AgentVersion, in.Platform, in.Name)
	if err != nil {
		return err
	}
	reply(w, 200, map[string]string{"node_id": id, "token": raw})
	return nil
}
func (s *Server) nodeWrap(h func(http.ResponseWriter, *http.Request, string) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token, err := nodeBearer(r)
		if err != nil {
			failure(w, err)
			return
		}
		hash := auth.HashToken(token)
		id, err := s.store.AuthenticateNode(r.Context(), hash)
		if err != nil {
			failure(w, err)
			return
		}
		r = r.WithContext(store.WithNodeIdentity(r.Context(), hash))
		if err = h(w, r, id); err != nil {
			failure(w, err)
		}
	}
}
func (s *Server) heartbeatNode(w http.ResponseWriter, r *http.Request, node string) error {
	var in struct {
		AgentVersion    string               `json:"agent_version"`
		Platform        string               `json:"platform"`
		DockerAvailable bool                 `json:"docker_available"`
		Instances       []domain.Observation `json:"instances"`
	}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	enabled, instances, err := s.store.HeartbeatNode(r.Context(), node, in.AgentVersion, in.Platform, in.DockerAvailable, in.Instances)
	if err != nil {
		return err
	}
	out := []map[string]string{}
	for _, i := range instances {
		out = append(out, map[string]string{"id": i.ID, "project": i.Project, "environment": i.Environment})
	}
	reply(w, 200, map[string]any{"enabled": enabled, "instances": out})
	return nil
}
func (s *Server) pollNode(w http.ResponseWriter, r *http.Request, node string) error {
	var in struct {
		WaitSeconds int `json:"wait_seconds"`
	}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	if in.WaitSeconds < 0 || in.WaitSeconds > 25 {
		return domain.ErrInvalid
	}
	deadline := time.Now().Add(time.Duration(in.WaitSeconds) * time.Second)
	for {
		task, err := s.store.PollNode(r.Context(), node)
		if err != nil {
			return err
		}
		if task != nil || !time.Now().Before(deadline) {
			reply(w, 200, map[string]any{"task": task})
			return nil
		}
		wait := time.Until(deadline)
		if wait > time.Second {
			wait = time.Second
		}
		timer := time.NewTimer(wait)
		select {
		case <-r.Context().Done():
			timer.Stop()
			return r.Context().Err()
		case <-timer.C:
		}
	}
}
func (s *Server) startNodeTask(w http.ResponseWriter, r *http.Request, node string) error {
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	task, err := s.store.StartNodeTask(r.Context(), node, r.PathValue("id"))
	if err != nil {
		return err
	}
	reply(w, 200, task)
	return nil
}
func (s *Server) reportNodeTask(w http.ResponseWriter, r *http.Request, node string) error {
	var in domain.TaskReport
	if err := decode(w, r, &in); err != nil {
		return err
	}
	task, err := s.store.ReportNodeTask(r.Context(), node, r.PathValue("id"), in)
	if err != nil {
		return err
	}
	reply(w, 200, task)
	return nil
}
func (s *Server) nodeTaskPackage(w http.ResponseWriter, r *http.Request, node string) error {
	release, err := s.store.NodeTaskMaterial(r.Context(), node, r.PathValue("id"))
	if err != nil {
		return err
	}
	path := filepath.Join(s.options.ArtifactsDir, release.SHA256+".tar.gz")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return domain.ErrNotFound
	}
	w.Header().Set("Content-Type", "application/gzip")
	w.Header().Set("ETag", `"`+release.SHA256+`"`)
	http.ServeFile(w, r, path)
	return nil
}
func (s *Server) nodeTaskRegistry(w http.ResponseWriter, r *http.Request, node string) error {
	release, err := s.store.NodeTaskMaterial(r.Context(), node, r.PathValue("id"))
	if err != nil {
		return err
	}
	repository := strings.Split(release.Image, "@")[0]
	host, path, ok := strings.Cut(repository, "/")
	if !ok {
		return domain.ErrInvalid
	}
	token := ""
	if host == s.options.RegistryPublicHost {
		if s.options.Signer == nil {
			return domain.ErrInvalid
		}
		token, err = s.options.Signer.Sign(auth.Principal{ID: "node-task:" + r.PathValue("id")}, []auth.Access{{Type: "repository", Name: path, Actions: []string{"pull"}}}, time.Now())
		if err != nil {
			return err
		}
	}
	reply(w, 200, map[string]string{"registry_host": host, "token": token})
	return nil
}
