package store

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

// Every update scheduler transaction takes this lock before any node row lock.
// PollNode shares it, making deployment and updater leases mutually exclusive.
func lockUpdateScheduler(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtext(current_database() || ':ctl-update-scheduler'))")
	return err
}

type updaterIdentityKey struct{}

func WithUpdaterIdentity(ctx context.Context, hash string) context.Context {
	return context.WithValue(ctx, updaterIdentityKey{}, hash)
}
func scanUpdater(row scanner) (domain.Updater, error) {
	var u domain.Updater
	var b []byte
	if err := row.Scan(&b); err != nil {
		return u, mapped(err)
	}
	err := json.Unmarshal(b, &u)
	if u.LastSeen != nil {
		u.Online = time.Since(*u.LastSeen) < 90*time.Second
	}
	return u, err
}
func scanUpdateJob(row scanner) (domain.UpdateJob, error) {
	var j domain.UpdateJob
	var b []byte
	if err := row.Scan(&b); err != nil {
		return j, mapped(err)
	}
	err := json.Unmarshal(b, &j)
	return j, err
}
func writeUpdater(ctx context.Context, tx pgx.Tx, u domain.Updater) error {
	u.Online = false
	b, _ := json.Marshal(u)
	_, err := tx.Exec(ctx, "UPDATE ctl_updaters SET data=$2 WHERE id=$1", u.ID, b)
	return err
}
func writeUpdateJob(ctx context.Context, tx pgx.Tx, j *domain.UpdateJob) error {
	j.UpdatedAt = time.Now().UTC()
	b, _ := json.Marshal(j)
	_, err := tx.Exec(ctx, "UPDATE ctl_update_jobs SET status=$2,data=$3 WHERE id=$1", j.ID, j.Status, b)
	return err
}
func lockUpdater(ctx context.Context, tx pgx.Tx, id string) (domain.Updater, error) {
	u, err := scanUpdater(tx.QueryRow(ctx, "SELECT data FROM ctl_updaters WHERE id=$1 FOR UPDATE", id))
	if err != nil {
		return u, err
	}
	if hash, ok := ctx.Value(updaterIdentityKey{}).(string); ok {
		var valid bool
		err = tx.QueryRow(ctx, "SELECT token_hash=$2 FROM ctl_updaters WHERE id=$1", id, hash).Scan(&valid)
		if err != nil {
			return u, err
		}
		if !valid {
			return u, domain.ErrUnauthorized
		}
	}
	return u, nil
}
func (s *Store) AuthenticateUpdater(ctx context.Context, hash string) (string, error) {
	var id string
	err := s.pool.QueryRow(ctx, "SELECT id FROM ctl_updaters WHERE token_hash=$1", hash).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", domain.ErrUnauthorized
	}
	return id, err
}
func updateTargetEnabled(ctx context.Context, tx pgx.Tx, u domain.Updater) (bool, error) {
	if u.Kind == "center" {
		return true, nil
	}
	n, err := lockRemoteNode(ctx, tx, u.TargetID, "UPDATE")
	if err != nil {
		return false, err
	}
	return n.Enabled && !n.PendingAuthorization, nil
}
func activeTargetUpdate(ctx context.Context, tx pgx.Tx, kind, target string) (bool, error) {
	var active bool
	err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_update_jobs WHERE kind=$1 AND target_id=$2 AND (status IN ('queued','leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL)))", kind, target).Scan(&active)
	return active, err
}
func (s *Store) CreateUpdateEnrollment(ctx context.Context, in domain.UpdateEnrollment, actor string) (string, time.Time, error) {
	var expiry time.Time
	if err := in.Validate(); err != nil {
		return "", expiry, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", expiry, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return "", expiry, err
	}
	if in.Kind == "node" {
		if _, err = lockRemoteNode(ctx, tx, in.TargetID, "UPDATE"); err != nil {
			return "", expiry, err
		}
	}
	active, err := activeTargetUpdate(ctx, tx, in.Kind, in.TargetID)
	if err != nil {
		return "", expiry, err
	}
	if active {
		return "", expiry, domain.ErrConflict
	}
	raw, err := auth.NewToken()
	if err != nil {
		return "", expiry, err
	}
	expiry = time.Now().UTC().Add(15 * time.Minute)
	_, err = tx.Exec(ctx, "INSERT INTO ctl_update_enrollments(hash,kind,target_id,expires_at) VALUES($1,$2,$3,$4) ON CONFLICT(kind,target_id) DO UPDATE SET hash=EXCLUDED.hash,expires_at=EXCLUDED.expires_at", auth.HashToken(raw), in.Kind, in.TargetID, expiry)
	if err != nil {
		return "", expiry, mapped(err)
	}
	if err = audit(ctx, tx, actor, "system_update.enrollment", "", "", []string{in.Kind, in.TargetID}); err != nil {
		return "", expiry, err
	}
	return raw, expiry, tx.Commit(ctx)
}
func (s *Store) EnrollUpdater(ctx context.Context, hash, helper, platform string, protocol int) (domain.Updater, string, error) {
	var u domain.Updater
	h := domain.UpdaterHeartbeat{HelperVersion: helper, Platform: platform, Protocol: protocol}
	if err := h.Validate(); err != nil {
		return u, "", err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return u, "", err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return u, "", err
	}
	var kind, target string
	err = tx.QueryRow(ctx, "SELECT kind,target_id FROM ctl_update_enrollments WHERE hash=$1 AND expires_at>now() FOR UPDATE", hash).Scan(&kind, &target)
	if errors.Is(err, pgx.ErrNoRows) {
		return u, "", domain.ErrUnauthorized
	}
	if err != nil {
		return u, "", err
	}
	if kind == "node" {
		if _, err = lockRemoteNode(ctx, tx, target, "UPDATE"); err != nil {
			return u, "", err
		}
	}
	active, err := activeTargetUpdate(ctx, tx, kind, target)
	if err != nil {
		return u, "", err
	}
	if active {
		return u, "", domain.ErrConflict
	}
	existing, err := scanUpdater(tx.QueryRow(ctx, "SELECT data FROM ctl_updaters WHERE kind=$1 AND target_id=$2 FOR UPDATE", kind, target))
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return u, "", err
	}
	id := existing.ID
	if id == "" {
		id = domain.NewID()
	}
	u = domain.Updater{ID: id, Kind: kind, TargetID: target, UpdaterHeartbeat: h}
	raw, err := auth.NewToken()
	if err != nil {
		return u, "", err
	}
	b, _ := json.Marshal(u)
	_, err = tx.Exec(ctx, "INSERT INTO ctl_updaters(id,kind,target_id,token_hash,data) VALUES($1,$2,$3,$4,$5) ON CONFLICT(kind,target_id) DO UPDATE SET token_hash=EXCLUDED.token_hash,data=EXCLUDED.data", u.ID, kind, target, auth.HashToken(raw), b)
	if err != nil {
		return u, "", mapped(err)
	}
	if _, err = tx.Exec(ctx, "DELETE FROM ctl_update_enrollments WHERE hash=$1", hash); err != nil {
		return u, "", err
	}
	return u, raw, tx.Commit(ctx)
}
func (s *Store) HeartbeatUpdater(ctx context.Context, id string, in domain.UpdaterHeartbeat) error {
	if err := in.Validate(); err != nil {
		return err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return err
	}
	u, err := lockUpdater(ctx, tx, id)
	if err != nil {
		return err
	}
	u.UpdaterHeartbeat = in
	now := time.Now().UTC()
	u.LastSeen = &now
	if err = writeUpdater(ctx, tx, u); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func updateJobs(ctx context.Context, tx pgx.Tx, request string) ([]domain.UpdateJob, error) {
	rows, err := tx.Query(ctx, "SELECT data FROM ctl_update_jobs WHERE request_id=$1 ORDER BY created_at,id", request)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.UpdateJob{}
	for rows.Next() {
		j, e := scanUpdateJob(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, j)
	}
	return out, rows.Err()
}
func (s *Store) CreateUpdateJobs(ctx context.Context, in domain.UpdateRequest, actor string, release func(context.Context, string) (domain.SystemRelease, error)) ([]domain.UpdateJob, error) {
	if err := in.Validate(); err != nil {
		return nil, err
	}
	normalized := in
	normalized.UpdaterIDs = slices.Clone(in.UpdaterIDs)
	slices.Sort(normalized.UpdaterIDs)
	b, _ := json.Marshal(normalized)
	hash := auth.HashToken(string(b))
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return nil, err
	}
	var oldHash string
	err = tx.QueryRow(ctx, "SELECT request_hash FROM ctl_update_requests WHERE request_id=$1", in.RequestID).Scan(&oldHash)
	if err == nil {
		if oldHash != hash {
			return nil, domain.ErrConflict
		}
		return updateJobs(ctx, tx, in.RequestID)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	updaters := []domain.Updater{}
	hasCenter := false
	for _, id := range in.UpdaterIDs {
		u, e := lockUpdater(ctx, tx, id)
		if e != nil {
			return nil, e
		}
		if !u.Ready || !u.Online || u.Protocol != 1 || !domain.UpdateVersionNewer(in.Version, u.CurrentVersion) {
			return nil, domain.ErrConflict
		}
		enabled, e := updateTargetEnabled(ctx, tx, u)
		if e != nil {
			return nil, e
		}
		if !enabled {
			return nil, domain.ErrForbidden
		}
		active, e := activeTargetUpdate(ctx, tx, u.Kind, u.TargetID)
		if e != nil {
			return nil, e
		}
		if active {
			return nil, domain.ErrConflict
		}
		hasCenter = hasCenter || u.Kind == "center"
		updaters = append(updaters, u)
	}
	if hasCenter && (len(updaters) != 1 || !in.BackupConfirmed || in.Parallelism != 1) {
		return nil, domain.ErrInvalid
	}
	var overlap bool
	err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_update_jobs WHERE (status IN ('queued','leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL)) AND ($1 OR kind='center'))", hasCenter).Scan(&overlap)
	if err != nil {
		return nil, err
	}
	if overlap {
		return nil, domain.ErrConflict
	}
	rel, err := release(ctx, in.Version)
	if err != nil {
		return nil, err
	}
	if rel.Version != in.Version || rel.Validate() != nil {
		return nil, domain.ErrInvalid
	}
	batch := domain.NewID()
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_update_requests(request_id,request_hash,batch_id) VALUES($1,$2,$3)", in.RequestID, hash, batch); err != nil {
		return nil, mapped(err)
	}
	jobs := []domain.UpdateJob{}
	for _, u := range updaters {
		now := time.Now().UTC()
		j := domain.UpdateJob{ID: domain.NewID(), RequestID: in.RequestID, BatchID: batch, UpdaterID: u.ID, Kind: u.Kind, TargetID: u.TargetID, Version: in.Version, FromVersion: u.CurrentVersion, CLISHA256: rel.CLISHA256, ServerSHA256: rel.ServerSHA256, Status: "queued", Stage: "queued", CurrentVersion: u.CurrentVersion, CreatedAt: now, UpdatedAt: now, Parallelism: in.Parallelism}
		b, _ = json.Marshal(j)
		_, err = tx.Exec(ctx, "INSERT INTO ctl_update_jobs(id,request_id,updater_id,kind,target_id,batch_id,status,created_at,data) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)", j.ID, j.RequestID, j.UpdaterID, j.Kind, j.TargetID, j.BatchID, j.Status, j.CreatedAt, b)
		if err != nil {
			return nil, mapped(err)
		}
		jobs = append(jobs, j)
	}
	if err = audit(ctx, tx, actor, "system_update.jobs", "", "", []string{batch, in.Version}); err != nil {
		return nil, err
	}
	return jobs, tx.Commit(ctx)
}
func (s *Store) ListSystemUpdates(ctx context.Context) ([]domain.Updater, []domain.UpdateJob, error) {
	updaters := []domain.Updater{}
	jobs := []domain.UpdateJob{}
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_updaters ORDER BY kind,target_id")
	if err != nil {
		return nil, nil, err
	}
	for rows.Next() {
		u, e := scanUpdater(rows)
		if e != nil {
			rows.Close()
			return nil, nil, e
		}
		updaters = append(updaters, u)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, nil, err
	}
	rows, err = s.pool.Query(ctx, `SELECT data FROM ctl_update_jobs
 WHERE status IN ('queued','leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL)
 OR id IN (SELECT id FROM ctl_update_jobs
 WHERE NOT (status IN ('queued','leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL))
 ORDER BY created_at DESC,id DESC LIMIT 200)
 ORDER BY created_at DESC,id DESC`)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	for rows.Next() {
		j, e := scanUpdateJob(rows)
		if e != nil {
			return nil, nil, e
		}
		jobs = append(jobs, j)
	}
	return updaters, jobs, rows.Err()
}
func (s *Store) CancelUpdateJob(ctx context.Context, id, actor string) (domain.UpdateJob, error) {
	var j domain.UpdateJob
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return j, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return j, err
	}
	j, err = scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE id=$1 FOR UPDATE", id))
	if err != nil {
		return j, err
	}
	if j.Status != "queued" {
		return j, domain.ErrConflict
	}
	j.Status = "cancelled"
	j.Stage = "completed"
	if err = writeUpdateJob(ctx, tx, &j); err != nil {
		return j, err
	}
	if err = audit(ctx, tx, actor, "system_update.cancel", "", "", []string{id}); err != nil {
		return j, err
	}
	return j, tx.Commit(ctx)
}
func (s *Store) PollUpdater(ctx context.Context, id string) (*domain.UpdateJob, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return nil, err
	}
	u, err := lockUpdater(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	j, err := scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE updater_id=$1 AND status IN ('leased','running')", id))
	if err == nil {
		return &j, tx.Commit(ctx)
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, err
	}
	if !u.Ready || !u.Online || u.Protocol != 1 {
		return nil, tx.Commit(ctx)
	}
	enabled, err := updateTargetEnabled(ctx, tx, u)
	if err != nil {
		return nil, err
	}
	if !enabled {
		return nil, tx.Commit(ctx)
	}
	j, err = scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE updater_id=$1 AND status='queued' ORDER BY created_at,id LIMIT 1 FOR UPDATE", id))
	if errors.Is(err, domain.ErrNotFound) {
		return nil, tx.Commit(ctx)
	}
	if err != nil {
		return nil, err
	}
	var busy bool
	err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_remote_tasks WHERE status IN ('leased','running') AND ($1='center' OR node_id=$2))", u.Kind, u.TargetID).Scan(&busy)
	if err != nil {
		return nil, err
	}
	if busy {
		return nil, tx.Commit(ctx)
	}
	var count int
	err = tx.QueryRow(ctx, "SELECT count(*) FROM ctl_update_jobs WHERE batch_id=$1 AND (status IN ('leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL))", j.BatchID).Scan(&count)
	if err != nil {
		return nil, err
	}
	if count >= j.Parallelism {
		return nil, tx.Commit(ctx)
	}
	j.Status = "leased"
	j.Stage = "waiting"
	if err = writeUpdateJob(ctx, tx, &j); err != nil {
		return nil, err
	}
	return &j, tx.Commit(ctx)
}
func (s *Store) StartUpdateJob(ctx context.Context, updater, id string) (domain.UpdateJob, error) {
	var j domain.UpdateJob
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return j, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return j, err
	}
	u, err := lockUpdater(ctx, tx, updater)
	if err != nil {
		return j, err
	}
	j, err = scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE id=$1 AND updater_id=$2 FOR UPDATE", id, updater))
	if err != nil {
		return j, err
	}
	if j.Status == "running" {
		return j, tx.Commit(ctx)
	}
	if j.Status != "leased" {
		return j, domain.ErrConflict
	}
	enabled, err := updateTargetEnabled(ctx, tx, u)
	if err != nil {
		return j, err
	}
	if !enabled || !u.Ready || !u.Online {
		return j, domain.ErrForbidden
	}
	j.Status = "running"
	j.Stage = "downloading"
	if err = writeUpdateJob(ctx, tx, &j); err != nil {
		return j, err
	}
	return j, tx.Commit(ctx)
}
func (s *Store) ReportUpdateJob(ctx context.Context, updater, id string, in domain.UpdateReport) (domain.UpdateJob, error) {
	var j domain.UpdateJob
	if err := in.Validate(); err != nil {
		return j, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return j, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return j, err
	}
	u, err := lockUpdater(ctx, tx, updater)
	if err != nil {
		return j, err
	}
	j, err = scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE id=$1 AND updater_id=$2 FOR UPDATE", id, updater))
	if err != nil {
		return j, err
	}
	if domain.UpdateTerminal(j.Status) {
		if j.Status != in.Status || j.Stage != in.Stage || j.ResultCode != in.ResultCode || j.CurrentVersion != in.CurrentVersion {
			return j, domain.ErrConflict
		}
		return j, tx.Commit(ctx)
	}
	if j.Status != "running" && !(j.Status == "leased" && in.Status == "needs_attention" && in.ResultCode == "interrupted") {
		return j, domain.ErrConflict
	}
	if in.Status == "succeeded" && in.CurrentVersion != j.Version {
		return j, domain.ErrInvalid
	}
	if in.Status == "rolled_back" && in.CurrentVersion != j.FromVersion {
		return j, domain.ErrInvalid
	}
	j.Status = in.Status
	j.Stage = in.Stage
	j.ResultCode = in.ResultCode
	j.CurrentVersion = in.CurrentVersion
	if err = writeUpdateJob(ctx, tx, &j); err != nil {
		return j, err
	}
	if domain.UpdateTerminal(in.Status) {
		u.CurrentVersion = in.CurrentVersion
		if in.Status == "needs_attention" {
			u.Ready = false
			u.BlockedReason = "pending_operation"
		}
		if err = writeUpdater(ctx, tx, u); err != nil {
			return j, err
		}
	}
	return j, tx.Commit(ctx)
}
func updateBlocksNode(ctx context.Context, tx pgx.Tx, node string) (bool, error) {
	var blocked bool
	err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_update_jobs WHERE (status IN ('queued','leased','running') OR (status='needs_attention' AND data->>'resolved_at' IS NULL)) AND (kind='center' OR target_id=$1))", node).Scan(&blocked)
	return blocked, err
}

