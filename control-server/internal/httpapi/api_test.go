package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestProjectSecretRevisionAndPublisherBoundary(t *testing.T) {
	db := testutil.Store(t)
	owner, _ := auth.NewToken()
	manifestServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Docker-Content-Digest", "sha256:"+strings.Repeat("a", 64))
	}))
	defer manifestServer.Close()
	api := New(db, Options{OwnerHash: auth.HashToken(owner), PublicOrigin: "http://localhost", RegistryPublicHost: "registry.example", ArtifactsDir: t.TempDir(), Verifier: registry.Verifier{InternalURL: manifestServer.URL, PublicHost: "registry.example"}})
	call := func(method, path string, body any, token string) *httptest.ResponseRecorder {
		var raw []byte
		if body != nil {
			raw, _ = json.Marshal(body)
		}
		r := httptest.NewRequest(method, path, bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		if token != "" {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		return w
	}
	p := map[string]any{"slug": "notes", "name": "Notes", "image_repository": "registry.example/notes", "default_environment": "prod"}
	if w := call("POST", "/api/v1/projects", p, owner); w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	patch := map[string]any{"expected_revision": 1, "runtime_env": []any{map[string]any{"key": "DATABASE_URL", "operation": "set", "secret": true, "value": "private-sentinel"}}}
	if w := call("PUT", "/api/v1/projects/notes/environments/prod", patch, owner); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	w := call("GET", "/api/v1/projects/notes/environments/prod", nil, owner)
	if w.Code != 200 || strings.Contains(w.Body.String(), "private-sentinel") {
		t.Fatal("secret exposed", w.Code)
	}
	if w = call("PUT", "/api/v1/projects/notes/environments/prod", patch, owner); w.Code != 409 {
		t.Fatal("stale write accepted", w.Code)
	}
	w = call("POST", "/api/v1/tokens", map[string]any{"name": "ci", "role": "publisher", "project": "notes"}, owner)
	var minted struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &minted)
	if w.Code != 201 || minted.Token == "" {
		t.Fatal("token create failed", w.Code, w.Body.String())
	}
	if w = call("GET", "/api/v1/projects/notes/environments/prod", nil, minted.Token); w.Code != 403 {
		t.Fatal("publisher read config", w.Code)
	}
	if w = call("POST", "/api/v1/projects/notes/resolve", map[string]any{}, minted.Token); w.Code != 403 {
		t.Fatal("publisher resolved production", w.Code)
	}
	raw, err := os.ReadFile("../artifacts/testdata/v1.tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	var buffer bytes.Buffer
	form := multipart.NewWriter(&buffer)
	part, _ := form.CreateFormFile("package", "release.tar.gz")
	part.Write(raw)
	form.WriteField("channel", "stable")
	form.WriteField("version", "v1.0.0")
	sum := sha256.Sum256(raw)
	form.WriteField("sha256", hex.EncodeToString(sum[:]))
	form.Close()
	req := httptest.NewRequest("POST", "/api/v1/projects/notes/releases", &buffer)
	req.Header.Set("Content-Type", form.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+minted.Token)
	w = httptest.NewRecorder()
	api.ServeHTTP(w, req)
	if w.Code != 201 {
		t.Fatal("publish failed", w.Code, w.Body.String())
	}
	w = call("POST", "/api/v1/projects/notes/resolve", map[string]any{}, owner)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "private-sentinel") || !strings.Contains(w.Body.String(), `"revision":2`) {
		t.Fatal("resolution failed", w.Code, w.Body.String())
	}
	if w = call("POST", "/api/v1/projects/notes/releases/v1.0.0/retire", nil, owner); w.Code != 200 {
		t.Fatal(w.Code)
	}
	if w = call("POST", "/api/v1/projects/notes/resolve", map[string]any{}, owner); w.Code != 404 {
		t.Fatal("retired release installable", w.Code)
	}
}

func TestCookieMutationsRequireSameOrigin(t *testing.T) {
	db := testutil.Store(t)
	owner, _ := auth.NewToken()
	api := New(db, Options{OwnerHash: auth.HashToken(owner), PublicOrigin: "http://localhost"})
	body, _ := json.Marshal(map[string]string{"token": owner})
	req := httptest.NewRequest("POST", "/api/v1/session", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://localhost")
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)
	if w.Code != 200 || len(w.Result().Cookies()) != 1 {
		t.Fatal("login failed", w.Code)
	}
	cookie := w.Result().Cookies()[0]
	for _, origin := range []string{"", "https://other.example"} {
		req = httptest.NewRequest("DELETE", "/api/v1/session", nil)
		req.AddCookie(cookie)
		req.Header.Set("Origin", origin)
		w = httptest.NewRecorder()
		api.ServeHTTP(w, req)
		if w.Code != 403 {
			t.Fatal("CSRF allowed", w.Code)
		}
	}
	req = httptest.NewRequest("DELETE", "/api/v1/session", nil)
	req.AddCookie(cookie)
	req.Header.Set("Origin", "http://localhost")
	w = httptest.NewRecorder()
	api.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	req = httptest.NewRequest("GET", "/api/v1/me", nil)
	req.AddCookie(cookie)
	w = httptest.NewRecorder()
	api.ServeHTTP(w, req)
	if w.Code != 401 {
		t.Fatal("session survived logout")
	}
}
