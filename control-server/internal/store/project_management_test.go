package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func publisherFixture(t *testing.T, s *Store, groups, projects []string) auth.Principal {
	t.Helper()
	raw, _ := auth.NewToken()
	ctx := context.Background()
	if _, err := s.CreateToken(ctx, domain.Token{Name: "publisher", Role: "publisher", Groups: groups, Projects: projects, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner"); err != nil {
		t.Fatal(err)
	}
	p, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestPublisherStaleMembershipCannotReadOrWriteProjectConfiguration(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveGroupRevision(ctx, "apps", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "new-group-secret", Secret: true}}}, "owner"); err != nil {
		t.Fatal(err)
	}
	stale := publisherFixture(t, s, []string{"default"}, nil)
	direct := publisherFixture(t, s, nil, []string{"notes"})
	old, err := s.GetProject(ctx, "notes")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.GetProjectAuthorized(ctx, "notes", stale); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale metadata grant accepted", err)
	}
	if r, err := s.GetRevisionAuthorized(ctx, "notes", "prod", stale, true); !errors.Is(err, domain.ErrForbidden) || len(r.InheritedConfiguration.RuntimeEnv) != 0 {
		t.Fatal("stale publisher read new group's secret", r, err)
	}
	if _, err = s.ListEnvironmentsAuthorized(ctx, "notes", stale); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale environment list accepted", err)
	}
	if _, err = s.UpdateProjectAuthorized(ctx, old, stale); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale metadata write accepted", err)
	}
	value := "bad"
	patch := domain.ConfigurationPatch{ExpectedRevision: 1, RuntimeEnv: []domain.Change{{Key: "VALUE", Operation: "set", Value: &value}}}
	if _, err = s.SaveConfigurationAuthorized(ctx, "notes", "prod", patch, stale); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale config write accepted", err)
	}
	if r, err := s.GetRevisionAuthorized(ctx, "notes", "prod", direct, true); err != nil || r.InheritedConfiguration.RuntimeEnv["PASSWORD"].Value != "new-group-secret" {
		t.Fatal("explicit project grant lost after move", err)
	}
	revision, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil || revision.Revision != 1 {
		t.Fatal("failed writes changed own revision", revision, err)
	}
	audits, err := s.ListAudit(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, event := range audits {
		if event.Actor == stale.ID && event.Action == "configuration.reveal" {
			t.Fatal("denied reveal was audited as successful")
		}
	}
}

func TestPublisherConfigurationWriteReauthorizesAfterWaitingForMembershipLock(t *testing.T) {
	s := fixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	p := publisherFixture(t, s, []string{"default"}, nil)
	writer, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Rollback(context.Background())
	if _, err = writer.Exec(ctx, `UPDATE ctl_projects SET group_slug='apps',data=jsonb_set(data,'{group}','"apps"') WHERE slug='notes'`); err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() {
		_, err := s.SaveConfigurationAuthorized(ctx, "notes", "prod", domain.ConfigurationPatch{ExpectedRevision: 1}, p)
		finished <- err
	}()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		var waiting bool
		if err = s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR UPDATE%')`).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-finished:
			t.Fatal("write did not wait for membership lock", err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-ticker.C:
		}
	}
	if err = writer.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err = <-finished; !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale grant survived row-lock wait", err)
	}
	r, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil || r.Revision != 1 {
		t.Fatal("denied config write committed", r, err)
	}
}

