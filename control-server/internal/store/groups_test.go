package store

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"strings"
	"testing"
	"time"
)

func TestDefaultGroupMigrationPreservesLegacyIdentity(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	p, err := s.CreateProject(ctx, domain.Project{Slug: "legacy", Name: "Legacy", ImageRepository: "registry.test/legacy"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := auth.NewToken()
	token, err := s.CreateToken(ctx, domain.Token{Name: "old", Role: "deployer", Project: p.Slug, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.SaveRevision(ctx, p.Slug, "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "private-legacy", Secret: true}}}, "stable", "owner")
	if err != nil {
		t.Fatal(err)
	}
	// Reproduce the deployed pre-group schema in this disposable test database.
	release := domain.Release{ID: domain.NewID(), Project: p.Slug, Version: "v1.0.0", Status: "published", Image: "registry.test/legacy@sha256:" + strings.Repeat("a", 64)}
	encoded, _ := json.Marshal(release)
	if _, err = s.pool.Exec(ctx, "INSERT INTO ctl_releases(id,project,version,status,data) VALUES($1,$2,$3,$4,$5)", release.ID, p.Slug, release.Version, release.Status, encoded); err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, "UPDATE ctl_projects SET stable_version=$2 WHERE slug=$1", p.Slug, release.Version); err != nil {
		t.Fatal(err)
	}
	_, err = s.pool.Exec(ctx, `ALTER TABLE ctl_projects DROP COLUMN group_slug; DROP TABLE ctl_groups; UPDATE ctl_projects SET data=data-'group'; ALTER TABLE ctl_tokens ALTER COLUMN project SET NOT NULL`)
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err = s.Migrate(ctx); err != nil {
			t.Fatal(err)
		}
	}
	groups, err := s.ListGroups(ctx)
	if err != nil || len(groups) != 1 || groups[0].Slug != "default" {
		t.Fatal(groups, err)
	}
	p, err = s.GetProject(ctx, p.Slug)
	if err != nil || p.Group != "default" {
		t.Fatal(p, err)
	}
	identity, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil || identity.ID != token.ID || !identity.Can("resolve", "legacy", "prod") || identity.Can("resolve", "other", "prod") {
		t.Fatal(identity, err)
	}
	revision, err := s.GetRevision(ctx, p.Slug, "prod")
	if err != nil || revision.Configuration.RuntimeEnv["PASSWORD"].Value != "private-legacy" {
		t.Fatal("revision lost", err)
	}
	retained, err := s.GetReleaseByVersion(ctx, p.Slug, release.Version)
	if err != nil || retained.ID != release.ID || retained.Image != release.Image {
		t.Fatal("release lost", err)
	}
	var stable string
	if err = s.pool.QueryRow(ctx, "SELECT stable_version FROM ctl_projects WHERE slug=$1", p.Slug).Scan(&stable); err != nil || stable != release.Version {
		t.Fatal("stable channel lost", err)
	}
}

func TestGroupScopesFollowMembershipAndPreserveExplicitProjects(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Applications"}, "owner"); err != nil {
		t.Fatal(err)
	}
	notes, err := s.GetProject(ctx, "notes")
	if err != nil {
		t.Fatal(err)
	}
	notes.Group = "apps"
	if _, err = s.UpdateProject(ctx, notes, "owner"); err != nil {
		t.Fatal(err)
	}
	for _, slug := range []string{"config"} {
		if _, err := s.CreateProject(ctx, domain.Project{Slug: slug, Name: slug, Group: "apps", ImageRepository: "registry.test/" + slug}, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	raw, _ := auth.NewToken()
	grant := domain.Token{Name: "host", Role: "deployer", Groups: []string{"apps"}, Projects: []string{"notes"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}
	if _, err := s.CreateToken(ctx, grant, auth.HashToken(raw), "owner"); err != nil {
		t.Fatal(err)
	}
	check := func(project string, allowed bool) {
		t.Helper()
		p, err := s.Authenticate(ctx, auth.HashToken(raw))
		if err != nil || p.Can("resolve", project, "prod") != allowed || p.Can("resolve", project, "test") || p.Can("registry.push", project, "") {
			t.Fatal("invalid scope", project, p, err)
		}
	}
	check("notes", true)
	check("config", true)
	check("outside", false)
	p, _ := s.GetProject(ctx, "config")
	p.Group = "default"
	if _, err := s.UpdateProject(ctx, p, "owner"); err != nil {
		t.Fatal(err)
	}
	check("config", false)
	p, _ = s.GetProject(ctx, "notes")
	p.Group = "default"
	if _, err := s.UpdateProject(ctx, p, "owner"); err != nil {
		t.Fatal(err)
	}
	check("notes", true)
	if _, err := s.CreateProject(ctx, domain.Project{Slug: "future", Name: "Future", Group: "apps", ImageRepository: "registry.test/future"}, "owner"); err != nil {
		t.Fatal(err)
	}
	check("future", true)
	p, _ = s.GetProject(ctx, "future")
	p.Group = ""
	p.Name = "Old client update"
	p, err = s.UpdateProject(ctx, p, "owner")
	if err != nil || p.Group != "apps" {
		t.Fatal("old update lost group", p, err)
	}
}

func TestTokenScopeValidation(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	for _, scope := range []domain.Token{
		{}, {Groups: []string{"missing"}}, {Projects: []string{"missing"}}, {Groups: []string{"default", "default"}}, {Projects: []string{"notes", "notes"}}, {Project: "notes", Projects: []string{"notes"}},
	} {
		scope.Name = "bad"
		scope.Role = "deployer"
		scope.Environments = []string{"prod"}
		scope.ExpiresAt = time.Now().Add(time.Hour)
		raw, _ := auth.NewToken()
		if _, err := s.CreateToken(ctx, scope, auth.HashToken(raw), "owner"); err == nil {
			t.Fatal("invalid scope accepted", scope)
		}
	}
	raw, _ := auth.NewToken()
	token, err := s.CreateToken(ctx, domain.Token{Name: "default", Role: "deployer", Groups: []string{"default"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.RevokeToken(ctx, token.ID, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(raw)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("revoked token authenticated", err)
	}
}
