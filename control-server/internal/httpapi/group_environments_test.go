package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestGroupConfigurationAPIInheritanceAndMasking(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	ownerToken, _ := auth.NewToken()
	server := New(db, Options{OwnerHash: auth.HashToken(ownerToken), RegistryPublicHost: "registry.test"})
	call := func(method, path string, body any, token string, status int) map[string]any {
		t.Helper()
		raw, _ := json.Marshal(body)
		r := httptest.NewRequest(method, path, bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		if w.Code != status {
			t.Fatalf("%s %s: status %d want %d: %s", method, path, w.Code, status, w.Body.String())
		}
		var out map[string]any
		json.Unmarshal(w.Body.Bytes(), &out)
		if strings.Contains(w.Body.String(), "group-password-sentinel") && !strings.HasSuffix(path, "/resolve") {
			t.Fatal("administrative response exposed a secret")
		}
		return out
	}
	call("POST", "/api/v1/projects", map[string]any{"slug": "notes", "name": "Notes"}, ownerToken, 201)
	path := "/api/v1/groups/default/environments/stage"
	call("GET", path, nil, ownerToken, 404)
	body := map[string]any{"expected_revision": 0, "runtime_env": []map[string]any{
		{"key": "DATABASE_PASSWORD", "operation": "set", "value": "group-password-sentinel", "secret": true},
		{"key": "SHARED", "operation": "set", "value": "group"},
	}, "install_params": []map[string]any{{"key": "INSTALL_ROOT", "operation": "set", "value": "/group"}}}
	group := call("PUT", path, body, ownerToken, 200)
	if group["revision"] != float64(1) || group["environment"] != "stage" {
		t.Fatal("incorrect group revision", group)
	}
	call("PUT", path, body, ownerToken, 409)
	projectPath := "/api/v1/projects/notes/environments/stage"
	project := call("GET", projectPath, nil, ownerToken, 200)
	if project["revision"] != float64(0) || len(project["runtime_env"].([]any)) != 0 || len(project["install_params"].([]any)) != 0 || len(project["inherited_runtime_env"].([]any)) != 2 {
		t.Fatal("group-only environment must have empty own maps", project)
	}
	if source := project["group_source"].(map[string]any); source["slug"] != "default" || source["id"] != group["id"] || source["revision"] != float64(1) {
		t.Fatal("group source metadata", source)
	}
	project = call("PUT", projectPath, map[string]any{"expected_revision": 0, "runtime_env": []map[string]any{{"key": "SHARED", "operation": "set", "value": "project"}}}, ownerToken, 200)
	if project["revision"] != float64(1) || len(project["runtime_env"].([]any)) != 1 || len(project["inherited_runtime_env"].([]any)) != 2 {
		t.Fatal("project save flattened inherited maps", project)
	}
	_, err := db.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.test/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 123}, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	deployer, _ := auth.NewToken()
	if _, err = db.CreateToken(ctx, domain.Token{Name: "host", Role: "deployer", Groups: []string{"default"}, Environments: []string{"stage", "prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(deployer), "owner"); err != nil {
		t.Fatal(err)
	}
	for _, method := range []string{"GET", "PUT"} {
		call(method, path, body, deployer, 403)
	}
	call("GET", "/api/v1/groups/default/environments", nil, deployer, 403)
	resolve := func(env string) map[string]any {
		return call("POST", "/api/v1/projects/notes/resolve", map[string]any{"environment": env}, deployer, 200)["configuration"].(map[string]any)
	}
	first := resolve("stage")
	if len(first) != 5 || !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(first["id"].(string)) || first["revision"].(float64) < 1 {
		t.Fatal("resolve wire contract changed", first)
	}
	if env := first["runtime_env"].(map[string]any); env["SHARED"] != "project" || env["DATABASE_PASSWORD"] != "group-password-sentinel" {
		t.Fatal("incorrect merge", first)
	}
	if first["install_params"].(map[string]any)["INSTALL_ROOT"] != "/group" {
		t.Fatal("install parameters not inherited", first)
	}
	if again := resolve("stage"); again["id"] != first["id"] {
		t.Fatal("effective ID changed without an input change")
	}
	if prod := resolve("prod"); len(prod["runtime_env"].(map[string]any)) != 0 {
		t.Fatal("cross-environment inheritance", prod)
	}
	call("PUT", projectPath, map[string]any{"expected_revision": 1, "runtime_env": []map[string]any{{"key": "SHARED", "operation": "remove"}}}, ownerToken, 200)
	removedOverride := resolve("stage")
	if removedOverride["id"] == first["id"] || removedOverride["runtime_env"].(map[string]any)["SHARED"] != "group" {
		t.Fatal("removing project override did not reveal group value")
	}
	call("PUT", path, map[string]any{"expected_revision": 1, "runtime_env": []map[string]any{{"key": "SHARED", "operation": "remove"}, {"key": "DATABASE_PASSWORD", "operation": "keep"}}}, ownerToken, 200)
	removedGroup := resolve("stage")
	if removedGroup["id"] == removedOverride["id"] || removedGroup["runtime_env"].(map[string]any)["SHARED"] != nil || removedGroup["runtime_env"].(map[string]any)["DATABASE_PASSWORD"] != "group-password-sentinel" {
		t.Fatal("group remove/keep behavior", removedGroup)
	}
	call("PUT", "/api/v1/groups/default/environments/qa", map[string]any{"expected_revision": 0, "runtime_env": []map[string]any{{"key": "ONLY_GROUP", "operation": "set", "value": "yes"}}}, ownerToken, 200)
	qa, err := db.Resolve(ctx, "notes", "qa", "")
	if err != nil || qa.Revision.Revision < 1 || qa.Revision.Configuration.RuntimeEnv["ONLY_GROUP"].Value != "yes" {
		t.Fatal("group-only resolution", qa, err)
	}
	request := httptest.NewRequest("GET", "/api/v1/projects/notes/environments", nil)
	request.Header.Set("Authorization", "Bearer "+ownerToken)
	w := httptest.NewRecorder()
	server.ServeHTTP(w, request)
	if w.Code != 200 || strings.TrimSpace(w.Body.String()) != `["prod","qa","stage"]` {
		t.Fatal("environment union", w.Code, w.Body.String())
	}
}