// ResolveUpdateJob acknowledges local repair without changing the terminal report.
func (s *Store) ResolveUpdateJob(ctx context.Context, id, actor string) (domain.UpdateJob, error) {
	var j domain.UpdateJob
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return j, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return j, err
	}
	j, err = scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE id=$1 FOR UPDATE", id))
	if err != nil {
		return j, err
	}
	if j.Status != "needs_attention" {
		return j, domain.ErrConflict
	}
	if j.ResolvedAt != nil {
		return j, tx.Commit(ctx)
	}
	u, err := lockUpdater(ctx, tx, j.UpdaterID)
	if err != nil {
		return j, err
	}
	if !u.Ready || !u.Online || u.Protocol != 1 || u.LastSeen == nil || !u.LastSeen.After(j.UpdatedAt) || (u.CurrentVersion != j.Version && u.CurrentVersion != j.FromVersion) {
		return j, domain.ErrConflict
	}
	enabled, err := updateTargetEnabled(ctx, tx, u)
	if err != nil {
		return j, err
	}
	if !enabled {
		return j, domain.ErrForbidden
	}
	now := time.Now().UTC()
	j.ResolvedAt = &now
	b, _ := json.Marshal(j)
	if _, err = tx.Exec(ctx, "UPDATE ctl_update_jobs SET data=$2 WHERE id=$1", j.ID, b); err != nil {
		return j, err
	}
	if err = audit(ctx, tx, actor, "system_update.resolve", "", "", []string{id}); err != nil {
		return j, err
	}
	return j, tx.Commit(ctx)
}

func (s *Store) UpdateJobMaterial(ctx context.Context, updater, id string) (domain.UpdateJob, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return domain.UpdateJob{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = lockUpdater(ctx, tx, updater); err != nil {
		return domain.UpdateJob{}, err
	}
	job, err := scanUpdateJob(tx.QueryRow(ctx, "SELECT data FROM ctl_update_jobs WHERE id=$1 AND updater_id=$2", id, updater))
	if err != nil {
		return job, err
	}
	if job.Status != "running" {
		return job, domain.ErrForbidden
	}
	return job, tx.Commit(ctx)
}
