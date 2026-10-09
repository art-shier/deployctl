package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestPublisherProjectManagementRevealAndOwnerTombstones(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	root, _ := auth.NewToken()
	server := New(db, Options{OwnerHash: auth.HashToken(root), RegistryPublicHost: "registry.test"})
	call := func(method, path string, body any, token string, status int) *httptest.ResponseRecorder {
		t.Helper()
		raw, _ := json.Marshal(body)
		r := httptest.NewRequest(method, path, bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		if w.Code != status {
			t.Fatalf("%s %s status %d want %d: %s", method, path, w.Code, status, w.Body.String())
		}
		return w
	}
	call("POST", "/api/v1/groups", map[string]any{"slug": "apps", "name": "Apps"}, root, 201)
	for _, slug := range []string{"notes", "blocked"} {
		call("POST", "/api/v1/projects", map[string]any{"slug": slug, "name": slug, "group": "apps"}, root, 201)
	}
	call("POST", "/api/v1/projects", map[string]any{"slug": "outside", "name": "Outside"}, root, 201)
	call("PUT", "/api/v1/groups/apps/environments/prod", map[string]any{"expected_revision": 0, "runtime_env": []any{map[string]any{"key": "GROUP_PASSWORD", "operation": "set", "secret": true, "value": "inherited-secret-marker"}}}, root, 200)
	call("PUT", "/api/v1/groups/apps/environments/stage", map[string]any{"expected_revision": 0}, root, 200)
	patch := map[string]any{"expected_revision": 1, "runtime_env": []any{map[string]any{"key": "PASSWORD", "operation": "set", "secret": true, "value": "own-secret-marker"}}}
	call("PUT", "/api/v1/projects/notes/environments/prod", patch, root, 200)
	mint := func(role string, groups, projects, excluded, envs []string) string {
		t.Helper()
		raw, _ := auth.NewToken()
		_, err := db.CreateReadableToken(ctx, domain.Token{Name: role, Role: role, Groups: groups, Projects: projects, ExcludedProjects: excluded, Environments: envs, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner")
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	pub := mint("publisher", []string{"apps"}, nil, []string{"blocked"}, []string{"prod"})
	deployer := mint("deployer", []string{"apps"}, nil, nil, []string{"prod"})
	direct := mint("publisher", nil, []string{"notes"}, nil, nil)
	path := "/api/v1/projects/notes/environments/prod"
	masked := call("GET", path, nil, pub, 200)
	if strings.Contains(masked.Body.String(), "secret-marker") {
		t.Fatal("ordinary read revealed secrets")
	}
	revealed := call("GET", path+"?reveal=true", nil, pub, 200)
	if !strings.Contains(revealed.Body.String(), "own-secret-marker") || !strings.Contains(revealed.Body.String(), "inherited-secret-marker") || revealed.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("explicit reveal missing values or cache protection", revealed.Body.String())
	}
	call("GET", path+"?reveal=true", nil, deployer, 403)
	call("GET", "/api/v1/projects/notes/environments/stage?reveal=true", nil, pub, 403)
	if w := call("GET", "/api/v1/projects/notes/environments", nil, pub, 200); strings.TrimSpace(w.Body.String()) != `["prod"]` {
		t.Fatal("environment scope ignored", w.Body.String())
	}
	call("GET", "/api/v1/projects/blocked/environments/prod?reveal=true", nil, pub, 403)
	call("GET", "/api/v1/projects/outside/environments/prod", nil, pub, 403)
	patch["expected_revision"] = 2
	call("PUT", path, patch, pub, 200)
	call("PUT", path, patch, pub, 409)
	call("PUT", path, patch, deployer, 403)
	call("GET", "/api/v1/groups/apps/environments/prod?reveal=true", nil, pub, 403)
	if w := call("GET", "/api/v1/groups/apps/environments/prod?reveal=true", nil, root, 200); !strings.Contains(w.Body.String(), "inherited-secret-marker") {
		t.Fatal("owner group reveal missing")
	}
	if w := call("GET", "/api/v1/groups", nil, pub, 200); !strings.Contains(w.Body.String(), `"slug":"apps"`) || strings.Contains(w.Body.String(), `"slug":"default"`) {
		t.Fatal("publisher group list escaped scope", w.Body.String())
	}
	call("GET", "/api/v1/groups", nil, deployer, 403)
	call("POST", "/api/v1/projects", map[string]any{"slug": "created", "name": "Created", "group": "apps"}, pub, 201)
	call("POST", "/api/v1/projects", map[string]any{"slug": "repo-escape", "name": "Escape", "group": "apps", "image_repository": "registry.test/outside"}, pub, 403)
	call("POST", "/api/v1/projects", map[string]any{"slug": "external-escape", "name": "Escape", "group": "apps", "image_repository": "external.test/new"}, pub, 403)
	outside, err := db.GetProject(ctx, "outside")
	if err != nil {
		t.Fatal(err)
	}
	outside.ImageRepository = "registry.test/canonical-escape"
	call("PATCH", "/api/v1/projects/outside", outside, root, 200)
	call("POST", "/api/v1/projects", map[string]any{"slug": "canonical-escape", "name": "Escape", "group": "apps"}, pub, 403)
	outside.ImageRepository = "registry.test/outside"
	call("PATCH", "/api/v1/projects/outside", outside, root, 200)
	call("POST", "/api/v1/projects", map[string]any{"slug": "escape", "name": "Escape", "group": "default"}, pub, 403)
	call("POST", "/api/v1/projects", map[string]any{"slug": "blocked", "name": "Blocked", "group": "apps"}, pub, 403)
	call("POST", "/api/v1/projects", map[string]any{"slug": "direct-create", "name": "Denied", "group": "apps"}, direct, 403)
	call("POST", "/api/v1/projects", map[string]any{"slug": "env-escape", "name": "Denied", "group": "apps", "default_environment": "stage"}, pub, 403)
	p, err := db.GetProject(ctx, "notes")
	if err != nil {
		t.Fatal(err)
	}
	p.Name = "Edited"
	call("PATCH", "/api/v1/projects/notes", p, pub, 200)
	p.ImageRepository = "registry.test/outside"
	call("PATCH", "/api/v1/projects/notes", p, pub, 403)
	p.ImageRepository = "registry.test/notes"
	p.Group = "default"
	call("PATCH", "/api/v1/projects/notes", p, pub, 403)
	call("PATCH", "/api/v1/projects/notes/group", map[string]any{"expected_group": "apps", "group": "default"}, pub, 403)
	call("POST", "/api/v1/projects/notes/resolve", map[string]any{"environment": "prod"}, pub, 403)
	for _, request := range []struct{ method, path string }{{"POST", "/api/v1/groups"}, {"GET", "/api/v1/tokens"}, {"DELETE", "/api/v1/projects/notes"}, {"DELETE", "/api/v1/groups/apps"}} {
		call(request.method, request.path, map[string]any{"slug": "no", "name": "No"}, pub, 403)
	}
	call("DELETE", "/api/v1/groups/apps", nil, root, 409)
	call("DELETE", "/api/v1/groups/default", nil, root, 409)
	call("DELETE", "/api/v1/projects/notes", nil, root, 204)
	for _, suffix := range []string{"", "/environments", "/environments/prod?reveal=true", "/releases", "/receipts", "/images/capabilities"} {
		call("GET", "/api/v1/projects/notes"+suffix, nil, root, 404)
	}
	call("PUT", path, patch, root, 404)
	call("PATCH", "/api/v1/projects/notes", p, root, 404)
	call("POST", "/api/v1/projects", map[string]any{"slug": "notes", "name": "Reserved", "group": "apps"}, root, 409)
	identity, err := db.Authenticate(ctx, auth.HashToken(direct))
	if err != nil || identity.Can("registry.pull", "notes", "") {
		t.Fatal("archived project remained authorized", identity, err)
	}
	call("DELETE", "/api/v1/projects/blocked", nil, root, 204)
	call("DELETE", "/api/v1/projects/created", nil, root, 204)
	call("DELETE", "/api/v1/groups/apps", nil, root, 204)
	call("GET", "/api/v1/groups/apps/environments", nil, root, 404)
	call("GET", "/api/v1/groups/apps/environments/prod?reveal=true", nil, root, 404)
	call("POST", "/api/v1/groups", map[string]any{"slug": "apps", "name": "Reserved"}, root, 409)
	audits, err := db.ListAudit(ctx)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(audits)
	if strings.Contains(string(encoded), "secret-marker") || !strings.Contains(string(encoded), "configuration.reveal") || !strings.Contains(string(encoded), "project.delete") || !strings.Contains(string(encoded), "group.delete") {
		t.Fatal("unsafe/missing reveal/delete audit", string(encoded))
	}
}
