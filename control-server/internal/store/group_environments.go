package store

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"slices"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

const groupRevisionSelect = `SELECT r.id,r.group_slug,r.environment,r.revision,r.ciphertext,r.created_at FROM ctl_group_environments e JOIN ctl_group_revisions r ON r.id=e.current_id WHERE e.group_slug=$1 AND e.name=$2`

// The namespace prefix prevents a group ciphertext from being used as a project revision.
func groupRevisionAAD(r domain.GroupRevision) string {
	return "group/" + r.Group + "/" + r.Environment + "/" + r.ID
}

func (s *Store) GetGroup(ctx context.Context, slug string) (domain.Group, error) {
	return scanGroup(s.pool.QueryRow(ctx, "SELECT data FROM ctl_groups WHERE slug=$1", slug))
}

func (s *Store) readGroupRevision(row scanner) (domain.GroupRevision, error) {
	var r domain.GroupRevision
	var encrypted []byte
	if err := row.Scan(&r.ID, &r.Group, &r.Environment, &r.Revision, &encrypted, &r.CreatedAt); err != nil {
		return r, mapped(err)
	}
	b, err := s.cipher.Open(encrypted, groupRevisionAAD(r))
	if err != nil {
		return r, err
	}
	err = json.Unmarshal(b, &r.Configuration)
	return r, err
}

func (s *Store) GetGroupRevision(ctx context.Context, group, env string) (domain.GroupRevision, error) {
	return s.readGroupRevision(s.pool.QueryRow(ctx, groupRevisionSelect, group, env))
}

func (s *Store) ListGroupEnvironments(ctx context.Context, group string) ([]string, error) {
	if _, err := s.GetGroup(ctx, group); err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, "SELECT name FROM ctl_group_environments WHERE group_slug=$1 ORDER BY name", group)
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

func (s *Store) SaveGroupRevision(ctx context.Context, group, env string, expected int64, cfg domain.Configuration, actor string) (domain.GroupRevision, error) {
	r := domain.GroupRevision{ID: domain.NewID(), Group: group, Environment: env, Configuration: cfg, CreatedAt: time.Now().UTC()}
	if domain.ValidateName(group, 48) != nil || domain.ValidateName(env, 32) != nil || expected < 0 || domain.ValidateConfiguration(cfg) != nil || cfg.DeploymentDefaults != (domain.DeploymentDefaults{}) {
		return r, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return r, err
	}
	defer tx.Rollback(ctx)
	if _, err = scanGroup(tx.QueryRow(ctx, "SELECT data FROM ctl_groups WHERE slug=$1", group)); err != nil {
		return r, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_group_environments(group_slug,name) VALUES($1,$2) ON CONFLICT DO NOTHING", group, env); err != nil {
		return r, mapped(err)
	}
	var current int64
	if err = tx.QueryRow(ctx, "SELECT revision FROM ctl_group_environments WHERE group_slug=$1 AND name=$2 FOR UPDATE", group, env).Scan(&current); err != nil {
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
	encrypted, err := s.cipher.Seal(b, groupRevisionAAD(r))
	if err != nil {
		return r, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_group_revisions(id,group_slug,environment,revision,ciphertext,created_at) VALUES($1,$2,$3,$4,$5,$6)", r.ID, group, env, r.Revision, encrypted, r.CreatedAt); err != nil {
		return r, mapped(err)
	}
	if _, err = tx.Exec(ctx, "UPDATE ctl_group_environments SET current_id=$3,revision=$4 WHERE group_slug=$1 AND name=$2", group, env, r.ID, r.Revision); err != nil {
		return r, err
	}
	keys := []string{"group:" + group}
	for k := range cfg.RuntimeEnv {
		keys = append(keys, "runtime:"+k)
	}
	for k := range cfg.InstallParams {
		keys = append(keys, "install:"+k)
	}
	slices.Sort(keys)
	if err = audit(ctx, tx, actor, "group.configuration.save", "", env, keys); err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}

// Membership, own revision, and inherited revision are read from one snapshot.
// A group-only environment remains editable with own revision 0.
func (s *Store) projectRevision(ctx context.Context, tx pgx.Tx, p domain.Project, env string) (domain.Revision, error) {
	r, err := s.readRevision(tx.QueryRow(ctx, revisionSelect, p.Slug, env))
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return r, err
	}
	ownExists := err == nil
	group, groupErr := s.readGroupRevision(tx.QueryRow(ctx, groupRevisionSelect, p.Group, env))
	if groupErr != nil && !errors.Is(groupErr, domain.ErrNotFound) {
		return r, groupErr
	}
	if !ownExists && groupErr != nil {
		return r, domain.ErrNotFound
	}
	if !ownExists {
		r = domain.Revision{Project: p.Slug, Environment: env, TargetVersion: "stable", Configuration: domain.Configuration{RuntimeEnv: map[string]domain.Variable{}, InstallParams: map[string]domain.Variable{}}}
	}
	if groupErr == nil {
		r.InheritedConfiguration = group.Configuration
		r.GroupSource = &domain.GroupSource{Slug: group.Group, ID: group.ID, Revision: group.Revision}
	}
	return r, nil
}

func mergeVariables(inherited, own map[string]domain.Variable) map[string]domain.Variable {
	out := make(map[string]domain.Variable, len(inherited)+len(own))
	for k, v := range inherited {
		out[k] = v
	}
	for k, v := range own {
		out[k] = v
	}
	return out
}

// The effective immutable identity commits to both source revisions and membership.
// Source revisions remain in their encrypted immutable tables for audit/reconstruction.
func effectiveRevision(p domain.Project, r domain.Revision) domain.Revision {
	groupID := ""
	if r.GroupSource != nil {
		groupID = r.GroupSource.ID
	}
	identity, _ := json.Marshal([]string{"ctl-effective-configuration-v1", p.Slug, p.Group, r.Environment, r.ID, groupID})
	digest := sha256.Sum256(identity)
	r.ID = hex.EncodeToString(digest[:16])
	r.Revision = max(r.Revision, 1)
	if r.GroupSource != nil {
		r.Revision = max(r.Revision, r.GroupSource.Revision)
	}
	r.Configuration.RuntimeEnv = mergeVariables(r.InheritedConfiguration.RuntimeEnv, r.Configuration.RuntimeEnv)
	r.Configuration.InstallParams = mergeVariables(r.InheritedConfiguration.InstallParams, r.Configuration.InstallParams)
	return r
}
