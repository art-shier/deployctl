package store

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func updateFixture(t *testing.T, s *Store, kind, target string) (domain.Updater, string) {
	t.Helper()
	ctx := context.Background()
	token, _, err := s.CreateUpdateEnrollment(ctx, domain.UpdateEnrollment{Kind: kind, TargetID: target}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	u, raw, err := s.EnrollUpdater(ctx, auth.HashToken(token), "v1.15.0", "linux", 1)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollUpdater(ctx, auth.HashToken(token), "v1.15.0", "linux", 1); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("enrollment replay", err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(raw)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("updater is owner", err)
	}
	if err = s.HeartbeatUpdater(ctx, u.ID, domain.UpdaterHeartbeat{CurrentVersion: "v1.14.0", HelperVersion: "v1.15.0", Platform: "linux", Protocol: 1, Ready: true}); err != nil {
		t.Fatal(err)
	}
	return u, raw
}
func updateRelease() domain.SystemRelease {
	return domain.SystemRelease{Version: "v1.16.0", PublishedAt: time.Now().UTC(), CLISHA256: strings.Repeat("a", 64), ServerSHA256: strings.Repeat("b", 64)}
}
func TestNodeUpdaterEnrollmentRevalidatesRevokedIdentityAndActiveJob(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n, invitation, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "node", Enabled: true}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, token, err := s.EnrollNode(ctx, auth.HashToken(invitation), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	stale := WithNodeIdentity(ctx, auth.HashToken(token))
	u, _ := updateFixture(t, s, "node", n.ID)
	_, err = s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.CreateUpdateEnrollment(stale, domain.UpdateEnrollment{Kind: "node", TargetID: n.ID}, n.ID); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("active update enrollment", err)
	}
	// A request authenticated before rotation must recheck the credential under
	// the scheduler and node locks, even when an active update would conflict.
	if _, _, err = s.RotateNodeEnrollment(ctx, n.ID, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.CreateUpdateEnrollment(stale, domain.UpdateEnrollment{Kind: "node", TargetID: n.ID}, n.ID); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("revoked request enrolled updater", err)
	}
}
func TestUpdateLifecycleFrozenRetryAndTerminalReplay(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, raw := updateFixture(t, s, "center", "center")
	if id, err := s.AuthenticateUpdater(ctx, auth.HashToken(raw)); err != nil || id != u.ID {
		t.Fatal(id, err)
	}
	req := domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}
	calls := 0
	fetch := func(context.Context, string) (domain.SystemRelease, error) { calls++; return updateRelease(), nil }
	jobs, err := s.CreateUpdateJobs(ctx, req, "owner", fetch)
	if err != nil || len(jobs) != 1 {
		t.Fatal(jobs, err)
	}
	same, err := s.CreateUpdateJobs(ctx, req, "owner", fetch)
	if err != nil || same[0].ID != jobs[0].ID || calls != 1 {
		t.Fatal("frozen retry", same, calls, err)
	}
	req.Parallelism = 2
	if _, err = s.CreateUpdateJobs(ctx, req, "owner", fetch); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("changed retry", err)
	}
	job, err := s.PollUpdater(ctx, u.ID)
	if err != nil || job == nil || job.Status != "leased" {
		t.Fatal(job, err)
	}
	again, err := s.PollUpdater(ctx, u.ID)
	if err != nil || again.ID != job.ID {
		t.Fatal(again, err)
	}
	if _, err = s.CancelUpdateJob(ctx, job.ID, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("cancel lease", err)
	}
	if _, _, err = s.CreateUpdateEnrollment(ctx, domain.UpdateEnrollment{Kind: "center", TargetID: "center"}, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("rotation active", err)
	}
	if _, err = s.StartUpdateJob(ctx, u.ID, job.ID); err != nil {
		t.Fatal(err)
	}

	if material, e := s.UpdateJobMaterial(WithUpdaterIdentity(ctx, auth.HashToken(raw)), u.ID, job.ID); e != nil || material.ServerSHA256 != job.ServerSHA256 {
		t.Fatal("own package unavailable", e)
	}
	if _, e := s.UpdateJobMaterial(ctx, domain.NewID(), job.ID); e == nil {
		t.Fatal("foreign updater obtained package")
	}
	if _, e := s.UpdateJobMaterial(WithUpdaterIdentity(ctx, auth.HashToken("wrong")), u.ID, job.ID); !errors.Is(e, domain.ErrUnauthorized) {
		t.Fatal("revoked token obtained package", e)
	}
	report := domain.UpdateReport{Status: "succeeded", Stage: "completed", ResultCode: "ok", CurrentVersion: "v1.16.0"}
	if _, err = s.ReportUpdateJob(ctx, u.ID, job.ID, report); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportUpdateJob(ctx, u.ID, job.ID, report); err != nil {
		t.Fatal("report replay", err)
	}
	if _, e := s.UpdateJobMaterial(ctx, u.ID, job.ID); !errors.Is(e, domain.ErrForbidden) {
		t.Fatal("terminal job obtained package", e)
	}
	report.Status = "failed"
	if _, err = s.ReportUpdateJob(ctx, u.ID, job.ID, report); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("terminal changed", err)
	}
	token, _, err := s.CreateUpdateEnrollment(ctx, domain.UpdateEnrollment{Kind: "center", TargetID: "center"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollUpdater(ctx, auth.HashToken(token), "v1.15.0", "linux", 1); err != nil {
		t.Fatal(err)
	}
	if _, err = s.AuthenticateUpdater(ctx, auth.HashToken(raw)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("old credential", err)
	}
}
func TestUpdateNodeLeaseConcurrencyAndDisabledGate(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	ids := []string{}
	nodes := []domain.Node{}
	for range 2 {
		n, _, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
		if err != nil {
			t.Fatal(err)
		}
		u, _ := updateFixture(t, s, "node", n.ID)
		ids = append(ids, u.ID)
		nodes = append(nodes, n)
	}
	req := domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: ids, Version: "v1.16.0", Parallelism: 1}
	jobs, err := s.CreateUpdateJobs(ctx, req, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if j, err := s.PollUpdater(ctx, ids[0]); err != nil || j == nil {
		t.Fatal(j, err)
	}
	if j, err := s.PollUpdater(ctx, ids[1]); err != nil || j != nil {
		t.Fatal("parallel cap", j, err)
	}
	if _, err = s.StartUpdateJob(ctx, ids[0], jobs[0].ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportUpdateJob(ctx, ids[0], jobs[0].ID, domain.UpdateReport{Status: "failed", Stage: "failed", ResultCode: "download_failed", CurrentVersion: "v1.14.0"}); err != nil {
		t.Fatal(err)
	}
	in := nodes[1].NodeInput
	in.Enabled = false
	if _, err = s.UpdateNode(ctx, nodes[1].ID, in, "owner"); err != nil {
		t.Fatal(err)
	}
	if j, err := s.PollUpdater(ctx, ids[1]); err != nil || j != nil {
		t.Fatal("disabled", j, err)
	}
}

func TestUpdateSuspendsBusinessLeaseAndDrainsExistingTask(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	owner := auth.Principal{ID: "owner", Role: "owner"}
	n, _, _, err := s.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	u, _ := updateFixture(t, s, "node", n.ID)
	req := domain.TaskRequest{RequestID: domain.NewID(), NodeID: n.ID, Environment: "prod", Action: "rollback"}
	if _, err = s.CreateTask(ctx, "notes", req, owner); err != nil {
		t.Fatal(err)
	}
	_, err = s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if business, err := s.PollNode(ctx, n.ID); err != nil || business != nil {
		t.Fatal("update must suspend new business lease", business, err)
	}
	if job, err := s.PollUpdater(ctx, u.ID); err != nil || job == nil {
		t.Fatal("updater lease", job, err)
	}
}

func TestUpdateNeedsAttentionRequiresExplicitFreshRecovery(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, _ := updateFixture(t, s, "center", "center")
	req := domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}
	jobs, err := s.CreateUpdateJobs(ctx, req, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PollUpdater(ctx, u.ID); err != nil {
		t.Fatal(err)
	}
	report := domain.UpdateReport{Status: "needs_attention", Stage: "interrupted", ResultCode: "interrupted", CurrentVersion: ""}
	if _, err = s.ReportUpdateJob(ctx, u.ID, jobs[0].ID, report); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ResolveUpdateJob(ctx, jobs[0].ID, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("stale recovery", err)
	}
	if err = s.HeartbeatUpdater(ctx, u.ID, domain.UpdaterHeartbeat{CurrentVersion: "v1.14.0", HelperVersion: "v1.15.0", Platform: "linux", Protocol: 1, Ready: true}); err != nil {
		t.Fatal(err)
	}
	j, err := s.ResolveUpdateJob(ctx, jobs[0].ID, "owner")
	if err != nil || j.ResolvedAt == nil || j.Status != "needs_attention" {
		t.Fatal(j, err)
	}
	if _, err = s.ResolveUpdateJob(ctx, jobs[0].ID, "owner"); err != nil {
		t.Fatal("resolve replay", err)
	}
	if _, err = s.ReportUpdateJob(ctx, u.ID, jobs[0].ID, report); err != nil {
		t.Fatal("report preserved", err)
	}
	req.RequestID = domain.NewID()
	if _, err = s.CreateUpdateJobs(ctx, req, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil }); err != nil {
		t.Fatal("follow-up after repair", err)
	}
}

func TestUpdateRejectsInvalidFrozenDigest(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, _ := updateFixture(t, s, "center", "center")
	_, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}, "owner", func(context.Context, string) (domain.SystemRelease, error) {
		rel := updateRelease()
		rel.CLISHA256 = strings.Repeat("z", 64)
		return rel, nil
	})
	if !errors.Is(err, domain.ErrInvalid) {
		t.Fatal("non-hex digest frozen", err)
	}
}

