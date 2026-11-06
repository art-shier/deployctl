package store_test

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"testing"
)

func TestStaticProjectAndTargetInheritance(t *testing.T) {
	s := testutil.Store(t)
	ctx := context.Background()
	p, err := s.CreateProject(ctx, domain.Project{Slug: "static-a", Name: "Static A", DeploymentType: "static"}, "owner")
	if err != nil || p.ImageRepository != "" {
		t.Fatal(p, err)
	}
	_, err = s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{DeploymentDefaults: domain.DeploymentDefaults{TargetDir: "/var/www/group"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	r, err := s.GetRevision(ctx, p.Slug, "prod")
	if err != nil {
		t.Fatal(err)
	}
	// GetRevision is the raw project revision; Resolve uses effective group defaults.
	if r.Configuration.DeploymentDefaults.TargetDir != "" {
		t.Fatal("raw revision changed")
	}
	changed := p
	changed.DeploymentType = "docker"
	changed.ImageRepository = "registry.test/static-a"
	if _, err = s.UpdateProject(ctx, changed, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("mutable type: %v", err)
	}
	p.Name = "Changed"
	if _, err = s.UpdateProject(ctx, p, "owner"); err != nil {
		t.Fatal(err)
	}
}
