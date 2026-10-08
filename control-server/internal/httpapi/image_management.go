package httpapi

import (
	"context"
	"errors"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
)

var errImageBusy = errors.New("image upload already in progress")
var errImageTooLarge = errors.New("image archive too large")

type imageManager interface {
	DeleteManifest(context.Context, string, string, []string) error
	ImportDockerArchive(context.Context, string, string, string) (registry.Image, error)
	InspectManifest(context.Context, string, string, string) (registry.ManifestDetails, error)
}

func (s *Server) imageCapabilities(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := permitted(p, "release.read", r.PathValue("slug"), ""); err != nil {
		return err
	}
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	_, available := s.options.Verifier.(imageManager)
	reply(w, 200, map[string]any{"managed": available && strings.Split(project.ImageRepository, "/")[0] == s.options.RegistryPublicHost, "max_archive_bytes": registry.MaxImageArchive})
	return nil
}
func (s *Server) imageDetails(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := permitted(p, "release.read", r.PathValue("slug"), ""); err != nil {
		return err
	}
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	manager, ok := s.options.Verifier.(imageManager)
	if !ok {
		return domain.ErrInvalid
	}
	details, err := manager.InspectManifest(r.Context(), project.ImageRepository, r.PathValue("digest"), r.Header.Get("X-Registry-Verification-Token"))
	if err != nil {
		return err
	}
	reply(w, 200, details)
	return nil
}
func (s *Server) deleteImage(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	digest := r.PathValue("digest")
	if domain.ValidateImage("registry.test/image@"+digest) != nil {
		return domain.ErrInvalid
	}
	manager, ok := s.options.Verifier.(imageManager)
	if !ok {
		return domain.ErrInvalid
	}
	err := s.store.ManageImages(r.Context(), r.PathValue("slug"), p.ID, "image.delete:"+digest, func(project domain.Project, roots []string) error {
		if strings.Split(project.ImageRepository, "/")[0] != s.options.RegistryPublicHost {
			return registry.ErrExternal
		}
		return manager.DeleteManifest(r.Context(), project.ImageRepository, digest, roots)
	})
	if err != nil {
		return err
	}
	reply(w, 200, map[string]bool{"ok": true})
	return nil
}
func (s *Server) uploadImage(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	tag := r.URL.Query().Get("tag")
	if !registry.ValidTag(tag) || len(r.URL.Query()) != 1 || len(r.URL.Query()["tag"]) != 1 {
		return domain.ErrInvalid
	}
	content, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || (content != "application/octet-stream" && content != "application/x-tar") {
		return registry.ErrArchive
	}
	if r.ContentLength > registry.MaxImageArchive {
		return errImageTooLarge
	}
	manager, ok := s.options.Verifier.(imageManager)
	if !ok {
		return domain.ErrInvalid
	}
	project, err := s.store.GetProject(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	if strings.Split(project.ImageRepository, "/")[0] != s.options.RegistryPublicHost {
		return registry.ErrExternal
	}
	select {
	case s.imageUpload <- struct{}{}:
		defer func() { <-s.imageUpload }()
	default:
		return errImageBusy
	}
	dir := filepath.Join(s.options.ArtifactsDir, ".uploads")
	if err = os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	// Remove only our old regular upload files; active uploads last at most 15 min.
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), "image-") {
			info, e := entry.Info()
			if e == nil && info.Mode().IsRegular() && time.Since(info.ModTime()) > 24*time.Hour {
				if e = os.Remove(filepath.Join(dir, entry.Name())); e != nil {
					return e
				}
			}
		}
	}
	file, err := os.CreateTemp(dir, "image-*.tar")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	r.Body = http.MaxBytesReader(w, r.Body, registry.MaxImageArchive)
	size, err := io.Copy(file, r.Body)
	if err != nil {
		var limit *http.MaxBytesError
		if errors.As(err, &limit) {
			return errImageTooLarge
		}
		return registry.ErrArchive
	}
	if size == 0 {
		return registry.ErrArchive
	}
	if err = file.Close(); err != nil {
		return err
	}
	var image registry.Image
	err = s.store.ManageImages(r.Context(), project.Slug, p.ID, "image.upload:"+tag, func(current domain.Project, _ []string) error {
		if current.ImageRepository != project.ImageRepository {
			return domain.ErrConflict
		}
		var e error
		image, e = manager.ImportDockerArchive(r.Context(), current.ImageRepository, tag, file.Name())
		return e
	})
	if err != nil {
		return err
	}
	reply(w, 201, image)
	return nil
}
