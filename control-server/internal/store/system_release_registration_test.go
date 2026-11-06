package store

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"testing"
)

func TestReleaseRegistrationPersistenceImmutabilityAndCredential(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := updateRelease()
	r.CLIURL = "https://unreachable.invalid/cli"
	r.ServerURL = "https://unreachable.invalid/server"
	token, err := s.RotateReleaseRegistrationToken(ctx, "owner")
	if err != nil {
		t.Fatal(err)
	}
	saved, created, err := s.RegisterSystemRelease(ctx, r, "pipeline", "pipeline", auth.HashToken(token))
	if err != nil || !created || saved.Source != "pipeline" {
		t.Fatal(saved, created, err)
	}
	_, created, err = s.RegisterSystemRelease(ctx, r, "owner", "manual", "")
	if err != nil || created {
		t.Fatal(created, err)
	}
	changed := r
	changed.CLIURL += "changed"
	if _, _, err = s.RegisterSystemRelease(ctx, changed, "owner", "manual", ""); !errors.Is(err, domain.ErrConflict) {
		t.Fatal(err)
	}
	if err = s.SeedSystemReleases(ctx, []domain.SystemRelease{changed}); err != nil {
		t.Fatal(err)
	}
	rows, err := s.RegisteredSystemReleases(ctx)
	if err != nil || len(rows) != 1 || rows[0].CLIURL != r.CLIURL {
		t.Fatal(rows, err)
	}
	next, err := s.RotateReleaseRegistrationToken(ctx, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.RegisterSystemRelease(ctx, r, "pipeline", "pipeline", auth.HashToken(token)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal(err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(next)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("registration token has general access", err)
	}
	if err = s.RevokeReleaseRegistrationToken(ctx, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.RegisterSystemRelease(ctx, r, "pipeline", "pipeline", auth.HashToken(next)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal(err)
	}
}

func TestCustomReleaseFreezesURLsAndChecksHelperAndPackageOwnership(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, _ := updateFixture(t, s, "center", "center")
	release := updateRelease()
	release.CLIURL = "https://packages.example/cli"
	release.ServerURL = "https://packages.example/server"
	req := domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: release.Version, Parallelism: 1, BackupConfirmed: true}
	fetch := func(context.Context, string) (domain.SystemRelease, error) { return release, nil }
	if _, err := s.CreateUpdateJobs(ctx, req, "owner", fetch); !errors.Is(err, domain.ErrInvalid) {
		t.Fatal("old helper accepted custom URLs", err)
	}
	if err := s.HeartbeatUpdater(ctx, u.ID, domain.UpdaterHeartbeat{CurrentVersion: "v1.14.0", HelperVersion: "v1.17.0", Platform: "linux", Protocol: 1, Ready: true}); err != nil {
		t.Fatal(err)
	}
	jobs, err := s.CreateUpdateJobs(ctx, req, "owner", fetch)
	if err != nil {
		t.Fatal(err)
	}
	job := jobs[0]
	if job.CLIURL != release.CLIURL || job.ServerURL != release.ServerURL {
		t.Fatal(job)
	}
	if _, err = s.UpdateJobMaterial(ctx, u.ID, job.ID); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("queued download", err)
	}
	if _, err = s.PollUpdater(ctx, u.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.StartUpdateJob(ctx, u.ID, job.ID); err != nil {
		t.Fatal(err)
	}
	release.ServerURL = "https://changed.example/server"
	material, err := s.UpdateJobMaterial(ctx, u.ID, job.ID)
	if err != nil || material.ServerURL != job.ServerURL {
		t.Fatal(material, err)
	}
	if _, err = s.UpdateJobMaterial(ctx, domain.NewID(), job.ID); err == nil {
		t.Fatal("other updater downloaded package")
	}
}
