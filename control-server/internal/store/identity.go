package store

import (
	"context"
	"encoding/json"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Store) CreateToken(ctx context.Context, t domain.Token, hash, actor string) (domain.Token, error) {
	if t.Name == "" || len(t.Name) > 128 || domain.ValidateName(t.Project, 48) != nil || len(t.Environments) > 128 || (t.Role != "publisher" && t.Role != "deployer") || len(hash) != 64 || !t.ExpiresAt.After(time.Now()) || t.ExpiresAt.After(time.Now().Add(366*24*time.Hour)) {
		return t, domain.ErrInvalid
	}
	if t.Role == "deployer" && len(t.Environments) == 0 {
		return t, domain.ErrInvalid
	}
	seen := map[string]bool{}
	for _, env := range t.Environments {
		if domain.ValidateName(env, 32) != nil || seen[env] {
			return t, domain.ErrInvalid
		}
		seen[env] = true
	}
	t.ID = domain.NewID()
	t.CreatedAt = time.Now().UTC()
	t.Revoked = false
	b, _ := json.Marshal(t)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return t, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_tokens(id,hash,project,expires_at,data) VALUES($1,$2,$3,$4,$5)", t.ID, hash, t.Project, t.ExpiresAt, b); err != nil {
		return t, mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.create", t.Project, "", nil); err != nil {
		return t, err
	}
	return t, tx.Commit(ctx)
}
func (s *Store) Authenticate(ctx context.Context, hash string) (auth.Principal, error) {
	p := auth.Principal{}
	var b []byte
	if err := s.pool.QueryRow(ctx, "SELECT data FROM ctl_tokens WHERE hash=$1 AND NOT revoked AND expires_at>now()", hash).Scan(&b); err != nil {
		return p, domain.ErrUnauthorized
	}
	var t domain.Token
	if err := json.Unmarshal(b, &t); err != nil {
		return p, err
	}
	return auth.Principal{ID: t.ID, Role: t.Role, Project: t.Project, Environments: t.Environments}, nil
}
func (s *Store) RevokeToken(ctx context.Context, id, actor string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var project string
	if err = tx.QueryRow(ctx, "UPDATE ctl_tokens SET revoked=true WHERE id=$1 RETURNING project", id).Scan(&project); err != nil {
		return mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.revoke", project, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) ListTokens(ctx context.Context) ([]domain.Token, error) {
	rows, err := s.pool.Query(ctx, "SELECT data,revoked FROM ctl_tokens ORDER BY data->>'created_at' DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Token{}
	for rows.Next() {
		var b []byte
		var revoked bool
		if err = rows.Scan(&b, &revoked); err != nil {
			return nil, err
		}
		var t domain.Token
		if err = json.Unmarshal(b, &t); err != nil {
			return nil, err
		}
		t.Revoked = revoked
		out = append(out, t)
	}
	return out, rows.Err()
}
func (s *Store) CreateSession(ctx context.Context, hash string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "DELETE FROM ctl_sessions WHERE expires_at<now()"); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_sessions(hash,expires_at) VALUES($1,$2)", hash, time.Now().Add(8*time.Hour)); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) SessionValid(ctx context.Context, hash string) bool {
	var found bool
	err := s.pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_sessions WHERE hash=$1 AND expires_at>now())", hash).Scan(&found)
	return err == nil && found
}
func (s *Store) DeleteSession(ctx context.Context, hash string) error {
	_, err := s.pool.Exec(ctx, "DELETE FROM ctl_sessions WHERE hash=$1", hash)
	return err
}
func (s *Store) SaveReceipt(ctx context.Context, r domain.Receipt) error {
	if domain.ValidateName(r.Project, 48) != nil || domain.ValidateName(r.Environment, 32) != nil || len(r.HostID) != 32 || len(r.ReleaseID) != 32 || len(r.RevisionID) != 32 || len(r.CLIVersion) > 96 {
		return domain.ErrInvalid
	}
	if r.ID == "" {
		r.ID = domain.NewID()
	}
	r.CreatedAt = time.Now().UTC()
	b, _ := json.Marshal(r)
	_, err := s.pool.Exec(ctx, "INSERT INTO ctl_receipts(id,project,data,created_at) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING", r.ID, r.Project, b, r.CreatedAt)
	return err
}
func (s *Store) ListReceipts(ctx context.Context, project string) ([]domain.Receipt, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_receipts WHERE project=$1 ORDER BY created_at DESC LIMIT 200", project)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Receipt{}
	for rows.Next() {
		var b []byte
		if err = rows.Scan(&b); err != nil {
			return nil, err
		}
		var r domain.Receipt
		if err = json.Unmarshal(b, &r); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
func (s *Store) ListAudit(ctx context.Context) ([]domain.AuditEvent, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_audit ORDER BY created_at DESC LIMIT 200")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.AuditEvent{}
	for rows.Next() {
		var b []byte
		if err = rows.Scan(&b); err != nil {
			return nil, err
		}
		var a domain.AuditEvent
		if err = json.Unmarshal(b, &a); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}
