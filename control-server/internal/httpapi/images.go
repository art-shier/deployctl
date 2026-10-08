package httpapi

import (
	"context"
	"net/http"
	"strings"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
)

type ImageLister interface {
	ListImages(context.Context, string) ([]registry.Image, error)
}

func (s *Server) images(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	slug := r.PathValue("slug")
	if err := permitted(p, "release.read", slug, ""); err != nil {
		return err
	}
	project, err := s.store.GetProject(r.Context(), slug)
	if err != nil {
		return err
	}
	inspector, ok := s.options.Verifier.(ImageLister)
	if !ok {
		return domain.ErrInvalid
	}
	images, err := inspector.ListImages(r.Context(), project.ImageRepository)
	if err != nil {
		return err
	}
	releases, err := s.store.ListReleases(r.Context(), slug)
	if err != nil {
		return err
	}
	for i := range images {
		for _, release := range releases {
			if strings.HasSuffix(release.Image, "@"+images[i].Digest) {
				images[i].Versions = append(images[i].Versions, release.Version)
			}
		}
	}
	reply(w, 200, images)
	return nil
}
