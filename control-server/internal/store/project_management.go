package store

import (
	"context"
	"errors"
	"slices"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

// FOR SHARE conflicts with archival (a non-key update), unlike FOR KEY SHARE.
func (s *Store) lockGroup(ctx context.Context, tx pgx.Tx, group string) error {
	var slug string
	return mapped(tx.QueryRow(ctx, "SELECT slug FROM ctl_groups WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", group).Scan(&slug))
}

func (s *Store) GetProjectAuthorized(ctx context.Context, slug string, p auth.Principal) (domain.Project, error) {
	project, err := s.GetProject(ctx, slug)
	if err == nil && !p.CanInGroup("project.read", project.Slug, project.Group, "") {
		err = domain.ErrForbidden
	}
	return project, err
}

const environmentNames = `SELECT name FROM ctl_environments WHERE project=$1 UNION SELECT e.name FROM ctl_group_environments e JOIN ctl_projects p ON p.group_slug=e.group_slug JOIN ctl_groups g ON g.slug=e.group_slug AND g.deleted_at IS NULL WHERE p.slug=$1 AND p.deleted_at IS NULL ORDER BY name`

func configurationPrincipal(p auth.Principal) bool { return p.Role == "owner" || p.Role == "publisher" }

func (s *Store) ListEnvironmentsAuthorized(ctx context.Context, slug string, p auth.Principal) ([]string, error) {
	if !configurationPrincipal(p) {
		return nil, domain.ErrForbidden
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL", slug))
	if err != nil {
		return nil, err
	}
	if !p.CanInGroup("project.read", slug, project.Group, "") {
		return nil, domain.ErrForbidden
	}
	rows, err := tx.Query(ctx, environmentNames, slug)
	if err != nil {
		return nil, err
	}
	out := []string{}
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			rows.Close()
			return nil, err
		}
		if p.CanInGroup("configuration.read", slug, project.Group, name) {
			out = append(out, name)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	return out, tx.Commit(ctx)
}

func (s *Store) GetRevisionAuthorized(ctx context.Context, slug, env string, p auth.Principal, reveal bool) (domain.Revision, error) {
	if !configurationPrincipal(p) {
		return domain.Revision{}, domain.ErrForbidden
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
	if err != nil {
		return domain.Revision{}, err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL", slug))
	if err != nil {
		return domain.Revision{}, err
	}
	if !p.CanInGroup("configuration.read", slug, project.Group, env) {
		return domain.Revision{}, domain.ErrForbidden
	}
	r, err := s.projectRevision(ctx, tx, project, env)
	if err != nil {
		return r, err
	}
	if reveal {
		if err = audit(ctx, tx, p.ID, "configuration.reveal", slug, env, nil); err != nil {
			return r, err
		}
	}
	return r, tx.Commit(ctx)
}

func configurationKeys(c domain.Configuration) []string {
	keys := []string{}
	for key := range c.RuntimeEnv {
		keys = append(keys, "runtime:"+key)
	}
	for key := range c.InstallParams {
		keys = append(keys, "install:"+key)
	}
	slices.Sort(keys)
	return keys
}

func (s *Store) SaveConfigurationAuthorized(ctx context.Context, slug, env string, patch domain.ConfigurationPatch, p auth.Principal) (domain.Revision, error) {
	if !configurationPrincipal(p) {
		return domain.Revision{}, domain.ErrForbidden
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return domain.Revision{}, err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR UPDATE", slug))
	if err != nil {
		return domain.Revision{}, err
	}
	if !p.CanInGroup("configuration.write", slug, project.Group, env) {
		return domain.Revision{}, domain.ErrForbidden
	}
	previous, err := s.readRevision(tx.QueryRow(ctx, revisionSelect, slug, env))
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return previous, err
	}
	if errors.Is(err, domain.ErrNotFound) {
		previous = domain.Revision{TargetVersion: "stable"}
	}
	if previous.Revision != patch.ExpectedRevision {
		return previous, domain.ErrConflict
	}
	cfg := previous.Configuration
	cfg.RuntimeEnv, err = domain.ApplyChanges(cfg.RuntimeEnv, patch.RuntimeEnv, true)
	if err != nil {
		return previous, err
	}
	cfg.InstallParams, err = domain.ApplyChanges(cfg.InstallParams, patch.InstallParams, false)
	if err != nil {
		return previous, err
	}
	if patch.DeploymentDefaults != nil {
		cfg.DeploymentDefaults = *patch.DeploymentDefaults
	}
	if project.DeploymentType == "static" {
		d := cfg.DeploymentDefaults
		d.TargetDir = ""
		if len(cfg.RuntimeEnv) > 0 || len(cfg.InstallParams) > 0 || d != (domain.DeploymentDefaults{}) {
			return previous, domain.ErrInvalid
		}
	} else if cfg.DeploymentDefaults.TargetDir != "" {
		return previous, domain.ErrInvalid
	}
	target := previous.TargetVersion
	if patch.TargetVersion != nil {
		target = *patch.TargetVersion
	}
	r, err := s.saveRevision(ctx, tx, slug, env, patch.ExpectedRevision, cfg, target)
	if err != nil {
		return r, err
	}
	if err = audit(ctx, tx, p.ID, "configuration.save", slug, env, configurationKeys(cfg)); err != nil {
		return r, err
	}
	r, err = s.projectRevision(ctx, tx, project, env)
	if err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}

func (s *Store) GetGroupRevisionRevealed(ctx context.Context, group, env, actor string) (domain.GroupRevision, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
	if err != nil {
		return domain.GroupRevision{}, err
	}
	defer tx.Rollback(ctx)
	r, err := s.readGroupRevision(tx.QueryRow(ctx, groupRevisionSelect, group, env))
	if err != nil {
		return r, err
	}
	if err = audit(ctx, tx, actor, "group.configuration.reveal", "", env, []string{group}); err != nil {
		return r, err
	}
	return r, tx.Commit(ctx)
}

func (s *Store) DeleteProject(ctx context.Context, slug, actor string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR UPDATE", slug))
	if err != nil {
		return err
	}
	if err = lockRepositories(ctx, tx, project.ImageRepository); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "UPDATE ctl_projects SET deleted_at=now() WHERE slug=$1", slug); err != nil {
		return err
	}
	if err = audit(ctx, tx, actor, "project.delete", slug, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) DeleteGroup(ctx context.Context, slug, actor string) error {
	if slug == "default" {
		return domain.ErrConflict
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = scanGroup(tx.QueryRow(ctx, "SELECT data FROM ctl_groups WHERE slug=$1 AND deleted_at IS NULL FOR UPDATE", slug)); err != nil {
		return err
	}
	var occupied bool
	if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_projects WHERE group_slug=$1 AND deleted_at IS NULL)", slug).Scan(&occupied); err != nil {
		return err
	}
	if occupied {
		return domain.ErrConflict
	}
	if _, err = tx.Exec(ctx, "UPDATE ctl_groups SET deleted_at=now() WHERE slug=$1", slug); err != nil {
		return err
	}
	if err = audit(ctx, tx, actor, "group.delete", "", "", []string{slug}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
