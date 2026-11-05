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

func TestRemoteEnrollmentAndFrozenTaskLifecycle(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	n, enrollment, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Projects: []string{"notes"}, Environments: []string{"prod"}, Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	id, token, err := s.EnrollNode(ctx, auth.HashToken(enrollment), "test", "linux")
	if err != nil || id != n.ID {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(enrollment), "test", "linux"); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("reused enrollment", err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(token)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("node platform identity", err)
	}

	if _, err = s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"KEY": {Value: "frozen-secret", Secret: true}}}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 1}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}
	task, err := s.CreateTask(ctx, "notes", req, owner)
	if err != nil {
		t.Fatal(err)
	}
	same, err := s.CreateTask(ctx, "notes", req, owner)
	if err != nil || same.ID != task.ID {
		t.Fatal("idempotency", err)
	}
	req.Action = "upgrade"
	if _, err = s.CreateTask(ctx, "notes", req, owner); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("different retry", err)
	}
	raw, _ := json.Marshal(task)
	if strings.Contains(string(raw), "frozen-secret") {
		t.Fatal("secret response")
	}
	claimed, err := s.PollNode(ctx, n.ID)
	if err != nil || claimed == nil || claimed.ID != task.ID {
		t.Fatal("claim", err)
	}
	var encrypted []byte
	if err = s.pool.QueryRow(ctx, "SELECT ciphertext FROM ctl_remote_tasks WHERE id=$1", task.ID).Scan(&encrypted); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encrypted), "frozen-secret") {
		t.Fatal("plaintext snapshot")
	}
	if _, err = s.StartNodeTask(ctx, n.ID, task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CancelTask(ctx, "notes", task.ID, owner); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("cancel running", err)
	}
	obs := domain.Observation{ID: task.InstanceID, Project: "notes", Environment: "prod", ActualVersion: "v1.0.0", Health: "healthy"}
	report := domain.TaskReport{Status: "succeeded", Stage: "completed", Observation: &obs}
	if _, err = s.ReportNodeTask(ctx, n.ID, task.ID, report); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportNodeTask(ctx, n.ID, task.ID, report); err != nil {
		t.Fatal("repeat report", err)
	}
	report.Status = "failed"
	report.Stage = "execution_failed"
	if _, err = s.ReportNodeTask(ctx, n.ID, task.ID, report); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("terminal changed", err)
	}
}

func TestRemoteConcurrentRequestRetries(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	n, _, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "rollback", RequestID: domain.NewID()}
	blocker, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(ctx)
	if _, err = blocker.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "remote-request:owner:"+req.RequestID); err != nil {
		t.Fatal(err)
	}
	results := make(chan domain.Task, 8)
	errs := make(chan error, 8)
	start := make(chan struct{})
	for range 8 {
		go func() { <-start; task, err := s.CreateTask(ctx, "notes", req, owner); results <- task; errs <- err }()
	}
	close(start)
	time.Sleep(150 * time.Millisecond)
	if err = blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	id := ""
	for range 8 {
		task := <-results
		if err := <-errs; err != nil {
			t.Fatal(err)
		}
		if id != "" && id != task.ID {
			t.Fatal("duplicate task")
		}
		id = task.ID
	}
}
func TestRemoteUnknownObservationPreservesKnownTransaction(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	n, enroll, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(enroll), "test", "linux"); err != nil {
		t.Fatal(err)
	}
	task, err := s.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "rollback", RequestID: domain.NewID()}, owner)
	if err != nil {
		t.Fatal(err)
	}
	obs := domain.Observation{ID: task.InstanceID, Project: "notes", Environment: "prod", Health: "unhealthy", ActualVersion: "v1.0.0", Binding: &domain.Binding{HostPort: 9000, BindAddress: "127.0.0.1"}, PendingTransaction: true}
	if _, _, err = s.HeartbeatNode(ctx, n.ID, "test", "linux", true, []domain.Observation{obs}); err != nil {
		t.Fatal(err)
	}
	obs.Health = "unknown"
	obs.ActualVersion = ""
	obs.Binding = nil
	obs.PendingTransaction = false
	if _, _, err = s.HeartbeatNode(ctx, n.ID, "test", "linux", true, []domain.Observation{obs}); err != nil {
		t.Fatal(err)
	}
	instances, err := s.ListInstances(ctx, "notes")
	if err != nil {
		t.Fatal(err)
	}
	i := instances[0]
	if !i.PendingTransaction || i.ActualVersion != "v1.0.0" || i.Binding == nil || i.Health != "unknown" {
		t.Fatal("lost safe observation", i)
	}
}

