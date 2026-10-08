package store

import (
	"context"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"time"
)

func validGroup(g domain.Group) error {
	if domain.ValidateName(g.Slug, 48) != nil || g.Name == "" || len(g.Name) > 512 || len(g.Description) > 8192 {
		return domain.ErrInvalid
	}
	return nil
}
func scanGroup(row scanner) (domain.Group, error) {
	var g domain.Group
	var b []byte
	if err := row.Scan(&b); err != nil {
		return g, mapped(err)
	}
	return g, json.Unmarshal(b, &g)
}
func (s *Store) ListGroups(ctx context.Context) ([]domain.Group, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_groups ORDER BY slug")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Group{}
	for rows.Next() {
		g, err := scanGroup(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, g)
	}
	return out, rows.Err()
}
func (s *Store) CreateGroup(ctx context.Context, g domain.Group, actor string) (domain.Group, error) {
	if err := validGroup(g); err != nil {
		return g, err
	}
	g.CreatedAt = time.Now().UTC()
	b, _ := json.Marshal(g)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return g, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_groups(slug,data) VALUES($1,$2)", g.Slug, b); err != nil {
		return g, mapped(err)
	}
	if err = audit(ctx, tx, actor, "group.create", "", "", []string{g.Slug}); err != nil {
		return g, err
	}
	return g, tx.Commit(ctx)
}
func (s *Store) UpdateGroup(ctx context.Context, g domain.Group, actor string) (domain.Group, error) {
	if err := validGroup(g); err != nil {
		return g, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return g, err
	}
	defer tx.Rollback(ctx)
	old, err := scanGroup(tx.QueryRow(ctx, "SELECT data FROM ctl_groups WHERE slug=$1 FOR UPDATE", g.Slug))
	if err != nil {
		return g, err
	}
	g.CreatedAt = old.CreatedAt
	b, _ := json.Marshal(g)
	if _, err = tx.Exec(ctx, "UPDATE ctl_groups SET data=$2 WHERE slug=$1", g.Slug, b); err != nil {
		return g, err
	}
	if err = audit(ctx, tx, actor, "group.update", "", "", []string{g.Slug}); err != nil {
		return g, err
	}
	return g, tx.Commit(ctx)
}

// MoveProjectGroup changes membership without overwriting concurrently edited metadata.
func (s *Store) MoveProjectGroup(ctx context.Context, slug, expected, target, actor string) (domain.Project, error) {
	var p domain.Project
	if domain.ValidateName(target, 48) != nil || domain.ValidateName(expected, 48) != nil {
		return p, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return p, err
	}
	defer tx.Rollback(ctx)
	p, err = scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 FOR UPDATE", slug))
	if err != nil {
		return p, err
	}
	if p.Group != expected {
		return p, domain.ErrConflict
	}
	p.Group = target
	b, _ := json.Marshal(p)
	if _, err = tx.Exec(ctx, "UPDATE ctl_projects SET group_slug=$2,data=$3 WHERE slug=$1", slug, target, b); err != nil {
		return p, mapped(err)
	}
	if err = audit(ctx, tx, actor, "project.move", slug, "", []string{expected, target}); err != nil {
		return p, err
	}
	return p, tx.Commit(ctx)
}
