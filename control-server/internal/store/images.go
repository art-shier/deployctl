package store

import (
	"context"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func (s *Store) ManageImages(ctx context.Context, slug, actor, action string, operate func(domain.Project, []string) error) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 FOR NO KEY UPDATE", slug))
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ctl-images:"+project.ImageRepository); err != nil {
		return err
	}
	rows, err := tx.Query(ctx, "SELECT DISTINCT data->>'image' FROM ctl_releases WHERE split_part(data->>'image','@',1)=$1", project.ImageRepository)
	if err != nil {
		return err
	}
	roots := []string{}
	for rows.Next() {
		var image string
		if err = rows.Scan(&image); err != nil {
			rows.Close()
			return err
		}
		roots = append(roots, image)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if err = operate(project, roots); err != nil {
		return err
	}
	if err = audit(ctx, tx, actor, action, slug, "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