func TestTombstonesRetainHistoryAndReserveScopesAcrossMigration(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveGroupRevision(ctx, "apps", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "retained-private", Secret: true}}}, "owner"); err != nil {
		t.Fatal(err)
	}
	raw, _ := auth.NewToken()
	legacy, err := s.CreateToken(ctx, domain.Token{Name: "legacy", Role: "publisher", Project: "notes", Groups: []string{"apps"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	release, err := s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 123}, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	rev, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.SaveReceipt(ctx, domain.Receipt{HostID: domain.NewID(), Project: "notes", Environment: "prod", ReleaseID: release.ID, RevisionID: rev.ID, CLIVersion: "1.11.0"}); err != nil {
		t.Fatal(err)
	}
	if err = s.DeleteGroup(ctx, "apps", "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("nonempty group deleted", err)
	}
	if err = s.DeleteProject(ctx, "notes", "owner"); err != nil {
		t.Fatal(err)
	}
	if err = s.DeleteGroup(ctx, "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err = s.Migrate(ctx); err != nil {
			t.Fatal(err)
		}
	}
	identity, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil || identity.Project != "" || len(identity.Groups) != 0 || len(identity.ExplicitProjects) != 0 || identity.Can("registry.pull", "notes", "") || identity.CanInGroup("configuration.read", "notes", "apps", "prod") {
		t.Fatal("archived grants remained active", identity, err)
	}
	items, err := s.ListTokens(ctx)
	if err != nil || len(items) != 1 || items[0].ID != legacy.ID || items[0].Project != "notes" || len(items[0].Groups) != 1 {
		t.Fatal("grant metadata was lost", items, err)
	}
	for _, table := range []string{"ctl_revisions", "ctl_group_revisions", "ctl_releases", "ctl_receipts", "ctl_tokens"} {
		var count int
		if err = s.pool.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count); err != nil || count < 1 {
			t.Fatal("history removed", table, count, err)
		}
	}
	if _, err = s.GetReleaseByVersion(ctx, "notes", "v1.0.0"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("archived release accessible", err)
	}
	if _, err = s.Resolve(ctx, "notes", "prod", ""); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("archived project deployable", err)
	}
	checked := false
	if _, err = s.PublishReleaseChecked(ctx, domain.Release{Project: "notes", Version: "v1.1.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 123}, true, "owner", func(domain.Project) error { checked = true; return nil }); !errors.Is(err, domain.ErrNotFound) || checked {
		t.Fatal("archived project publication reached registry", err)
	}
	if _, err = s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{}, "stable", "owner"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("archived config mutated", err)
	}
	if _, err = s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Reserved"}, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("archived group resurrected", err)
	}
	if _, err = s.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Reserved", ImageRepository: "registry.example/notes"}, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("archived project resurrected", err)
	}
	encoded, _ := json.Marshal(items)
	if strings.Contains(string(encoded), raw) {
		t.Fatal("retained token metadata exposed secret")
	}
}

func TestGroupDeletionRacesCannotLeaveActiveMembersUnderArchivedGroup(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	for _, operation := range []string{"create", "move"} {
		for i := range 8 {
			group := fmt.Sprintf("apps-%s-%d", operation, i)
			slug := fmt.Sprintf("project-%s-%d", operation, i)
			if _, err := s.CreateGroup(ctx, domain.Group{Slug: group, Name: group}, "owner"); err != nil {
				t.Fatal(err)
			}
			if operation == "move" {
				if _, err := s.CreateProject(ctx, domain.Project{Slug: slug, Name: slug, ImageRepository: "registry.example/" + slug}, "owner"); err != nil {
					t.Fatal(err)
				}
			}
			start := make(chan struct{})
			member := make(chan error, 1)
			deleted := make(chan error, 1)
			go func() {
				<-start
				var err error
				if operation == "create" {
					_, err = s.CreateProject(ctx, domain.Project{Slug: slug, Name: slug, Group: group, ImageRepository: "registry.example/" + slug}, "owner")
				} else {
					_, err = s.MoveProjectGroup(ctx, slug, "default", group, "owner")
				}
				member <- err
			}()
			go func() { <-start; deleted <- s.DeleteGroup(ctx, group, "owner") }()
			close(start)
			memberErr, deleteErr := <-member, <-deleted
			if memberErr == nil && deleteErr == nil {
				t.Fatal("membership and deletion both committed", operation)
			}
			if deleteErr != nil && !errors.Is(deleteErr, domain.ErrConflict) {
				t.Fatal("unexpected group deletion failure", deleteErr)
			}
			if memberErr != nil && !errors.Is(memberErr, domain.ErrNotFound) && !errors.Is(memberErr, domain.ErrInvalid) {
				t.Fatal("unexpected membership failure", memberErr)
			}
			var unsafe bool
			if err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM ctl_projects p JOIN ctl_groups g ON g.slug=p.group_slug WHERE p.deleted_at IS NULL AND g.deleted_at IS NOT NULL)`).Scan(&unsafe); err != nil || unsafe {
				t.Fatal("active project under archived group", operation, err)
			}
		}
	}
}
