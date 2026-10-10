package store_test

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"strings"
	"testing"
)

func TestStaticReleaseImmutableAndDirectoryDefaults(t *testing.T) {
	s := testutil.Store(t)
	ctx := context.Background()
	p, err := s.CreateProject(ctx, domain.Project{Slug: "static-a", Name: "A", DeploymentType: "static"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	r := domain.Release{Project: p.Slug, Version: "v1.0.0", DeploymentType: "static", ArchiveFormat: "zip", SHA256: strings.Repeat("a", 64), Size: 11 * 1024 * 1024, ExpandedSize: 1, EntryCount: 1, Commit: strings.Repeat("b", 40)}
	first, err := s.PublishRelease(ctx, r, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	retry, err := s.PublishRelease(ctx, r, true, "owner")
	if err != nil || retry.ID != first.ID {
		t.Fatal(retry, err)
	}
	for _, change := range []func(*domain.Release){func(r *domain.Release) { r.Commit = strings.Repeat("c", 40) }, func(r *domain.Release) { r.ArchiveFormat = "tar.gz" }, func(r *domain.Release) { r.EntryCount = 2 }, func(r *domain.Release) { r.SHA256 = strings.Repeat("d", 64) }} {
		altered := r
		change(&altered)
		if _, err = s.PublishRelease(ctx, altered, true, "owner"); !errors.Is(err, domain.ErrConflict) {
			t.Fatal("version not immutable", err)
		}
	}
	cfg := domain.Configuration{DeploymentDefaults: domain.DeploymentDefaults{TargetDir: "/var/www/group"}, RuntimeEnv: map[string]domain.Variable{"SECRET": {Value: "hidden", Secret: true}}}
	if _, err = s.SaveGroupRevision(ctx, "default", "prod", 0, cfg, "owner"); err != nil {
		t.Fatal(err)
	}
	result, err := s.Resolve(ctx, p.Slug, "prod", "")
	if err != nil || result.Revision.Configuration.DeploymentDefaults.TargetDir != "/var/www/group" || len(result.Revision.Configuration.RuntimeEnv) != 0 {
		t.Fatal(result, err)
	}
	own := domain.Configuration{DeploymentDefaults: domain.DeploymentDefaults{TargetDir: "/var/www/own"}}
	if _, err = s.SaveRevision(ctx, p.Slug, "prod", 1, own, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	result, err = s.Resolve(ctx, p.Slug, "prod", "")
	if err != nil || result.Revision.Configuration.DeploymentDefaults.TargetDir != "/var/www/own" {
		t.Fatal(result, err)
	}
	if _, err = s.SaveRevision(ctx, p.Slug, "prod", 2, domain.Configuration{}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	result, err = s.Resolve(ctx, p.Slug, "prod", "")
	if err != nil || result.Revision.Configuration.DeploymentDefaults.TargetDir != "/var/www/group" {
		t.Fatal(result, err)
	}
}
