package store

import (
	"context"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"strings"
	"testing"
	"time"
)

func TestImageDeletionSerializesWithPublicationAcrossSharedRepository(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	repository := "registry.test/shared"
	for _, slug := range []string{"shared-a", "other"} {
		if _, err := s.CreateProject(ctx, domain.Project{Slug: slug, Name: slug, ImageRepository: repository, DefaultEnvironment: "prod"}, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	release := domain.Release{Project: "other", Version: "v1.0.0", Image: repository + "@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 123}
	entered := make(chan struct{})
	resume := make(chan struct{})
	published := make(chan error, 1)
	go func() {
		_, err := s.PublishReleaseChecked(ctx, release, false, "publisher", func(domain.Project) error { close(entered); <-resume; return nil })
		published <- err
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("publication never entered verification")
	}
	operated := make(chan domain.Project, 1)
	deleted := make(chan error, 1)
	go func() {
		deleted <- s.ManageImages(ctx, "shared-a", "owner", "image.delete", func(project domain.Project) error { operated <- project; return nil })
	}()
	select {
	case <-operated:
		close(resume)
		t.Fatal("delete bypassed repository publication lock")
	case <-time.After(100 * time.Millisecond):
	}
	close(resume)
	if err := <-published; err != nil {
		t.Fatal(err)
	}
	project := <-operated
	if project.ImageRepository != repository {
		t.Fatal("wrong repository under deletion lock", project.ImageRepository)
	}
	if err := <-deleted; err != nil {
		t.Fatal(err)
	}
	if err := s.RetireRelease(ctx, "other", release.Version, "owner"); err != nil {
		t.Fatal(err)
	}
	if err := s.ManageImages(ctx, "shared-a", "owner", "image.test", func(_ domain.Project) error {
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
