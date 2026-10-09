package store

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func waitForRepositoryLock(t *testing.T, s *Store, ctx context.Context, finished <-chan error) {
	t.Helper()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		var waiting bool
		if err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%pg_advisory_xact_lock(hashtextextended%')`).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			return
		}
		select {
		case err := <-finished:
			t.Fatal("project binding bypassed repository lock", err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-ticker.C:
		}
	}
}

func TestPublisherCreationRechecksRepositoryBindingsAfterOwnerCommit(t *testing.T) {
	s := fixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	p := publisherFixture(t, s, []string{"apps"}, nil)
	repository := "registry.example/candidate"
	writer, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Rollback(context.Background())
	if err = lockRepositories(ctx, writer, repository); err != nil {
		t.Fatal(err)
	}
	// Hold an uncommitted owner binding, invisible until creation obtains the lock.
	if _, err = writer.Exec(ctx, `UPDATE ctl_projects SET data=jsonb_set(data,'{image_repository}',to_jsonb($1::text)) WHERE slug='notes'`, repository); err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() {
		_, err := s.CreateProjectAuthorized(ctx, domain.Project{Slug: "candidate", Name: "Candidate", Group: "apps", ImageRepository: repository}, p)
		finished <- err
	}()
	waitForRepositoryLock(t, s, ctx, finished)
	if err = writer.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err = <-finished; !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("creation missed newly committed unauthorized binding", err)
	}
	if _, err = s.GetProject(ctx, "candidate"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("denied alias persisted", err)
	}
}

func TestOwnerProjectBindingChangesUseRepositoryLocks(t *testing.T) {
	for _, operation := range []string{"create", "update-old", "update-new"} {
		t.Run(operation, func(t *testing.T) {
			s := fixture(t)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			project, err := s.GetProject(ctx, "notes")
			if err != nil {
				t.Fatal(err)
			}
			locked := "registry.example/new"
			if operation == "update-old" {
				locked = project.ImageRepository
			}
			project.ImageRepository = "registry.example/new"
			guard, err := s.pool.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer guard.Rollback(context.Background())
			if err = lockRepositories(ctx, guard, locked); err != nil {
				t.Fatal(err)
			}
			finished := make(chan error, 1)
			go func() {
				var err error
				if operation == "create" {
					project.Slug = "new"
					_, err = s.CreateProject(ctx, project, "owner")
				} else {
					_, err = s.UpdateProject(ctx, project, "owner")
				}
				finished <- err
			}()
			waitForRepositoryLock(t, s, ctx, finished)
			if err = guard.Commit(ctx); err != nil {
				t.Fatal(err)
			}
			if err = <-finished; err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestPublisherRepositoryAliasesRespectCurrentMembershipAndArchives(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	p := publisherFixture(t, s, []string{"apps"}, nil)
	project := domain.Project{Slug: "alias", Name: "Alias", Group: "apps", ImageRepository: "registry.example/notes"}
	if _, err := s.CreateProjectAuthorized(ctx, project, p); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("unauthorized repository alias accepted", err)
	}
	if _, err := s.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.CreateProjectAuthorized(ctx, project, p); err != nil {
		t.Fatal("authorized shared repository denied", err)
	}
	if err := s.DeleteProject(ctx, "notes", "owner"); err != nil {
		t.Fatal(err)
	}
	project.Slug = "archived-alias"
	if _, err := s.CreateProjectAuthorized(ctx, project, p); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("archived repository alias accepted", err)
	}
	if _, err := s.CreateProject(ctx, project, "owner"); err != nil {
		t.Fatal("owner shared repository assignment denied", err)
	}
}
