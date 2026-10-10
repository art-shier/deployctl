package httpapi

import (
	"bytes"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"net/http/httptest"
	"testing"
)

func TestStaticProjectAPI(t *testing.T) {
	db := testutil.Store(t)
	token, _ := auth.NewToken()
	srv := New(db, Options{OwnerHash: auth.HashToken(token), RegistryPublicHost: "registry.test"})
	call := func(method, path string, body any, want int) *httptest.ResponseRecorder {
		t.Helper()
		raw, _ := json.Marshal(body)
		req := httptest.NewRequest(method, path, bytes.NewReader(raw))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.ServeHTTP(w, req)
		if w.Code != want {
			t.Fatalf("%s %s: %d %s", method, path, w.Code, w.Body.String())
		}
		return w
	}
	body := map[string]any{"slug": "static-a", "name": "A", "deployment_type": "static"}
	w := call("POST", "/api/v1/projects", body, 201)
	var project map[string]any
	json.Unmarshal(w.Body.Bytes(), &project)
	if project["image_repository"] != "" || project["deployment_type"] != "static" {
		t.Fatal(project)
	}
	project["name"] = "Renamed"
	call("PATCH", "/api/v1/projects/static-a", project, 200)
	project["deployment_type"] = "docker"
	call("PATCH", "/api/v1/projects/static-a", project, 409)
	call("PUT", "/api/v1/projects/static-a/environments/prod", map[string]any{"expected_revision": 1, "deployment_defaults": map[string]any{"target_dir": "/var/www/a"}}, 200)
	call("PUT", "/api/v1/projects/static-a/environments/prod", map[string]any{"expected_revision": 2, "runtime_env": []any{map[string]any{"key": "SECRET", "operation": "set", "value": "private"}}}, 400)
}
