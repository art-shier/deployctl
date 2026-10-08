package httpapi

import (
	"bytes"
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestStaleGroupPrincipalCannotResolveDestinationGroupSecrets(t *testing.T) {
	ctx := context.Background()
	db := testutil.Store(t)
	for _, group := range []string{"alpha", "beta"} {
		if _, err := db.CreateGroup(ctx, domain.Group{Slug: group, Name: group}, "owner"); err != nil {
			t.Fatal(err)
		}
		if _, err := db.SaveGroupRevision(ctx, group, "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: group + "-private-secret", Secret: true}}}, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Group: "alpha", Name: "Notes", ImageRepository: "registry.test/notes"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.test/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 1}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	raw, _ := auth.NewToken()
	if _, err := db.CreateReadableToken(ctx, domain.Token{Name: "alpha-host", Role: "deployer", Groups: []string{"alpha"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner"); err != nil {
		t.Fatal(err)
	}
	principal, err := db.Authenticate(ctx, auth.HashToken(raw))
	if err != nil {
		t.Fatal(err)
	}
	srv := New(db, Options{})
	resolve := func(p auth.Principal) (*httptest.ResponseRecorder, error) {
		req := httptest.NewRequest("POST", "/api/v1/projects/notes/resolve", bytes.NewBufferString(`{"environment":"prod"}`))
		req.Header.Set("Content-Type", "application/json")
		req.SetPathValue("slug", "notes")
		out := httptest.NewRecorder()
		err := srv.resolve(out, req, p)
		return out, err
	}
	if out, err := resolve(principal); err != nil || !strings.Contains(out.Body.String(), "alpha-private-secret") {
		t.Fatal("original grant denied", err)
	}
	if _, err = db.MoveProjectGroup(ctx, "notes", "alpha", "beta", "owner"); err != nil {
		t.Fatal(err)
	}
	out, err := resolve(principal)
	if !errors.Is(err, errForbidden) || strings.Contains(out.Body.String(), "beta-private-secret") {
		t.Fatal("stale group grant exposed destination configuration", err)
	}
	explicit, _ := auth.NewToken()
	if _, err = db.CreateReadableToken(ctx, domain.Token{Name: "explicit-host", Role: "deployer", Projects: []string{"notes"}, Groups: []string{"alpha"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, explicit, "owner"); err != nil {
		t.Fatal(err)
	}
	p, err := db.Authenticate(ctx, auth.HashToken(explicit))
	if err != nil {
		t.Fatal(err)
	}
	if out, err = resolve(p); err != nil || !strings.Contains(out.Body.String(), "beta-private-secret") {
		t.Fatal("explicit project grant lost", err)
	}
}
