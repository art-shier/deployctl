package store

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
	"sort"
	"time"
)

func registrationLock(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtext('ctl-release-registration'))")
	return err
}
func (s *Store) RotateReleaseRegistrationToken(ctx context.Context, actor string) (string, error) {
	token, err := auth.NewToken()
	if err != nil {
		return "", err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	if err = registrationLock(ctx, tx); err != nil {
		return "", err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_release_registration_credential(singleton,token_hash,created_at) VALUES(true,$1,$2) ON CONFLICT(singleton) DO UPDATE SET token_hash=excluded.token_hash,created_at=excluded.created_at", auth.HashToken(token), time.Now().UTC()); err != nil {
		return "", err
	}
	if err = audit(ctx, tx, actor, "system_release.credential.rotate", "", "", nil); err != nil {
		return "", err
	}
	return token, tx.Commit(ctx)
}
func (s *Store) RevokeReleaseRegistrationToken(ctx context.Context, actor string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = registrationLock(ctx, tx); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "DELETE FROM ctl_release_registration_credential"); err != nil {
		return err
	}
	if err = audit(ctx, tx, actor, "system_release.credential.revoke", "", "", nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func (s *Store) ReleaseRegistrationCredential(ctx context.Context) (*time.Time, error) {
	var created time.Time
	err := s.pool.QueryRow(ctx, "SELECT created_at FROM ctl_release_registration_credential WHERE singleton").Scan(&created)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return &created, err
}

// A version's digest and download addresses are immutable after registration.
// A dedicated token grants only this operation, and is rechecked under the rotation lock.
func (s *Store) RegisterSystemRelease(ctx context.Context, release domain.SystemRelease, actor, source, tokenHash string) (domain.SystemRelease, bool, error) {
	if release.ValidateRegistration() != nil {
		return release, false, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return release, false, err
	}
	defer tx.Rollback(ctx)
	if err = registrationLock(ctx, tx); err != nil {
		return release, false, err
	}
	if source == "pipeline" {
		var valid bool
		if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_release_registration_credential WHERE singleton AND token_hash=$1)", tokenHash).Scan(&valid); err != nil {
			return release, false, err
		}
		if !valid {
			return release, false, domain.ErrUnauthorized
		}
	} else if source != "manual" {
		return release, false, domain.ErrInvalid
	}
	var raw []byte
	err = tx.QueryRow(ctx, "SELECT data FROM ctl_system_releases WHERE version=$1", release.Version).Scan(&raw)
	if err == nil {
		var previous domain.SystemRelease
		if err = json.Unmarshal(raw, &previous); err != nil {
			return release, false, err
		}
		if previous.CLIURL != release.CLIURL || previous.ServerURL != release.ServerURL || previous.CLISHA256 != release.CLISHA256 || previous.ServerSHA256 != release.ServerSHA256 || !previous.PublishedAt.Equal(release.PublishedAt) {
			return previous, false, domain.ErrConflict
		}
		return previous, false, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return release, false, err
	}
	now := time.Now().UTC()
	release.Source = source
	release.RegisteredAt = &now
	raw, _ = json.Marshal(release)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_system_releases(version,data) VALUES($1,$2)", release.Version, raw); err != nil {
		return release, false, err
	}
	if err = audit(ctx, tx, actor, "system_release.register", "", "", []string{release.Version, source}); err != nil {
		return release, false, err
	}
	return release, true, tx.Commit(ctx)
}
func (s *Store) SeedSystemReleases(ctx context.Context, releases []domain.SystemRelease) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = registrationLock(ctx, tx); err != nil {
		return err
	}
	for _, release := range releases {
		if release.Validate() != nil {
			continue
		}
		release.CLIURL, release.ServerURL = domain.OfficialUpdateURLs(release.Version)
		release.Source = "imported"
		now := time.Now().UTC()
		release.RegisteredAt = &now
		raw, _ := json.Marshal(release)
		if _, err = tx.Exec(ctx, "INSERT INTO ctl_system_releases(version,data) VALUES($1,$2) ON CONFLICT(version) DO NOTHING", release.Version, raw); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
func (s *Store) RegisteredSystemReleases(ctx context.Context) ([]domain.SystemRelease, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_system_releases")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	releases := []domain.SystemRelease{}
	for rows.Next() {
		var raw []byte
		var release domain.SystemRelease
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(raw, &release); err != nil {
			return nil, err
		}
		releases = append(releases, release)
	}
	sort.Slice(releases, func(i, j int) bool { return domain.UpdateVersionNewer(releases[i].Version, releases[j].Version) })
	return releases, rows.Err()
}
