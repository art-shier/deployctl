package config

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestInitializePreservesKeysAndLoadFailsClosed(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "keys")
	if err := Initialize(dir); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(filepath.Join(dir, "encryption.key"))
	if err := Initialize(dir); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(filepath.Join(dir, "encryption.key"))
	if !bytes.Equal(before, after) {
		t.Fatal("key overwritten")
	}
	t.Setenv("CTL_KEYS_DIR", dir)
	t.Setenv("CTL_DATABASE_URL", "")
	if _, err := Load(); err == nil {
		t.Fatal("missing database accepted")
	}
	t.Setenv("CTL_DATABASE_URL", "postgres://fixture-only")
	t.Setenv("CTL_PUBLIC_ORIGIN", "http://remote.example")
	if _, err := Load(); err == nil {
		t.Fatal("insecure public origin accepted")
	}
	t.Setenv("CTL_PUBLIC_ORIGIN", "http://127.0.0.1:8080")
	if _, err := Load(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "encryption.key"), []byte("bad"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(); err == nil {
		t.Fatal("invalid key accepted")
	}
}
