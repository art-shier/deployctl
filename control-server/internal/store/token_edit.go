package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

var ErrTokenUnavailable = errors.New("legacy token is not recoverable")

func validEditableToken(t domain.Token) error {
	if t.Name == "" || len(t.Name) > 128 || (t.Role != "publisher" && t.Role != "deployer") || !t.ExpiresAt.After(time.Now()) || t.ExpiresAt.After(time.Now().Add(366*24*time.Hour)) {
		return domain.ErrInvalid
	}
	if len(t.Groups)+len(t.Projects) == 0 || len(t.Groups)+len(t.Projects) > 128 || len(t.ExcludedProjects) > 128 || len(t.Environments) > 128 || (t.Role == "deployer" && len(t.Environments) == 0) {
		return domain.ErrInvalid
	}
	for index, scope := range [][]string{t.Projects, t.Groups, t.ExcludedProjects, t.Environments} {
		size := 48
		if index == 3 {
			size = 32
		}
		seen := map[string]bool{}
		for _, key := range scope {
			if domain.ValidateName(key, size) != nil || seen[key] {
				return domain.ErrInvalid
			}
			seen[key] = true
		}
	}
	return nil
}

func checkEditableScopes(ctx context.Context, tx pgx.Tx, t domain.Token) error {
	for _, key := range append(append([]string{}, t.Projects...), t.ExcludedProjects...) {
		var found string
		if err := tx.QueryRow(ctx, "SELECT slug FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", key).Scan(&found); err != nil {
			return mapped(err)
		}
	}
	for _, key := range t.Groups {
		var found string
		if err := tx.QueryRow(ctx, "SELECT slug FROM ctl_groups WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", key).Scan(&found); err != nil {
			return mapped(err)
		}
	}
	return nil
}

func readEditableToken(ctx context.Context, tx pgx.Tx, id string) (domain.Token, error) {
	var t domain.Token
	var raw []byte
	var readable bool
	err := tx.QueryRow(ctx, "SELECT data,ciphertext IS NOT NULL FROM ctl_tokens WHERE id=$1 AND NOT revoked FOR UPDATE", id).Scan(&raw, &readable)
	if err != nil {
		return t, mapped(err)
	}
	if err = json.Unmarshal(raw, &t); err != nil {
		return t, err
	}
	t.TokenReadable = readable
	return t, nil
}

func (s *Store) UpdateToken(ctx context.Context, id string, next domain.Token, actor string) (domain.Token, error) {
	if err := validEditableToken(next); err != nil {
		return next, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return next, err
	}
	defer tx.Rollback(ctx)
	prior, err := readEditableToken(ctx, tx, id)
	if err != nil {
		return next, err
	}
	if err = checkEditableScopes(ctx, tx, next); err != nil {
		return next, err
	}
	next.ID = prior.ID
	next.CreatedAt = prior.CreatedAt
	next.Revoked = false
	next.Project = ""
	next.TokenReadable = prior.TokenReadable
	encoded, err := json.Marshal(next)
	if err != nil {
		return next, err
	}
	if _, err = tx.Exec(ctx, "UPDATE ctl_tokens SET data=$2,project=NULL,expires_at=$3 WHERE id=$1", id, encoded, next.ExpiresAt); err != nil {
		return next, mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.update", "", "", []string{id}); err != nil {
		return next, err
	}
	return next, tx.Commit(ctx)
}

func (s *Store) TokenSecret(ctx context.Context, id, actor string) (string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	var encrypted []byte
	var hash string
	if err = tx.QueryRow(ctx, "SELECT ciphertext,hash FROM ctl_tokens WHERE id=$1 AND NOT revoked FOR SHARE", id).Scan(&encrypted, &hash); err != nil {
		return "", mapped(err)
	}
	if len(encrypted) == 0 {
		return "", ErrTokenUnavailable
	}
	raw, err := s.cipher.Open(encrypted, "ctl-token/"+id)
	if err != nil {
		return "", err
	}
	if auth.HashToken(string(raw)) != hash {
		return "", domain.ErrInvalid
	}
	if err = audit(ctx, tx, actor, "token.reveal", "", "", []string{id}); err != nil {
		return "", err
	}
	if err = tx.Commit(ctx); err != nil {
		return "", err
	}
	return string(raw), nil
}

func (s *Store) RotateToken(ctx context.Context, id, raw, actor string) (domain.Token, error) {
	var t domain.Token
	if !strings.HasPrefix(raw, "ctl_") || len(raw) > 4096 {
		return t, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return t, err
	}
	defer tx.Rollback(ctx)
	t, err = readEditableToken(ctx, tx, id)
	if err != nil {
		return t, err
	}
	encrypted, err := s.cipher.Seal([]byte(raw), "ctl-token/"+id)
	if err != nil {
		return t, err
	}
	t.TokenReadable = true
	encoded, _ := json.Marshal(t)
	if _, err = tx.Exec(ctx, "UPDATE ctl_tokens SET hash=$2,ciphertext=$3,data=$4 WHERE id=$1", id, auth.HashToken(raw), encrypted, encoded); err != nil {
		return t, mapped(err)
	}
	if err = audit(ctx, tx, actor, "token.rotate", "", "", []string{id}); err != nil {
		return t, err
	}
	return t, tx.Commit(ctx)
}
