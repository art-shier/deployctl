package store

import (
	"context"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Store) WithRepositoryLock(ctx context.Context, repository string, operate func() error) error {
	if domain.ValidateImage(repository+"@sha256:0000000000000000000000000000000000000000000000000000000000000000") != nil {
		return domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ctl-images:"+repository); err != nil {
		return err
	}
	if err = operate(); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) ManageImages(ctx context.Context, slug, actor, action string, operate func(domain.Project) error) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR NO KEY UPDATE", slug))
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ctl-images:"+project.ImageRepository); err != nil {
		return err
	}
	if err = operate(project); err != nil {
		return err
	}
	if err = audit(ctx, tx, actor, action, slug, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
