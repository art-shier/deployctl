package store

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/secrets"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func fixture(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("CTL_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("real PostgreSQL fixture runs in CI")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	database := "ctl_test_" + domain.NewID()
	if _, err = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.Database = database
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	cipher, _ := secrets.New(make([]byte, 32))
	s := New(pool, cipher)
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Migrate(ctx); err != nil {
		t.Fatal("migration not repeatable", err)
	}
	t.Cleanup(func() {
		pool.Close()
		admin.Exec(ctx, "DROP DATABASE "+pgx.Identifier{database}.Sanitize()+" WITH (FORCE)")
		admin.Close()
	})
	_, err = s.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", DefaultEnvironment: "prod", ImageRepository: "registry.example/notes"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestRevisionConflictPreservesSecret(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	first, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil || first.Revision != 1 {
		t.Fatal(first, err)
	}
	cfg := domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "private-sentinel", Secret: true}}}
	next, err := s.SaveRevision(ctx, "notes", "prod", 1, cfg, "stable", "owner")
	if err != nil || next.Revision != 2 {
		t.Fatal(err)
	}
	cfg.RuntimeEnv["PASSWORD"] = domain.Variable{Value: "overwrite"}
	if _, err = s.SaveRevision(ctx, "notes", "prod", 1, cfg, "stable", "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("stale update accepted", err)
	}
	saved, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil || saved.Configuration.RuntimeEnv["PASSWORD"].Value != "private-sentinel" {
		t.Fatal("secret lost", err)
	}
	var encrypted []byte
	s.pool.QueryRow(ctx, "SELECT ciphertext FROM ctl_revisions WHERE id=$1", saved.ID).Scan(&encrypted)
	if strings.Contains(string(encrypted), "private-sentinel") {
		t.Fatal("plaintext in database")
	}
}

func TestPublishIdempotencyAndResolvePinsRevision(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 123}
	first, err := s.PublishRelease(ctx, r, true, "ci")
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.PublishRelease(ctx, r, true, "ci")
	if err != nil || first.ID != again.ID {
		t.Fatal("not idempotent", err)
	}
	r.SHA256 = strings.Repeat("c", 64)
	if _, err = s.PublishRelease(ctx, r, false, "ci"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("overwrote version", err)
	}
	resolved, err := s.Resolve(ctx, "notes", "prod", "")
	if err != nil || resolved.Release.ID != first.ID {
		t.Fatal(err)
	}
	_, err = s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"TEXT": {Value: "new"}}}, "stable", "owner")
	if err != nil {
		t.Fatal(err)
	}
	if resolved.Revision.Revision != 1 || len(resolved.Revision.Configuration.RuntimeEnv) != 0 {
		t.Fatal("resolution drifted")
	}
	if err = s.RetireRelease(ctx, "notes", "v1.0.0", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Resolve(ctx, "notes", "prod", ""); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("retired release installable", err)
	}
}

func TestRevokedTokenRejected(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	raw, _ := auth.NewToken()
	token, err := s.CreateToken(ctx, domain.Token{Name: "deploy", Role: "deployer", Project: "notes", Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil || !p.Can("resolve", "notes", "prod") {
		t.Fatal(err)
	}
	if err = s.RevokeToken(ctx, token.ID, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(raw)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("revoked token accepted", err)
	}
}
