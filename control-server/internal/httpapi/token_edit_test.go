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

func TestTokenEditRevealRotateAndHiddenRevocation(t *testing.T) {
	db := testutil.Store(t)
	root, _ := auth.NewToken()
	srv := New(db, Options{OwnerHash: auth.HashToken(root), PublicOrigin: "http://localhost", RegistryPublicHost: "registry.test"})
	call := func(method, path string, body any, token string) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(body)
		req := httptest.NewRequest(method, path, bytes.NewReader(raw))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+token)
		out := httptest.NewRecorder()
		srv.ServeHTTP(out, req)
		return out
	}
	for _, slug := range []string{"notes", "config"} {
		if w := call("POST", "/api/v1/projects", map[string]any{"slug": slug, "name": slug}, root); w.Code != 201 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	body := map[string]any{"name": "host", "role": "deployer", "groups": []string{"default"}, "projects": []string{}, "excluded_projects": []string{}, "environments": []string{"prod"}, "expires_at": time.Now().Add(time.Hour)}
	w := call("POST", "/api/v1/tokens", body, root)
	var minted struct {
		Token      string       `json:"token"`
		Credential domain.Token `json:"credential"`
	}
	json.Unmarshal(w.Body.Bytes(), &minted)
	if w.Code != 201 || !minted.Credential.TokenReadable {
		t.Fatal("create", w.Code, w.Body.String())
	}
	path := "/api/v1/tokens/" + minted.Credential.ID
	if w = call("GET", path+"/secret", nil, root); w.Code != 200 || !strings.Contains(w.Body.String(), minted.Token) || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("owner reveal", w.Code)
	}
	if w = call("GET", "/api/v1/tokens", nil, root); w.Code != 200 || strings.Contains(w.Body.String(), minted.Token) {
		t.Fatal("token leak in list", w.Code)
	}
	for _, target := range []struct{ method, path string }{{"GET", path + "/secret"}, {"POST", path + "/rotate"}, {"PATCH", path}} {
		if w = call(target.method, target.path, body, minted.Token); w.Code != 403 {
			t.Fatal("deployer managed credential", w.Code)
		}
	}
	body["excluded_projects"] = []string{"notes"}
	if w = call("PATCH", path, body, root); w.Code != 200 || strings.Contains(w.Body.String(), minted.Token) {
		t.Fatal("edit", w.Code, w.Body.String())
	}
	if w = call("GET", "/api/v1/projects/notes", nil, minted.Token); w.Code != 403 {
		t.Fatal("excluded project allowed", w.Code)
	}
	if w = call("GET", "/api/v1/projects/config", nil, minted.Token); w.Code != 200 {
		t.Fatal("remaining project denied", w.Code)
	}
	body["groups"] = []string{}
	body["projects"] = []string{"notes"}
	body["excluded_projects"] = []string{}
	if w = call("PATCH", path, body, root); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", "/api/v1/projects/notes", nil, minted.Token); w.Code != 200 {
		t.Fatal("project addition failed", w.Code)
	}
	if w = call("GET", "/api/v1/projects/config", nil, minted.Token); w.Code != 403 {
		t.Fatal("project removal failed", w.Code)
	}
	w = call("POST", path+"/rotate", nil, root)
	var rotated struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &rotated)
	if w.Code != 200 || rotated.Token == "" || rotated.Token == minted.Token {
		t.Fatal("rotate", w.Code)
	}
	if w = call("GET", "/api/v1/me", nil, minted.Token); w.Code != 401 {
		t.Fatal("old token accepted", w.Code)
	}
	if w = call("DELETE", path, nil, root); w.Code != 200 {
		t.Fatal(w.Code)
	}
	if w = call("GET", "/api/v1/tokens", nil, root); w.Body.String() != "[]\n" {
		t.Fatal("revoked list visible", w.Body.String())
	}
	if w = call("GET", path+"/secret", nil, root); w.Code != 404 {
		t.Fatal("revoked reveal", w.Code)
	}
	if w = call("PATCH", path, body, root); w.Code != 404 {
		t.Fatal("revoked revival", w.Code)
	}
	raw, _ := auth.NewToken()
	old, err := db.CreateToken(context.Background(), domain.Token{Name: "legacy", Project: "notes", Role: "publisher", ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
	if err != nil {
		t.Fatal(err)
	}
	if w = call("GET", "/api/v1/tokens/"+old.ID+"/secret", nil, root); w.Code != 409 || !strings.Contains(w.Body.String(), "重新生成") {
		t.Fatal("legacy reveal", w.Code, w.Body.String())
	}
}
