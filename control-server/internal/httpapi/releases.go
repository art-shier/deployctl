package httpapi

import (
	"context"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/artifacts"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
)

type ProofManifestVerifier interface {
	CheckManifestWithToken(context.Context, string, string, string) error
}

func (s *Server) getRelease(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "release.read", slug, ""); err != nil {
		return err
	}
	result, err := s.store.GetReleaseByVersion(r.Context(), slug, r.PathValue("version"))
	if err != nil {
		return err
	}
	reply(w, 200, result)
	return nil
}

func (s *Server) releases(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "release.read", slug, ""); err != nil {
		return err
	}
	items, err := s.store.ListReleases(r.Context(), slug)
	if err != nil {
		return err
	}
	reply(w, 200, items)
	return nil
}
func (s *Server) publish(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "release.publish", slug, ""); err != nil {
		return err
	}
	if _, err := s.store.GetProject(r.Context(), slug); err != nil {
		return err
	}
	r.Body = http.MaxBytesReader(w, r.Body, artifacts.MaxPackage+65536)
	reader, err := r.MultipartReader()
	if err != nil {
		return domain.ErrInvalid
	}
	fields := map[string]string{}
	seen := map[string]bool{}
	var pack *artifacts.ValidatedPackage
	for {
		part, e := reader.NextPart()
		if e == io.EOF {
			break
		}
		if e != nil {
			return domain.ErrInvalid
		}
		name := part.FormName()
		if seen[name] {
			return domain.ErrInvalid
		}
		seen[name] = true
		if name == "package" {
			if pack != nil {
				return domain.ErrInvalid
			}
			validated, e := artifacts.Validate(part, "", slug)
			part.Close()
			if e != nil {
				return e
			}
			pack = &validated
		} else {
			if name != "version" && name != "channel" && name != "sha256" {
				return domain.ErrInvalid
			}
			raw, e := io.ReadAll(io.LimitReader(part, 1025))
			part.Close()
			if e != nil || len(raw) > 1024 {
				return domain.ErrInvalid
			}
			fields[name] = string(raw)
		}
	}
	if pack == nil || fields["version"] != pack.Release.Version || fields["sha256"] != pack.Release.SHA256 || (fields["channel"] != "" && fields["channel"] != "stable") {
		return domain.ErrInvalid
	}
	if s.options.Verifier == nil {
		return domain.ErrInvalid
	}
	proof := r.Header.Get("X-Registry-Verification-Token")
	if registry.ValidateVerificationToken(proof) != nil {
		return domain.ErrInvalid
	}
	check := func(project domain.Project) error {
		if proof != "" {
			verifier, ok := s.options.Verifier.(ProofManifestVerifier)
			if !ok {
				return domain.ErrInvalid
			}
			return verifier.CheckManifestWithToken(r.Context(), pack.Release.Image, project.ImageRepository, proof)
		} else {
			return s.options.Verifier.CheckManifest(r.Context(), pack.Release.Image, project.ImageRepository)
		}
	}
	if err = artifacts.PublishFile(s.options.ArtifactsDir, *pack); err != nil {
		return err
	}
	release, err := s.store.PublishReleaseChecked(r.Context(), pack.Release, fields["channel"] == "stable", p.ID, check)
	if err != nil {
		return err
	}
	reply(w, 201, release)
	return nil
}
func (s *Server) retire(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	if err := s.store.RetireRelease(r.Context(), r.PathValue("slug"), r.PathValue("version"), p.ID); err != nil {
		return err
	}
	reply(w, 200, map[string]bool{"ok": true})
	return nil
}
func (s *Server) resolve(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	var body struct {
		Environment string `json:"environment"`
		Version     string `json:"version"`
	}
	if err := decode(w, r, &body); err != nil {
		return err
	}
	slug := r.PathValue("slug")
	project, err := s.store.GetProject(r.Context(), slug)
	if err != nil {
		return err
	}
	if body.Environment == "" {
		body.Environment = project.DefaultEnvironment
	}
	if domain.ValidateName(body.Environment, 32) != nil || (body.Version != "" && domain.ValidateVersion(body.Version) != nil) {
		return domain.ErrInvalid
	}
	if err = permitted(p, "resolve", slug, body.Environment); err != nil {
		return err
	}
	result, err := s.store.Resolve(r.Context(), slug, body.Environment, body.Version)
	if err != nil {
		return err
	}
	release, rev := result.Release, result.Revision
	reply(w, 200, map[string]any{"schema_version": 1, "minimum_client_version": "1.7.0", "project": slug, "environment": body.Environment,
		"release":       map[string]any{"id": release.ID, "version": release.Version, "image": release.Image, "package_path": "/api/v1/projects/" + slug + "/artifacts/" + release.ID, "sha256": release.SHA256},
		"configuration": map[string]any{"id": rev.ID, "revision": rev.Revision, "runtime_env": domain.Values(rev.Configuration.RuntimeEnv), "install_params": domain.Values(rev.Configuration.InstallParams), "deployment_defaults": rev.Configuration.DeploymentDefaults},
	})
	return nil
}
func (s *Server) artifact(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "artifact.read", slug, ""); err != nil {
		return err
	}
	release, err := s.store.GetRelease(r.Context(), slug, r.PathValue("id"))
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
func (s *Server) registryToken(w http.ResponseWriter, r *http.Request) {
	_, raw, ok := r.BasicAuth()
	if !ok {
		w.Header().Set("WWW-Authenticate", `Basic realm="ctl-registry"`)
		failure(w, domain.ErrUnauthorized)
		return
	}
	p, err := s.tokenPrincipal(r.Context(), raw)
	if err != nil {
		failure(w, err)
		return
	}
	if s.options.Signer == nil || r.URL.Query().Get("service") != s.options.Signer.Service {
		failure(w, domain.ErrInvalid)
		return
	}
	projects, err := s.store.ListProjects(r.Context())
	if err != nil {
		failure(w, err)
		return
	}
	access := []auth.Access{}
	scopes := r.URL.Query()["scope"]
	if len(scopes) > 128 {
		failure(w, domain.ErrInvalid)
		return
	}
	for _, line := range scopes {
		for _, scope := range strings.Fields(line) {
			for _, project := range projects {
				host, repository, _ := strings.Cut(project.ImageRepository, "/")
				if host == s.options.RegistryPublicHost {
					access = append(access, auth.ScopedAccess(p, scope, project.Slug, repository)...)
				}
			}
		}
	}
	now := time.Now().UTC()
	token, err := s.options.Signer.Sign(p, access, now)
	if err != nil {
		failure(w, err)
		return
	}
	reply(w, 200, map[string]any{"token": token, "access_token": token, "expires_in": 300, "issued_at": now.Format(time.RFC3339)})
}
