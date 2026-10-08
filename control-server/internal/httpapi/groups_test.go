package httpapi

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestGroupAPIAndSharedDeploymentCredential(t *testing.T) {
	db := testutil.Store(t)
	ownerToken, _ := auth.NewToken()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	signer := &auth.RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}
	server := New(db, Options{OwnerHash: auth.HashToken(ownerToken), PublicOrigin: "http://localhost", RegistryPublicHost: "registry.test", Signer: signer})
	call := func(method, path string, body any, token string) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(body)
		r := httptest.NewRequest(method, path, bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		if strings.HasPrefix(path, "/registry/token") {
			r.SetBasicAuth("ctl", token)
		} else {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		return w
	}
	if w := call("GET", "/api/v1/groups", nil, ownerToken); w.Code != 200 || !strings.Contains(w.Body.String(), `"slug":"default"`) {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("POST", "/api/v1/groups", map[string]any{"slug": "apps", "name": "Applications"}, ownerToken); w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, slug := range []string{"notes", "config", "outside"} {
		group := "apps"
		if slug == "outside" {
			group = "default"
		}
		w := call("POST", "/api/v1/projects", map[string]any{"slug": slug, "name": slug, "group": group}, ownerToken)
		if w.Code != 201 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	w := call("POST", "/api/v1/tokens", map[string]any{"name": "shared-host", "role": "deployer", "groups": []string{"apps"}, "environments": []string{"prod"}}, ownerToken)
	var minted struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &minted)
	if w.Code != 201 || minted.Token == "" {
		t.Fatal(w.Code, w.Body.String())
	}
	w = call("GET", "/api/v1/projects", nil, minted.Token)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"slug":"notes"`) || !strings.Contains(w.Body.String(), `"slug":"config"`) || strings.Contains(w.Body.String(), `"slug":"outside"`) {
		t.Fatal(w.Code, w.Body.String())
	}
	w = call("GET", "/api/v1/me", nil, minted.Token)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"groups":["apps"]`) || strings.Contains(w.Body.String(), minted.Token) {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, slug := range []string{"notes", "config"} {
		// Missing releases yield404 after authorization, while an unauthorized environment yields403.
		if w = call("POST", "/api/v1/projects/"+slug+"/resolve", map[string]any{"environment": "prod"}, minted.Token); w.Code != 404 {
			t.Fatal(w.Code, w.Body.String())
		}
		if w = call("POST", "/api/v1/projects/"+slug+"/resolve", map[string]any{"environment": "test"}, minted.Token); w.Code != 403 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	if w = call("GET", "/api/v1/projects/outside", nil, minted.Token); w.Code != 403 {
		t.Fatal("cross-group access", w.Code)
	}
	if w = call("POST", "/api/v1/groups", map[string]any{"slug": "escape", "name": "Escape"}, minted.Token); w.Code != 403 {
		t.Fatal("group mutation allowed", w.Code)
	}
	for _, scope := range []map[string]any{{"project": "notes"}, {"projects": []string{"notes"}}, {"groups": []string{"apps"}, "project": "notes"}} {
		scope["name"], scope["role"] = "removed-project-scope", "publisher"
		if w = call("POST", "/api/v1/tokens", scope, ownerToken); w.Code != 400 {
			t.Fatal("project credential creation accepted", w.Code)
		}
	}
	if w = call("PATCH", "/api/v1/projects/notes/group", map[string]any{"expected_group": "apps", "group": "default"}, minted.Token); w.Code != 403 {
		t.Fatal("deployer moved membership", w.Code)
	}
	if w = call("PATCH", "/api/v1/projects/config/group", map[string]any{"expected_group": "apps", "group": "default"}, ownerToken); w.Code != 200 {
		t.Fatal("owner move failed", w.Code)
	}
	if w = call("GET", "/api/v1/projects/config", nil, minted.Token); w.Code != 403 {
		t.Fatal("removed project still authorized", w.Code)
	}
	if w = call("PATCH", "/api/v1/projects/config/group", map[string]any{"expected_group": "apps", "group": "apps"}, ownerToken); w.Code != 409 {
		t.Fatal("stale move accepted", w.Code)
	}
	if w = call("DELETE", "/api/v1/groups/default", nil, ownerToken); w.Code < 400 {
		t.Fatal("default deleted")
	}
	w = call("GET", "/registry/token?service=ctl-registry&scope=repository:notes:pull,push&scope=repository:outside:pull", nil, minted.Token)
	var registry struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &registry)
	if w.Code != 200 || !signer.Allows(registry.Token, "notes", "pull", time.Now()) || signer.Allows(registry.Token, "notes", "push", time.Now()) || signer.Allows(registry.Token, "outside", "pull", time.Now()) {
		t.Fatal("Registry scope escaped", w.Code)
	}
}
