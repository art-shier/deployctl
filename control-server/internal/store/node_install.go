package store

import (
	"context"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/nodeinstall"
	"github.com/jackc/pgx/v5"
)

type NodeInstallation struct {
	ID          string    `json:"id"`
	Host        string    `json:"host"`
	Port        int       `json:"port"`
	User        string    `json:"user"`
	Fingerprint string    `json:"fingerprint"`
	Status      string    `json:"status"`
	Stage       string    `json:"stage"`
	ErrorCode   string    `json:"error_code"`
	NodeID      string    `json:"node_id"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	ExpiresAt   time.Time `json:"expires_at"`
}

const installColumns = `id,host,port,username,fingerprint,status,stage,error_code,node_id,created_at,updated_at,expires_at`

func scanInstallation(row pgx.Row) (j NodeInstallation, err error) {
	err = row.Scan(&j.ID, &j.Host, &j.Port, &j.User, &j.Fingerprint, &j.Status, &j.Stage, &j.ErrorCode, &j.NodeID, &j.CreatedAt, &j.UpdatedAt, &j.ExpiresAt)
	return j, mapped(err)
}
func (s *Store) ListNodeInstallations(ctx context.Context) ([]NodeInstallation, error) {
	// A crashed worker has no reusable credentials. Expiry is also cross-replica safe.
	if _, err := s.pool.Exec(ctx, `UPDATE ctl_node_installations SET status='failed',error_code='interrupted',updated_at=now() WHERE status='running' AND expires_at<now()`); err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `SELECT `+installColumns+` FROM ctl_node_installations ORDER BY created_at DESC LIMIT 50`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []NodeInstallation{}
	for rows.Next() {
		j, err := scanInstallation(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, rows.Err()
}
func (s *Store) GetNodeInstallation(ctx context.Context, id string) (NodeInstallation, error) {
	return scanInstallation(s.pool.QueryRow(ctx, `SELECT `+installColumns+` FROM ctl_node_installations WHERE id=$1`, id))
}
func (s *Store) CreateNodeInstallation(ctx context.Context, r nodeinstall.Request, actor string) (NodeInstallation, string, error) {
	var j NodeInstallation
	if r.Validate() != nil {
		return j, "", domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return j, "", err
	}
	defer tx.Rollback(ctx)
	// Serializes capacity and duplicate requests across all center replicas.
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(73912417)`); err != nil {
		return j, "", err
	}
	if _, err = tx.Exec(ctx, `UPDATE ctl_node_installations SET status='failed',error_code='interrupted',updated_at=now() WHERE status='running' AND expires_at<now()`); err != nil {
		return j, "", err
	}
	existing, e := scanInstallation(tx.QueryRow(ctx, `SELECT `+installColumns+` FROM ctl_node_installations WHERE id=$1`, r.ID))
	if e == nil {
		if existing.Host != r.Host || existing.Port != r.Port || existing.User != r.User || existing.Fingerprint != r.Fingerprint {
			return j, "", domain.ErrConflict
		}
		return existing, "", tx.Commit(ctx)
	}
	if e != domain.ErrNotFound {
		return j, "", e
	}
	var count int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM ctl_node_installations WHERE status='running'`).Scan(&count); err != nil {
		return j, "", err
	}
	if count >= 4 {
		return j, "", domain.ErrConflict
	}
	raw, err := auth.NewToken()
	if err != nil {
		return j, "", err
	}
	expires := time.Now().UTC().Add(25 * time.Minute)
	j, err = scanInstallation(tx.QueryRow(ctx, `INSERT INTO ctl_node_installations(id,host,port,username,fingerprint,actor,status,stage,enrollment_hash,expires_at) VALUES($1,$2,$3,$4,$5,$6,'running','connecting',$7,$8) RETURNING `+installColumns, r.ID, r.Host, r.Port, r.User, r.Fingerprint, actor, auth.HashToken(raw), expires))
	if err != nil {
		return j, "", mapped(err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO ctl_node_enrollments(hash,expires_at) VALUES($1,$2)`, auth.HashToken(raw), expires); err != nil {
		return j, "", err
	}
	if err = audit(ctx, tx, actor, "node.install.create", "", "", []string{r.ID}); err != nil {
		return j, "", err
	}
	return j, raw, tx.Commit(ctx)
}
func (s *Store) UpdateNodeInstallation(ctx context.Context, id, status, stage, code string) error {
	_, err := s.pool.Exec(ctx, `UPDATE ctl_node_installations SET status=$2,stage=$3,error_code=$4,updated_at=now() WHERE id=$1 AND status='running'`, id, status, stage, code)
	if err == nil && status != "running" {
		_, err = s.pool.Exec(ctx, `DELETE FROM ctl_node_enrollments WHERE hash=(SELECT enrollment_hash FROM ctl_node_installations WHERE id=$1)`, id)
	}
	return err
}
func (s *Store) InstallationHeartbeat(ctx context.Context, id string) (bool, error) {
	var ready bool
	err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM ctl_node_installations j JOIN ctl_nodes n ON n.id=j.node_id WHERE j.id=$1 AND n.registered AND (n.data->>'last_seen')::timestamptz > j.created_at AND (n.data->>'last_seen')::timestamptz > now()-interval '90 seconds')`, id).Scan(&ready)
	return ready, err
}
