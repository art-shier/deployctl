// Package testutil creates disposable databases only from CTL_TEST_DATABASE_URL.
package testutil

import (
	"context"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/secrets"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"testing"
)

func Store(t *testing.T) *store.Store {
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
	name := "ctl_test_" + domain.NewID()
	if _, err = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.Database = name
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		pool.Close()
		admin.Exec(ctx, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)")
		admin.Close()
	})
	cipher, _ := secrets.New(make([]byte, 32))
	s := store.New(pool, cipher)
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return s
}
