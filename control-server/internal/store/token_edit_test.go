package store

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func TestReadableTokenEncryptedUpdateExclusionAndRevocation(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, domain.Project{Slug: "other", Name: "Other", ImageRepository: "registry.example/other"}, "owner"); err != nil {
		t.Fatal(err)
	}
	raw, _ := auth.NewToken()
	tok, err := s.CreateReadableToken(ctx, domain.Token{Name: "server", Role: "deployer", Groups: []string{"default"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner")
	if err != nil || !tok.TokenReadable {
		t.Fatal(tok, err)
	}
	got, err := s.TokenSecret(ctx, tok.ID, "owner")
	if err != nil || got != raw {
		t.Fatal("cannot recover token", err)
	}
	var encrypted []byte
	var metadata string
	if err = s.pool.QueryRow(ctx, "SELECT ciphertext,data::text FROM ctl_tokens WHERE id=$1", tok.ID).Scan(&encrypted, &metadata); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encrypted), raw) || strings.Contains(metadata, raw) {
		t.Fatal("plaintext persisted")
	}
	tok.ExcludedProjects = []string{"notes"}
	tok, err = s.UpdateToken(ctx, tok.ID, tok, "owner")
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil || p.Can("resolve", "notes", "prod") || !p.Can("resolve", "other", "prod") {
		t.Fatal("edited scope not applied", p, err)
	}
	tok.Groups = nil
	tok.Projects = []string{"notes"}
	tok.ExcludedProjects = nil
	tok, err = s.UpdateToken(ctx, tok.ID, tok, "owner")
	if err != nil {
		t.Fatal(err)
	}
	p, err = s.Authenticate(ctx, auth.HashToken(raw))
	if err != nil || !p.Can("resolve", "notes", "prod") || p.Can("resolve", "other", "prod") {
		t.Fatal("project scope not replaced", p, err)
	}
	tok.Projects = []string{"missing"}
	if _, err = s.UpdateToken(ctx, tok.ID, tok, "owner"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("unknown scope accepted", err)
	}
	p, _ = s.Authenticate(ctx, auth.HashToken(raw))
	if !p.Can("resolve", "notes", "prod") {
		t.Fatal("failed edit changed old scope")
	}
	replacement, _ := auth.NewToken()
	rotated, err := s.RotateToken(ctx, tok.ID, replacement, "owner")
	if err != nil || rotated.ID != tok.ID {
		t.Fatal("rotate", err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(raw)); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("old token survived rotation", err)
	}
	if _, err = s.Authenticate(ctx, auth.HashToken(replacement)); err != nil {
		t.Fatal("new token rejected", err)
	}
	if got, err = s.TokenSecret(ctx, tok.ID, "owner"); err != nil || got != replacement {
		t.Fatal("rotated token unrecoverable", err)
	}
	if err = s.RevokeToken(ctx, tok.ID, "owner"); err != nil {
		t.Fatal(err)
	}
	items, err := s.ListTokens(ctx)
	if err != nil || len(items) != 0 {
		t.Fatal("revoked token visible", items, err)
	}
	if _, err = s.TokenSecret(ctx, tok.ID, "owner"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("revoked secret readable", err)
	}
	if _, err = s.UpdateToken(ctx, tok.ID, rotated, "owner"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("revoked token revived", err)
	}
	audit, err := s.ListAudit(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range audit {
		for _, key := range entry.Keys {
			if key == raw || key == replacement {
				t.Fatal("secret in audit")
			}
		}
	}
}

func TestLegacyTokenRevealNeedsRotationWithoutLosingScope(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	raw, _ := auth.NewToken()
	tok, err := s.CreateToken(ctx, domain.Token{Name: "legacy", Role: "deployer", Project: "notes", Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.TokenSecret(ctx, tok.ID, "owner"); !errors.Is(err, ErrTokenUnavailable) {
		t.Fatal("legacy token unexpectedly recoverable", err)
	}
	next, _ := auth.NewToken()
	if _, err = s.RotateToken(ctx, tok.ID, next, "owner"); err != nil {
		t.Fatal(err)
	}
	p, err := s.Authenticate(ctx, auth.HashToken(next))
	if err != nil || !p.Can("resolve", "notes", "prod") {
		t.Fatal("legacy scope lost", p, err)
	}
}