func TestRemotePendingTransactionRecheckedBeforeStart(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	n, _, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 1}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	task, err := s.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "upgrade", RequestID: domain.NewID()}, owner)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PollNode(ctx, n.ID); err != nil {
		t.Fatal(err)
	}
	obs := domain.Observation{ID: task.InstanceID, Project: "notes", Environment: "prod", Health: "unhealthy", PendingTransaction: true}
	if _, _, err = s.HeartbeatNode(ctx, n.ID, "test", "linux", true, []domain.Observation{obs}); err != nil {
		t.Fatal(err)
	}
	if _, err = s.StartNodeTask(ctx, n.ID, task.ID); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("unsafe start", err)
	}
}

func remoteNode(t *testing.T, s *Store, name string) domain.Node {
	t.Helper()
	n, _, _, err := s.CreateNode(context.Background(), domain.NodeInput{Name: name, Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod", "test"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	return n
}
func remoteTask(t *testing.T, s *Store, n domain.Node, env string) domain.Task {
	t.Helper()
	task, err := s.CreateTask(context.Background(), "notes", domain.TaskRequest{NodeID: n.ID, Environment: env, Action: "rollback", RequestID: domain.NewID()}, auth.Principal{ID: "owner", Role: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	return task
}
func TestRemoteMultiNodeInstancesAndSingleClaim(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n1 := remoteNode(t, s, "one")
	n2 := remoteNode(t, s, "two")
	first := remoteTask(t, s, n1, "prod")
	second := remoteTask(t, s, n2, "prod")
	queued := remoteTask(t, s, n1, "test")
	if first.InstanceID == second.InstanceID {
		t.Fatal("shared node state")
	}
	if _, err := s.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n1.ID, Environment: "prod", Action: "rollback", RequestID: domain.NewID()}, auth.Principal{ID: "owner", Role: "owner"}); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("duplicate active instance", err)
	}
	for range 3 {
		task, err := s.PollNode(ctx, n1.ID)
		if err != nil || task.ID != first.ID {
			t.Fatal("claim changed", task, err)
		}
	}
	if _, err := s.ReportNodeTask(ctx, n1.ID, first.ID, domain.TaskReport{Status: "needs_attention", Stage: "interrupted"}); err != nil {
		t.Fatal(err)
	}
	task, err := s.PollNode(ctx, n1.ID)
	if err != nil || task.ID != queued.ID {
		t.Fatal("next queued task", err)
	}
}
func TestRemoteCancelStartSerialized(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "one")
	owner := auth.Principal{ID: "owner", Role: "owner"}
	for range 8 {
		task := remoteTask(t, s, n, "prod")
		if _, err := s.PollNode(ctx, n.ID); err != nil {
			t.Fatal(err)
		}
		gate := make(chan struct{})
		started := make(chan error, 1)
		cancelled := make(chan error, 1)
		go func() { <-gate; _, err := s.StartNodeTask(ctx, n.ID, task.ID); started <- err }()
		go func() { <-gate; _, err := s.CancelTask(ctx, "notes", task.ID, owner); cancelled <- err }()
		close(gate)
		se, ce := <-started, <-cancelled
		if se == nil {
			if !errors.Is(ce, domain.ErrConflict) {
				t.Fatal("start winner", ce)
			}
			if _, err := s.ReportNodeTask(ctx, n.ID, task.ID, domain.TaskReport{Status: "needs_attention", Stage: "interrupted"}); err != nil {
				t.Fatal(err)
			}
		} else if ce != nil || !errors.Is(se, domain.ErrConflict) {
			t.Fatal("cancel winner", se, ce)
		}
	}
}
func TestRemoteCrossNodeAndDisabledRecovery(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	one := remoteNode(t, s, "one")
	two := remoteNode(t, s, "two")
	task := remoteTask(t, s, one, "prod")
	if _, err := s.PollNode(ctx, one.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.StartNodeTask(ctx, two.ID, task.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("foreign start", err)
	}
	if _, err := s.ReportNodeTask(ctx, two.ID, task.ID, domain.TaskReport{Status: "needs_attention", Stage: "interrupted"}); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("foreign report", err)
	}
	if _, err := s.NodeTaskMaterial(ctx, two.ID, task.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("foreign material", err)
	}
	if _, _, err := s.HeartbeatNode(ctx, two.ID, "test", "linux", true, []domain.Observation{{ID: task.InstanceID, Project: "notes", Environment: "prod", Health: "healthy"}}); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("foreign instance", err)
	}
	one.Enabled = false
	if _, err := s.UpdateNode(ctx, one.ID, one.NodeInput, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.StartNodeTask(ctx, one.ID, task.ID); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("disabled start", err)
	}
	if _, err := s.NodeTaskMaterial(ctx, one.ID, task.ID); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("disabled material", err)
	}
	if _, err := s.ReportNodeTask(ctx, one.ID, task.ID, domain.TaskReport{Status: "needs_attention", Stage: "interrupted"}); err != nil {
		t.Fatal("disabled uncertain lease recovery", err)
	}
}
func TestRemoteEnrollmentRotationAndStaleRequest(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n, once, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "one", Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, old, err := s.EnrollNode(ctx, auth.HashToken(once), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	stale := WithNodeIdentity(ctx, auth.HashToken(old))
	replacement, _, err := s.RotateNodeEnrollment(ctx, n.ID, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.AuthenticateNode(ctx, auth.HashToken(old)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("old node still authenticates", err)
	}
	if _, _, err = s.HeartbeatNode(stale, n.ID, "test", "linux", true, nil); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("stale request mutated rotated identity", err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(replacement), "test", "linux"); err != nil {
		t.Fatal(err)
	}
	nodes, err := s.ListNodes(ctx)
	if err != nil || nodes[0].Online {
		t.Fatal("enrollment without heartbeat online", err)
	}
}
func TestRemoteCredentialAndGrantRevalidation(t *testing.T) {
	for _, change := range []string{"revoke", "group", "node", "expire"} {
		t.Run(change, func(t *testing.T) {
			s := fixture(t)
			ctx := context.Background()
			n := remoteNode(t, s, "one")
			for _, group := range []string{"alpha", "beta"} {
				if _, err := s.CreateGroup(ctx, domain.Group{Slug: group, Name: group}, "owner"); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := s.MoveProjectGroup(ctx, "notes", "default", "alpha", "owner"); err != nil {
				t.Fatal(err)
			}
			raw, _ := auth.NewToken()
			token, err := s.CreateReadableToken(ctx, domain.Token{Name: "operator", Role: "deployer", Groups: []string{"alpha"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner")
			if err != nil {
				t.Fatal(err)
			}
			principal, err := s.Authenticate(ctx, auth.HashToken(raw))
			if err != nil {
				t.Fatal(err)
			}
			task, err := s.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "rollback", RequestID: domain.NewID()}, principal)
			if err != nil {
				t.Fatal(err)
			}
			// Half the cases recheck while queued, the others while already leased.
			if change == "group" || change == "expire" {
				if _, err = s.PollNode(ctx, n.ID); err != nil {
					t.Fatal(err)
				}
			}
			switch change {
			case "revoke":
				err = s.RevokeToken(ctx, token.ID, "owner")
			case "group":
				_, err = s.MoveProjectGroup(ctx, "notes", "alpha", "beta", "owner")
			case "node":
				n.ExcludedProjects = []string{"notes"}
				_, err = s.UpdateNode(ctx, n.ID, n.NodeInput, "owner")
			case "expire":
				_, err = s.pool.Exec(ctx, "UPDATE ctl_remote_tasks SET data=jsonb_set(data,'{created_at}',to_jsonb($2::text)),created_at=$2::timestamptz WHERE id=$1", task.ID, time.Now().Add(-25*time.Hour).Format(time.RFC3339Nano))
			}
			if err != nil {
				t.Fatal(err)
			}
			if change == "group" || change == "expire" {
				if _, err = s.StartNodeTask(ctx, n.ID, task.ID); err == nil {
					t.Fatal("unauthorized start")
				}
			} else {
				claimed, e := s.PollNode(ctx, n.ID)
				if e != nil || claimed != nil {
					t.Fatal("unauthorized dispatch", claimed, e)
				}
			}
			tasks, err := s.ListTasks(ctx, "notes")
			if err != nil || !domain.TaskTerminal(tasks[0].Status) {
				t.Fatal("invalidated task retained", tasks, err)
			}
		})
	}
}
func TestRemoteFrozenMergedResolution(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "one")
	if _, err := s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"GROUP": {Value: "group-secret", Secret: true}}}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PROJECT": {Value: "project-secret", Secret: true}}}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	release := domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 1}
	if _, err := s.PublishRelease(ctx, release, true, "owner"); err != nil {
		t.Fatal(err)
	}
	task, err := s.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}, auth.Principal{ID: "owner", Role: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	release.Version = "v2.0.0"
	if _, err = s.PublishRelease(ctx, release, true, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.SaveGroupRevision(ctx, "default", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"GROUP": {Value: "new-secret", Secret: true}}}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.SaveRevision(ctx, "notes", "prod", 2, domain.Configuration{}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	claimed, err := s.PollNode(ctx, n.ID)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(claimed.Resolution)
	for _, want := range []string{"group-secret", "project-secret", "v1.0.0", "/api/v1/node/tasks/" + task.ID + "/package"} {
		if !strings.Contains(string(data), want) {
			t.Fatal("missing frozen value", want, string(data))
		}
	}
	if strings.Contains(string(data), "new-secret") || strings.Contains(string(data), "v2.0.0") {
		t.Fatal("snapshot re-resolved")
	}
	// Reconstruct a Store to simulate a control-server restart with durable data.
	restarted := New(s.pool, s.cipher)
	again, err := restarted.PollNode(ctx, n.ID)
	if err != nil || again.ID != task.ID {
		t.Fatal("lost durable claim", err)
	}
	material, err := restarted.NodeTaskMaterial(ctx, n.ID, task.ID)
	if err != nil || material.Version != "v1.0.0" {
		t.Fatal("unpinned package", err)
	}
	lists, err := restarted.ListTasks(ctx, "notes")
	if err != nil {
		t.Fatal(err)
	}
	public, _ := json.Marshal(lists)
	if strings.Contains(string(public), "secret") {
		t.Fatal("list leaked snapshot")
	}
}

func TestRemoteArchivedProjectDoesNotTrapNodeIdentity(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "one")
	task := remoteTask(t, s, n, "prod")
	n.Enabled = false
	if _, err := s.UpdateNode(ctx, n.ID, n.NodeInput, "owner"); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteProject(ctx, "notes", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.RotateNodeEnrollment(ctx, n.ID, "owner"); err != nil {
		t.Fatal("orphaned queue blocks rotation", err)
	}
	tasks, err := s.ListTasks(ctx, "notes")
	if err != nil || len(tasks) != 1 || tasks[0].ID != task.ID || tasks[0].Status != "cancelled" {
		t.Fatal(tasks, err)
	}
}

func TestRemoteEnrollmentRefusesDisabledValidActiveTask(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "one")
	remoteTask(t, s, n, "prod")
	n.Enabled = false
	if _, err := s.UpdateNode(ctx, n.ID, n.NodeInput, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.RotateNodeEnrollment(ctx, n.ID, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("active task rotation", err)
	}
}

func TestRemoteConcurrentAndExpiredEnrollment(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n, once, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "one", Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	results := make(chan error, 8)
	gate := make(chan struct{})
	for range 8 {
		go func() { <-gate; _, _, err := s.EnrollNode(ctx, auth.HashToken(once), "test", "linux"); results <- err }()
	}
	close(gate)
	success := 0
	for range 8 {
		err := <-results
		if err == nil {
			success++
		} else if !errors.Is(err, domain.ErrUnauthorized) {
			t.Fatal(err)
		}
	}
	if success != 1 {
		t.Fatal("enrollment winners", success)
	}
	expired, _, err := s.RotateNodeEnrollment(ctx, n.ID, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, "UPDATE ctl_nodes SET enrollment_expires=now()-interval '1 second' WHERE id=$1", n.ID); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(expired), "test", "linux"); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("expired enrollment accepted", err)
	}
}

func TestRemoteIdempotentRetryStillRequiresProjectPermission(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "one")
	for _, group := range []string{"alpha", "beta"} {
		if _, err := s.CreateGroup(ctx, domain.Group{Slug: group, Name: group}, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.MoveProjectGroup(ctx, "notes", "default", "alpha", "owner"); err != nil {
		t.Fatal(err)
	}
	raw, _ := auth.NewToken()
	if _, err := s.CreateReadableToken(ctx, domain.Token{Name: "operator", Role: "deployer", Groups: []string{"alpha"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner"); err != nil {
		t.Fatal(err)
	}
	principal, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil {
		t.Fatal(err)
	}
	req := domain.TaskRequest{NodeID: n.ID, Environment: "prod", Action: "rollback", RequestID: domain.NewID()}
	if _, err = s.CreateTask(ctx, "notes", req, principal); err != nil {
		t.Fatal(err)
	}
	if _, err = s.MoveProjectGroup(ctx, "notes", "alpha", "beta", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CreateTask(ctx, "notes", req, principal); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("stale retry accessed task", err)
	}
}
