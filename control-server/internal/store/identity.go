package store

import (
	"context"
	"encoding/json"
	"slices"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Store) CreateToken(ctx context.Context, t domain.Token, hash, actor string) (domain.Token, error) {
	return s.createToken(ctx, t, hash, "", actor)
}
func (s *Store) CreateReadableToken(ctx context.Context, t domain.Token, raw, actor string) (domain.Token, error) {
	return s.createToken(ctx, t, auth.HashToken(raw), raw, actor)
}
func (s *Store) createToken(ctx context.Context, t domain.Token, hash, raw, actor string) (domain.Token, error) {
	if t.Name == "" || len(t.Name) > 128 || len(t.Projects)+len(t.Groups) > 128 || len(t.Environments) > 128 || (t.Role != "publisher" && t.Role != "deployer") || len(hash) != 64 || !t.ExpiresAt.After(time.Now()) || t.ExpiresAt.After(time.Now().Add(366*24*time.Hour)) {
		return t, domain.ErrInvalid
	}
	if t.Role == "deployer" && len(t.Environments) == 0 {
		return t, domain.ErrInvalid
	}
	projects := append([]string{}, t.Projects...)
	if t.Project != "" {
		projects = append(projects, t.Project)
	}
	if len(projects)+len(t.Groups) == 0 || len(projects)+len(t.Groups) > 128 {
		return t, domain.ErrInvalid
	}
	if len(t.ExcludedProjects) > 128 {
		return t, domain.ErrInvalid
	}
	for _, scope := range [][]string{projects, t.Groups, t.ExcludedProjects} {
		seen := map[string]bool{}
		for _, id := range scope {
			if domain.ValidateName(id, 48) != nil || seen[id] {
				return t, domain.ErrInvalid
			}
			seen[id] = true
		}
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
	t.TokenReadable = raw != ""
	var encrypted []byte
	if raw != "" {
		if !strings.HasPrefix(raw, "ctl_") || len(raw) > 4096 {
			return t, domain.ErrInvalid
		}
		var err error
		encrypted, err = s.cipher.Seal([]byte(raw), "ctl-token/"+t.ID)
		if err != nil {
			return t, err
		}
	}
	b, _ := json.Marshal(t)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return t, err
	}
	defer tx.Rollback(ctx)
	// Validate every scope, including empty groups, before persisting any credential.
	for _, id := range append(append([]string{}, projects...), t.ExcludedProjects...) {
		var slug string
		if err = tx.QueryRow(ctx, "SELECT slug FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", id).Scan(&slug); err != nil {
			return t, mapped(err)
		}
	}
	for _, id := range t.Groups {
		var slug string
		if err = tx.QueryRow(ctx, "SELECT slug FROM ctl_groups WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", id).Scan(&slug); err != nil {
			return t, mapped(err)
		}
	}
	var legacyProject any
	if t.Project != "" {
		legacyProject = t.Project
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_tokens(id,hash,project,expires_at,data,ciphertext) VALUES($1,$2,$3,$4,$5,$6)", t.ID, hash, legacyProject, t.ExpiresAt, b, encrypted); err != nil {
		return t, mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.create", t.Project, "", append(projects, t.Groups...)); err != nil {
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
	projects := append([]string{}, t.Projects...)
	if t.Project != "" && !slices.Contains(projects, t.Project) {
		projects = append(projects, t.Project)
	}
	rows, err := s.pool.Query(ctx, "SELECT slug FROM ctl_projects WHERE slug=ANY($1) AND deleted_at IS NULL ORDER BY slug", projects)
	if err != nil {
		return p, err
	}
	projects = []string{}
	for rows.Next() {
		var slug string
		if err = rows.Scan(&slug); err != nil {
			rows.Close()
			return p, err
		}
		projects = append(projects, slug)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return p, err
	}
	legacyProject := t.Project
	if !slices.Contains(projects, legacyProject) {
		legacyProject = ""
	}
	explicitProjects := append([]string{}, projects...)
	rows, err = s.pool.Query(ctx, "SELECT slug FROM ctl_groups WHERE slug=ANY($1) AND deleted_at IS NULL ORDER BY slug", t.Groups)
	if err != nil {
		return p, err
	}
	groups := []string{}
	for rows.Next() {
		var slug string
		if err = rows.Scan(&slug); err != nil {
			rows.Close()
			return p, err
		}
		groups = append(groups, slug)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return p, err
	}
	if len(groups) > 0 {
		rows, err := s.pool.Query(ctx, "SELECT slug FROM ctl_projects WHERE group_slug=ANY($1) AND deleted_at IS NULL ORDER BY slug", groups)
		if err != nil {
			return p, err
		}
		defer rows.Close()
		for rows.Next() {
			var slug string
			if err = rows.Scan(&slug); err != nil {
				return p, err
			}
			if !slices.Contains(projects, slug) {
				projects = append(projects, slug)
			}
		}
		if err = rows.Err(); err != nil {
			return p, err
		}
	}
	projects = slices.DeleteFunc(projects, func(project string) bool { return slices.Contains(t.ExcludedProjects, project) })
	return auth.Principal{ID: t.ID, Role: t.Role, Project: legacyProject, Projects: projects, Groups: groups, Environments: t.Environments, ExcludedProjects: t.ExcludedProjects, ExplicitProjects: explicitProjects}, nil
}
func (s *Store) RevokeToken(ctx context.Context, id, actor string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var project string
	if err = tx.QueryRow(ctx, "UPDATE ctl_tokens SET revoked=true WHERE id=$1 RETURNING COALESCE(project,'')", id).Scan(&project); err != nil {
		return mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.revoke", project, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) ListTokens(ctx context.Context) ([]domain.Token, error) {
	rows, err := s.pool.Query(ctx, "SELECT data,revoked,ciphertext IS NOT NULL FROM ctl_tokens WHERE NOT revoked ORDER BY data->>'created_at' DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Token{}
	for rows.Next() {
		var b []byte
		var revoked bool
		var readable bool
		if err = rows.Scan(&b, &revoked, &readable); err != nil {
			return nil, err
		}
		var t domain.Token
		if err = json.Unmarshal(b, &t); err != nil {
			return nil, err
		}
		t.Revoked = revoked
		t.TokenReadable = readable
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
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", r.Project)); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, "INSERT INTO ctl_receipts(id,project,data,created_at) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING", r.ID, r.Project, b, r.CreatedAt)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) ListReceipts(ctx context.Context, project string) ([]domain.Receipt, error) {
	if _, err := s.GetProject(ctx, project); err != nil {
		return nil, err
	}
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
