package store

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/secrets"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed schema.sql
var schema string

type Store struct {
	pool   *pgxpool.Pool
	cipher *secrets.Cipher
}
type Resolved struct {
	Project  domain.Project
	Release  domain.Release
	Revision domain.Revision
}

func New(pool *pgxpool.Pool, cipher *secrets.Cipher) *Store { return &Store{pool, cipher} }
func (s *Store) Migrate(ctx context.Context) error          { _, err := s.pool.Exec(ctx, schema); return err }
func mapped(err error) error {
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.ErrNotFound
	}
	var p *pgconn.PgError
	if errors.As(err, &p) && p.Code == "23505" {
		return domain.ErrConflict
	}
	return err
}
func validProject(p domain.Project) error {
	if domain.ValidateName(p.Slug, 48) != nil || domain.ValidateName(p.DefaultEnvironment, 32) != nil || p.Name == "" || len(p.Name) > 512 || len(p.Description) > 8192 || len(p.Repository) > 1024 {
		return domain.ErrInvalid
	}
	repo := regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$`)
	if !repo.MatchString(p.ImageRepository) {
		return domain.ErrInvalid
	}
	return nil
}
func audit(ctx context.Context, tx pgx.Tx, actor, action, project, environment string, keys []string) error {
	a := domain.AuditEvent{ID: domain.NewID(), Actor: actor, Action: action, Project: project, Environment: environment, Keys: keys, CreatedAt: time.Now().UTC()}
	b, _ := json.Marshal(a)
	_, err := tx.Exec(ctx, "INSERT INTO ctl_audit(id,project,data,created_at) VALUES($1,$2,$3,$4)", a.ID, project, b, a.CreatedAt)
	return err
}
func (s *Store) CreateProject(ctx context.Context, p domain.Project, actor string) (domain.Project, error) {
	if p.DefaultEnvironment == "" {
		p.DefaultEnvironment = "prod"
	}
	if err := validProject(p); err != nil {
		return p, err
	}
	p.CreatedAt = time.Now().UTC()
	b, _ := json.Marshal(p)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return p, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_projects(slug,data) VALUES($1,$2)", p.Slug, b); err != nil {
		return p, mapped(err)
	}
	if _, err = s.saveRevision(ctx, tx, p.Slug, p.DefaultEnvironment, 0, domain.Configuration{}, "stable"); err != nil {
		return p, err
	}
	if err = audit(ctx, tx, actor, "project.create", p.Slug, "", nil); err != nil {
		return p, err
	}
	return p, tx.Commit(ctx)
}

type scanner interface{ Scan(...any) error }

func scanProject(row scanner) (domain.Project, error) {
	var p domain.Project
	var b []byte
	err := row.Scan(&b)
	if err != nil {
		return p, mapped(err)
	}
	err = json.Unmarshal(b, &p)
	return p, err
}
func (s *Store) GetProject(ctx context.Context, slug string) (domain.Project, error) {
	return scanProject(s.pool.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1", slug))
}
func (s *Store) ListProjects(ctx context.Context) ([]domain.Project, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_projects ORDER BY slug")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Project{}
	for rows.Next() {
		p, err := scanProject(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
func (s *Store) UpdateProject(ctx context.Context, p domain.Project, actor string) (domain.Project, error) {
	if err := validProject(p); err != nil {
		return p, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return p, err
	}
	defer tx.Rollback(ctx)
	old, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 FOR UPDATE", p.Slug))
	if err != nil {
		return p, err
	}
	p.CreatedAt = old.CreatedAt
	// Existing environment identities and immutable releases remain intact.
	var exists bool
	if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_environments WHERE project=$1 AND name=$2)", p.Slug, p.DefaultEnvironment).Scan(&exists); err != nil {
		return p, err
	}
	if !exists {
		if _, err = s.saveRevision(ctx, tx, p.Slug, p.DefaultEnvironment, 0, domain.Configuration{}, "stable"); err != nil {
			return p, err
		}
	}
	b, _ := json.Marshal(p)
	if _, err = tx.Exec(ctx, "UPDATE ctl_projects SET data=$2 WHERE slug=$1", p.Slug, b); err != nil {
		return p, err
	}
	if err = audit(ctx, tx, actor, "project.update", p.Slug, "", nil); err != nil {
		return p, err
	}
	return p, tx.Commit(ctx)
}
func (s *Store) readRevision(row scanner) (domain.Revision, error) {
	var r domain.Revision
	var encrypted []byte
	err := row.Scan(&r.ID, &r.Project, &r.Environment, &r.Revision, &r.TargetVersion, &encrypted, &r.CreatedAt)
	if err != nil {
		return r, mapped(err)
	}
	b, err := s.cipher.Open(encrypted, r.Project+"/"+r.Environment+"/"+r.ID)
	if err != nil {
		return r, err
	}
	err = json.Unmarshal(b, &r.Configuration)
	return r, err
}

const revisionSelect = `SELECT r.id,r.project,r.environment,r.revision,r.target_version,r.ciphertext,r.created_at FROM ctl_environments e JOIN ctl_revisions r ON r.id=e.current_id WHERE e.project=$1 AND e.name=$2`

func (s *Store) GetRevision(ctx context.Context, project, env string) (domain.Revision, error) {
	return s.readRevision(s.pool.QueryRow(ctx, revisionSelect, project, env))
}
func (s *Store) ListEnvironments(ctx context.Context, project string) ([]string, error) {
	rows, err := s.pool.Query(ctx, "SELECT name FROM ctl_environments WHERE project=$1 ORDER BY name", project)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []string{}
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			return nil, err
		}
		out = append(out, name)
	}
	return out, rows.Err()
}
func (s *Store) saveRevision(ctx context.Context, tx pgx.Tx, project, env string, expected int64, cfg domain.Configuration, target string) (domain.Revision, error) {
	r := domain.Revision{ID: domain.NewID(), Project: project, Environment: env, Configuration: cfg, TargetVersion: target, CreatedAt: time.Now().UTC()}
	if domain.ValidateName(env, 32) != nil || expected < 0 || domain.ValidateConfiguration(cfg) != nil {
		return r, domain.ErrInvalid
	}
	if target == "" {
		target = "stable"
		r.TargetVersion = target
	}
	if target != "stable" && domain.ValidateVersion(target) != nil {
		return r, domain.ErrInvalid
	}
	if target != "stable" {
		var ok bool
		err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_releases WHERE project=$1 AND version=$2 AND status='published')", project, target).Scan(&ok)
		if err != nil {
			return r, err
		}
		if !ok {
			return r, domain.ErrInvalid
		}
	}
	if _, err := tx.Exec(ctx, "INSERT INTO ctl_environments(project,name) VALUES($1,$2) ON CONFLICT DO NOTHING", project, env); err != nil {
		return r, mapped(err)
	}
	var current int64
	if err := tx.QueryRow(ctx, "SELECT revision FROM ctl_environments WHERE project=$1 AND name=$2 FOR UPDATE", project, env).Scan(&current); err != nil {
		return r, err
	}
	if current != expected {
		return r, domain.ErrConflict
	}
	r.Revision = current + 1
	b, err := json.Marshal(cfg)
	if err != nil {
		return r, err
	}
	encrypted, err := s.cipher.Seal(b, project+"/"+env+"/"+r.ID)
	if err != nil {
		return r, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_revisions(id,project,environment,revision,target_version,ciphertext,created_at) VALUES($1,$2,$3,$4,$5,$6,$7)", r.ID, project, env, r.Revision, target, encrypted, r.CreatedAt); err != nil {
		return r, mapped(err)
	}
	_, err = tx.Exec(ctx, "UPDATE ctl_environments SET current_id=$3,revision=$4 WHERE project=$1 AND name=$2", project, env, r.ID, r.Revision)
	return r, err
}
func (s *Store) SaveRevision(ctx context.Context, project, env string, expected int64, cfg domain.Configuration, target, actor string) (domain.Revision, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return domain.Revision{}, err
	}
	defer tx.Rollback(ctx)
	r, err := s.saveRevision(ctx, tx, project, env, expected, cfg, target)
	if err != nil {
		return r, err
	}
	keys := []string{}
	for k := range cfg.RuntimeEnv {
		keys = append(keys, "runtime:"+k)
	}
	for k := range cfg.InstallParams {
		keys = append(keys, "install:"+k)
	}
	slices.Sort(keys)
	if err = audit(ctx, tx, actor, "configuration.save", project, env, keys); err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}
func scanRelease(row scanner) (domain.Release, error) {
	var r domain.Release
	var b []byte
	var status string
	if err := row.Scan(&b, &status); err != nil {
		return r, mapped(err)
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return r, err
	}
	r.Status = status
	return r, nil
}
func (s *Store) PublishRelease(ctx context.Context, r domain.Release, stable bool, actor string) (domain.Release, error) {
	checksum := regexp.MustCompile(`^[a-f0-9]{64}$`)
	if domain.ValidateName(r.Project, 48) != nil || domain.ValidateVersion(r.Version) != nil || domain.ValidateImage(r.Image) != nil || !checksum.MatchString(r.SHA256) || r.Size <= 0 || r.Size > 10*1024*1024 {
		return r, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return r, err
	}
	defer tx.Rollback(ctx)
	p, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 FOR UPDATE", r.Project))
	if err != nil {
		return r, err
	}
	if strings.Split(r.Image, "@")[0] != p.ImageRepository {
		return r, domain.ErrInvalid
	}
	old, err := scanRelease(tx.QueryRow(ctx, "SELECT data,status FROM ctl_releases WHERE project=$1 AND version=$2", r.Project, r.Version))
	if err == nil {
		if old.SHA256 != r.SHA256 || old.Image != r.Image || old.Status != "published" {
			return r, domain.ErrConflict
		}
		r = old
	} else if !errors.Is(err, domain.ErrNotFound) {
		return r, err
	} else {
		r.ID = domain.NewID()
		r.CreatedAt = time.Now().UTC()
		r.Status = "published"
		b, _ := json.Marshal(r)
		if _, err = tx.Exec(ctx, "INSERT INTO ctl_releases(id,project,version,status,data) VALUES($1,$2,$3,'published',$4)", r.ID, r.Project, r.Version, b); err != nil {
			return r, mapped(err)
		}
	}
	if stable {
		if _, err = tx.Exec(ctx, "UPDATE ctl_projects SET stable_version=$2 WHERE slug=$1", r.Project, r.Version); err != nil {
			return r, err
		}
	}
	if err = audit(ctx, tx, actor, "release.publish:"+r.Version, r.Project, "", nil); err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}
func (s *Store) ListReleases(ctx context.Context, project string) ([]domain.Release, error) {
	rows, err := s.pool.Query(ctx, "SELECT data,status FROM ctl_releases WHERE project=$1 ORDER BY data->>'created_at' DESC", project)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Release{}
	for rows.Next() {
		r, e := scanRelease(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
func (s *Store) GetRelease(ctx context.Context, project, id string) (domain.Release, error) {
	return scanRelease(s.pool.QueryRow(ctx, "SELECT data,status FROM ctl_releases WHERE project=$1 AND id=$2", project, id))
}
func (s *Store) RetireRelease(ctx context.Context, project, version, actor string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	cmd, err := tx.Exec(ctx, "UPDATE ctl_releases SET status='retired' WHERE project=$1 AND version=$2", project, version)
	if err != nil {
		return err
	}
	if cmd.RowsAffected() != 1 {
		return domain.ErrNotFound
	}
	if err = audit(ctx, tx, actor, "release.retire:"+version, project, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) Resolve(ctx context.Context, project, env, version string) (Resolved, error) {
	out := Resolved{}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	out.Project, err = scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1", project))
	if err != nil {
		return out, err
	}
	if env == "" {
		env = out.Project.DefaultEnvironment
	}
	out.Revision, err = s.readRevision(tx.QueryRow(ctx, revisionSelect, project, env))
	if err != nil {
		return out, err
	}
	if version == "" {
		version = out.Revision.TargetVersion
	}
	if version == "stable" {
		if err = tx.QueryRow(ctx, "SELECT stable_version FROM ctl_projects WHERE slug=$1", project).Scan(&version); err != nil {
			return out, err
		}
	}
	out.Release, err = scanRelease(tx.QueryRow(ctx, "SELECT data,status FROM ctl_releases WHERE project=$1 AND version=$2 AND status='published'", project, version))
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
