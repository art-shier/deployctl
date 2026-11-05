package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestNodeRoutesRejectPlatformAndCookieIdentity(t *testing.T) {
	s := New(nil, Options{OwnerHash: auth.HashToken("owner")})
	for _, path := range []string{"/api/v1/node/heartbeat", "/api/v1/node/poll", "/api/v1/node/tasks/abc/start"} {
		r := httptest.NewRequest("POST", path, bytes.NewBufferString(`{}`))
		r.Header.Set("Content-Type", "application/json")
		r.AddCookie(&http.Cookie{Name: "ctl_session", Value: "session"})
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 401 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
}
func TestRemoteHTTPEnrollmentIdentityBoundary(t *testing.T) {
	db := testutil.Store(t)
	s := New(db, Options{OwnerHash: auth.HashToken("owner")})
	call := func(method, path, token, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	w := call("POST", "/api/v1/nodes", "owner", `{"name":"host","groups":[],"projects":["notes"],"excluded_projects":[],"environments":["prod"]}`)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	var created struct {
		EnrollmentToken string `json:"enrollment_token"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	w = call("POST", "/api/v1/node/enroll", created.EnrollmentToken, `{"agent_version":"test","platform":"linux"}`)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var identity struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &identity)
	for _, path := range []string{"/api/v1/me", "/api/v1/nodes", "/api/v1/projects", "/api/v1/tokens"} {
		w = call("GET", path, identity.Token, "")
		if w.Code != 401 {
			t.Fatal("identity escaped", path, w.Code)
		}
	}
	w = call("POST", "/api/v1/node/enroll", created.EnrollmentToken, `{"agent_version":"test","platform":"linux"}`)
	if w.Code != 401 {
		t.Fatal("enrollment reused", w.Code)
	}
	w = call("POST", "/api/v1/node/heartbeat", identity.Token, `{"agent_version":"test","platform":"linux","docker_available":true,"instances":[]}`)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	w = call("POST", "/api/v1/node/poll", identity.Token, `{"wait_seconds":0}`)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"task":null`) {
		t.Fatal(w.Code, w.Body.String())
	}

}

func TestRemoteHTTPTaskMaterialAndRegistryScope(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	if _, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "private-frozen-value", Secret: true}}}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 7}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, strings.Repeat("b", 64)+".tar.gz"), []byte("package"), 0600); err != nil {
		t.Fatal(err)
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	signer := &auth.RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}
	srv := New(db, Options{OwnerHash: auth.HashToken("owner"), ArtifactsDir: dir, RegistryPublicHost: "registry.example", Signer: signer})
	call := func(method, path, token, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		srv.ServeHTTP(w, r)
		return w
	}
	nodeTokens := []string{}
	nodes := []domain.Node{}
	for range 2 {
		node, once, _, err := db.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
		if err != nil {
			t.Fatal(err)
		}
		_, raw, err := db.EnrollNode(ctx, auth.HashToken(once), "test", "linux")
		if err != nil {
			t.Fatal(err)
		}
		nodes = append(nodes, node)
		nodeTokens = append(nodeTokens, raw)
	}
	body := `{"node_id":"` + nodes[0].ID + `","environment":"prod","action":"install","request_id":"` + domain.NewID() + `"}`
	w := call("POST", "/api/v1/projects/notes/tasks", "owner", body)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "private-frozen-value") {
		t.Fatal("task response leaked config")
	}
	var task domain.Task
	if err = json.Unmarshal(w.Body.Bytes(), &task); err != nil {
		t.Fatal(err)
	}
	base := "/api/v1/node/tasks/" + task.ID
	if w = call("GET", base+"/package", nodeTokens[0], ""); w.Code != 403 {
		t.Fatal("queued package", w.Code)
	}
	if w = call("POST", "/api/v1/node/poll", nodeTokens[0], `{"wait_seconds":0}`); w.Code != 200 || !strings.Contains(w.Body.String(), "private-frozen-value") {
		t.Fatal("agent frozen resolution", w.Code, w.Body.String())
	}
	for _, path := range []string{base + "/package", base + "/registry"} {
		if w = call("GET", path, nodeTokens[1], ""); w.Code != 404 {
			t.Fatal("foreign material", w.Code)
		}
	}
	if w = call("GET", base+"/package", nodeTokens[0], ""); w.Code != 200 || w.Body.String() != "package" {
		t.Fatal("pinned package", w.Code, w.Body.String())
	}
	w = call("GET", base+"/registry", nodeTokens[0], "")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var credential struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &credential)
	if !signer.Allows(credential.Token, "notes", "pull", time.Now()) || signer.Allows(credential.Token, "notes", "push", time.Now()) || signer.Allows(credential.Token, "other", "pull", time.Now()) || signer.Allows(credential.Token, "notes", "pull", time.Now().Add(6*time.Minute)) {
		t.Fatal("registry grant escaped task repo pull/ttl")
	}
	if w = call("GET", "/api/v1/me", credential.Token, ""); w.Code != 401 {
		t.Fatal("registry token platform privileges")
	}
	if w = call("GET", "/api/v1/projects/notes/tasks", "owner", ""); w.Code != 200 || strings.Contains(w.Body.String(), "private-frozen-value") {
		t.Fatal("task list secret", w.Code)
	}
	if w = call("POST", base+"/start", nodeTokens[0], `{}`); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", base+"/registry", nodeTokens[0], ""); w.Code != 200 {
		t.Fatal("running token renewal", w.Code)
	}
	nodes[0].Enabled = false
	if _, err = db.UpdateNode(ctx, nodes[0].ID, nodes[0].NodeInput, "owner"); err != nil {
		t.Fatal(err)
	}
	if w = call("GET", base+"/package", nodeTokens[0], ""); w.Code != 403 {
		t.Fatal("disabled package", w.Code)
	}
	if w = call("POST", base+"/report", nodeTokens[0], `{"status":"succeeded","stage":"completed"}`); w.Code != 200 {
		t.Fatal("disabled completion", w.Code, w.Body.String())
	}
	if w = call("GET", base+"/registry", nodeTokens[0], ""); w.Code != 403 {
		t.Fatal("terminal credentials", w.Code)
	}
	if w = call("POST", "/api/v1/node/poll", "owner", `{"wait_seconds":0}`); w.Code != 401 {
		t.Fatal("platform token accepted by node API", w.Code)
	}
}

func TestRemoteInstanceReadUsesCallerEnvironmentScope(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	if _, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes"}, "owner"); err != nil {
		t.Fatal(err)
	}
	n, _, _, err := db.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod", "test"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	for _, env := range []string{"prod", "test"} {
		if _, err = db.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: n.ID, Environment: env, Action: "rollback", RequestID: domain.NewID()}, auth.Principal{ID: "owner", Role: "owner"}); err != nil {
			t.Fatal(err)
		}
	}
	raw, _ := auth.NewToken()
	if _, err = db.CreateReadableToken(ctx, domain.Token{Name: "reader", Role: "publisher", Projects: []string{"notes"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, raw, "owner"); err != nil {
		t.Fatal(err)
	}
	srv := New(db, Options{})
	r := httptest.NewRequest("GET", "/api/v1/projects/notes/instances", nil)
	r.Header.Set("Authorization", "Bearer "+raw)
	w := httptest.NewRecorder()
	srv.ServeHTTP(w, r)
	var instances []domain.Instance
	if err = json.Unmarshal(w.Body.Bytes(), &instances); err != nil {
		t.Fatal(err)
	}
	if w.Code != 200 || len(instances) != 1 || instances[0].Environment != "prod" {
		t.Fatal("project reader environment scope", w.Code, w.Body.String())
	}
}