func TestUpdateDrainsRunningBusinessAndCenterBlocksGlobally(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "drain")
	business := remoteTask(t, s, n, "prod")
	if task, err := s.PollNode(ctx, n.ID); err != nil || task == nil {
		t.Fatal(task, err)
	}
	if _, err := s.StartNodeTask(ctx, n.ID, business.ID); err != nil {
		t.Fatal(err)
	}
	center, _ := updateFixture(t, s, "center", "center")
	jobs, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{center.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if job, err := s.PollUpdater(ctx, center.ID); err != nil || job != nil {
		t.Fatal("must drain business", job, err)
	}
	if task, err := s.PollNode(ctx, n.ID); err != nil || task == nil || task.ID != business.ID {
		t.Fatal("existing business may finish", task, err)
	}
	if _, err = s.ReportNodeTask(ctx, n.ID, business.ID, domain.TaskReport{Status: "failed", Stage: "execution_failed"}); err != nil {
		t.Fatal(err)
	}
	another := remoteNode(t, s, "blocked")
	queued := remoteTask(t, s, another, "prod")
	if task, err := s.PollNode(ctx, another.ID); err != nil || task != nil {
		t.Fatal("global suspension", task, err)
	}
	if job, err := s.PollUpdater(ctx, center.ID); err != nil || job == nil {
		t.Fatal("drained", job, err)
	}
	if _, err = s.StartUpdateJob(ctx, center.ID, jobs[0].ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportUpdateJob(ctx, center.ID, jobs[0].ID, domain.UpdateReport{Status: "failed", Stage: "failed", ResultCode: "download_failed", CurrentVersion: "v1.14.0"}); err != nil {
		t.Fatal(err)
	}
	if task, err := s.PollNode(ctx, another.ID); err != nil || task == nil || task.ID != queued.ID {
		t.Fatal("resume after update", task, err)
	}
}

func TestUpdateConcurrentEnrollmentAndRequestRetry(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	token, _, err := s.CreateUpdateEnrollment(ctx, domain.UpdateEnrollment{Kind: "center", TargetID: "center"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	type enrollment struct {
		u   domain.Updater
		err error
	}
	results := make(chan enrollment, 6)
	gate := make(chan struct{})
	for range 6 {
		go func() {
			<-gate
			u, _, e := s.EnrollUpdater(ctx, auth.HashToken(token), "v1.15.0", "linux", 1)
			results <- enrollment{u, e}
		}()
	}
	close(gate)
	var updater domain.Updater
	success := 0
	for range 6 {
		r := <-results
		if r.err == nil {
			success++
			updater = r.u
		} else if !errors.Is(r.err, domain.ErrUnauthorized) {
			t.Fatal(r.err)
		}
	}
	if success != 1 {
		t.Fatal("token exchange", success)
	}
	if err = s.HeartbeatUpdater(ctx, updater.ID, domain.UpdaterHeartbeat{CurrentVersion: "v1.14.0", HelperVersion: "v1.15.0", Platform: "linux", Protocol: 1, Ready: true}); err != nil {
		t.Fatal(err)
	}
	req := domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{updater.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}
	type result struct {
		jobs []domain.UpdateJob
		err  error
	}
	jobs := make(chan result, 6)
	gate = make(chan struct{})
	var calls atomic.Int32
	for range 6 {
		go func() {
			<-gate
			j, e := s.CreateUpdateJobs(ctx, req, "owner", func(context.Context, string) (domain.SystemRelease, error) { calls.Add(1); return updateRelease(), nil })
			jobs <- result{j, e}
		}()
	}
	close(gate)
	id := ""
	for range 6 {
		r := <-jobs
		if r.err != nil || len(r.jobs) != 1 {
			t.Fatal(r.jobs, r.err)
		}
		if id != "" && r.jobs[0].ID != id {
			t.Fatal("duplicate request jobs")
		}
		id = r.jobs[0].ID
	}
	if calls.Load() != 1 {
		t.Fatal("refetch", calls.Load())
	}
}

func TestUpdateCreationAndBusinessLeaseRace(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	for range 8 {
		n := remoteNode(t, s, "race")
		business := remoteTask(t, s, n, "prod")
		u, _ := updateFixture(t, s, "node", n.ID)
		gate := make(chan struct{})
		businessResult := make(chan *domain.AgentTask, 1)
		errorsResult := make(chan error, 2)
		go func() { <-gate; task, err := s.PollNode(ctx, n.ID); businessResult <- task; errorsResult <- err }()
		go func() {
			<-gate
			_, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
			errorsResult <- err
		}()
		close(gate)
		task := <-businessResult
		for range 2 {
			if err := <-errorsResult; err != nil {
				t.Fatal(err)
			}
		}
		job, err := s.PollUpdater(ctx, u.ID)
		if err != nil {
			t.Fatal(err)
		}
		if task != nil {
			if task.ID != business.ID || job != nil {
				t.Fatal("overlapping lease", task, job)
			}
		} else if job == nil {
			t.Fatal("neither lease granted")
		}
	}
}

func TestUpdateCenterAndNodeJobsAreMutuallyExclusive(t *testing.T) {
	for _, first := range []string{"node", "center"} {
		t.Run(first, func(t *testing.T) {
			s := fixture(t)
			ctx := context.Background()
			n := remoteNode(t, s, "node")
			node, _ := updateFixture(t, s, "node", n.ID)
			center, _ := updateFixture(t, s, "center", "center")
			one, two := node, center
			if first == "center" {
				one, two = center, node
			}
			create := func(u domain.Updater) ([]domain.UpdateJob, error) {
				return s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
			}
			jobs, err := create(one)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = create(two); !errors.Is(err, domain.ErrConflict) {
				t.Fatal("overlapping center/node queue", err)
			}
			if _, err = s.CancelUpdateJob(ctx, jobs[0].ID, "owner"); err != nil {
				t.Fatal(err)
			}
			if _, err = create(two); err != nil {
				t.Fatal("queue after cancellation", err)
			}
		})
	}
}

func TestUpdateEnrollmentExpiryPendingGateAndSecretFreeAudit(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	n := remoteNode(t, s, "node")
	u, credential := updateFixture(t, s, "node", n.ID)
	// A pending node must not gain update access even if its helper is healthy.
	if _, err := s.pool.Exec(ctx, "UPDATE ctl_nodes SET data=jsonb_set(data,'{pending_authorization}','true') WHERE id=$1", n.ID); err != nil {
		t.Fatal(err)
	}
	_, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("pending node", err)
	}
	invitation, _, err := s.CreateUpdateEnrollment(ctx, domain.UpdateEnrollment{Kind: "center", TargetID: "center"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, "UPDATE ctl_update_enrollments SET expires_at=now()-interval '1 second' WHERE hash=$1", auth.HashToken(invitation)); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollUpdater(ctx, auth.HashToken(invitation), "v1.15.0", "linux", 1); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("expired enrollment", err)
	}
	audit, err := s.ListAudit(ctx)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(audit)
	if strings.Contains(string(raw), credential) || strings.Contains(string(raw), invitation) {
		t.Fatal("credential in audit")
	}
}

func TestUpdateResolveRejectsUnrelatedObservedVersion(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, _ := updateFixture(t, s, "center", "center")
	jobs, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PollUpdater(ctx, u.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportUpdateJob(ctx, u.ID, jobs[0].ID, domain.UpdateReport{Status: "needs_attention", Stage: "interrupted", ResultCode: "interrupted", CurrentVersion: ""}); err != nil {
		t.Fatal(err)
	}
	if err = s.HeartbeatUpdater(ctx, u.ID, domain.UpdaterHeartbeat{CurrentVersion: "v1.15.0", HelperVersion: "v1.15.0", Platform: "linux", Protocol: 1, Ready: true}); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ResolveUpdateJob(ctx, jobs[0].ID, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("unrelated recovered version", err)
	}
}

func TestUpdateHistoryRetainsOldUnresolvedBlockers(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	u, _ := updateFixture(t, s, "center", "center")
	jobs, err := s.CreateUpdateJobs(ctx, domain.UpdateRequest{RequestID: domain.NewID(), UpdaterIDs: []string{u.ID}, Version: "v1.16.0", Parallelism: 1, BackupConfirmed: true}, "owner", func(context.Context, string) (domain.SystemRelease, error) { return updateRelease(), nil })
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PollUpdater(ctx, u.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReportUpdateJob(ctx, u.ID, jobs[0].ID, domain.UpdateReport{Status: "needs_attention", Stage: "interrupted", ResultCode: "interrupted", CurrentVersion: ""}); err != nil {
		t.Fatal(err)
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	for i := range 205 {
		j := jobs[0]
		j.ID = domain.NewID()
		j.Status = "failed"
		j.CreatedAt = time.Now().UTC().Add(time.Duration(i) * time.Second)
		b, _ := json.Marshal(j)
		if _, err = tx.Exec(ctx, "INSERT INTO ctl_update_jobs(id,request_id,updater_id,kind,target_id,batch_id,status,created_at,data) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)", j.ID, j.RequestID, j.UpdaterID, j.Kind, j.TargetID, j.BatchID, j.Status, j.CreatedAt, b); err != nil {
			t.Fatal(err)
		}
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	_, history, err := s.ListSystemUpdates(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(history) != 201 || history[len(history)-1].ID != jobs[0].ID {
		t.Fatal("unresolved blocker hidden by history limit", len(history))
	}
}
