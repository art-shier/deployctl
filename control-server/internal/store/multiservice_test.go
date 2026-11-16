package store

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"strings"
	"testing"
)

func multiFixtureRelease() domain.Release {
	return domain.Release{Project: "notes", Version: "v2.0.0", SchemaVersion: 3, Images: map[string]string{"api": "registry.example/notes@sha256:" + strings.Repeat("a", 64), "postgres": "registry.example/notes@sha256:" + strings.Repeat("b", 64)}, Capabilities: []string{"multi_service_v1"}, MinimumDeployctlVersion: "1.20.0", SHA256: strings.Repeat("c", 64), Size: 100}
}
func TestMultiReleasePublishAndImmutability(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := multiFixtureRelease()
	published, err := s.PublishRelease(ctx, r, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := s.GetReleaseByVersion(ctx, "notes", r.Version)
	if err != nil || len(loaded.Images) != 2 || loaded.SchemaVersion != 3 {
		t.Fatal("metadata not persisted", err)
	}
	again, err := s.PublishRelease(ctx, r, true, "owner")
	if err != nil || again.ID != published.ID {
		t.Fatal("idempotency", err)
	}
	r.Images["postgres"] = "registry.example/notes@sha256:" + strings.Repeat("d", 64)
	if _, err = s.PublishRelease(ctx, r, true, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("changed images accepted", err)
	}
	r = multiFixtureRelease()
	r.Version = "v2.0.1"
	r.Images["postgres"] = "registry.example/other@sha256:" + strings.Repeat("b", 64)
	if _, err = s.PublishRelease(ctx, r, true, "owner"); !errors.Is(err, domain.ErrInvalid) {
		t.Fatal("cross project images accepted", err)
	}
}
func TestMultiReleaseOldAgentCannotQueueTask(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.PublishRelease(ctx, multiFixtureRelease(), true, "owner"); err != nil {
		t.Fatal(err)
	}
	n, enrollment, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Projects: []string{"notes"}, Environments: []string{"prod"}, Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(enrollment), "1.19.5", "linux"); err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}
	if _, err = s.CreateTask(ctx, "notes", req, auth.Principal{ID: "owner", Role: "owner"}); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("old agent accepted", err)
	}
}

func TestMultiReleaseCapabilitiesAreRecheckedBeforeDispatch(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	if _, err := s.PublishRelease(ctx, multiFixtureRelease(), true, "owner"); err != nil {
		t.Fatal(err)
	}
	n, enrollment, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Projects: []string{"notes"}, Environments: []string{"prod"}, Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(enrollment), "1.20.0", "linux"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.HeartbeatNodeCapabilities(ctx, n.ID, "1.20.0", "linux", true, nil, []string{"multi_service_v1"}, nil); err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}
	task, err := s.CreateTask(ctx, "notes", req, owner)
	if err != nil {
		t.Fatal(err)
	}
	// Downgrade while queued: a prior capability advertisement is not a lease.
	if _, _, err = s.HeartbeatNode(ctx, n.ID, "1.19.5", "linux", true, nil); err != nil {
		t.Fatal(err)
	}
	polled, err := s.PollNode(ctx, n.ID)
	if err != nil || polled != nil {
		t.Fatal("incapable node received task", err)
	}
	var status string
	if err = s.pool.QueryRow(ctx, "SELECT status FROM ctl_remote_tasks WHERE id=$1", task.ID).Scan(&status); err != nil || status != "cancelled" {
		t.Fatal("task not cancelled", status, err)
	}
}

func TestMultiReleaseCapableAgentReceivesCompleteFrozenResolution(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.PublishRelease(ctx, multiFixtureRelease(), true, "owner"); err != nil {
		t.Fatal(err)
	}
	n, enrollment, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Projects: []string{"notes"}, Environments: []string{"prod"}, Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(enrollment), "1.20.0", "linux"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.HeartbeatNodeCapabilities(ctx, n.ID, "1.20.0", "linux", true, nil, []string{"multi_service_v1"}); err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{RequestedJobs: []string{"restore"}, NodeID: n.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}
	task, err := s.CreateTask(ctx, "notes", req, auth.Principal{ID: "owner", Role: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	changed := req
	changed.RequestedJobs = []string{"other-job"}
	if _, err = s.CreateTask(ctx, "notes", changed, auth.Principal{ID: "owner", Role: "owner"}); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("accepted changed job selection on retry", err)
	}
	polled, err := s.PollNode(ctx, n.ID)
	if err != nil || polled == nil {
		t.Fatal("not dispatched", err)
	}
	if polled.Resolution["schema_version"] != 3 || len(polled.Resolution["release"].(map[string]any)["images"].(map[string]string)) != 2 {
		t.Fatal("incomplete task resolution")
	}
	if len(polled.RequestedJobs) != 1 || polled.RequestedJobs[0] != "restore" {
		t.Fatal("job inputs lost in frozen task")
	}
	// Capability loss after polling is rechecked before any execution starts.
	if _, _, err = s.HeartbeatNode(ctx, n.ID, "1.19.5", "linux", true, nil); err != nil {
		t.Fatal(err)
	}
	if _, err = s.StartNodeTask(ctx, n.ID, task.ID); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("downgraded node started task", err)
	}
}
