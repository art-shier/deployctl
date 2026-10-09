package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestMissingAgentBundleDoesNotFailStartupAndReturnsUnavailable(t *testing.T) {
	owner, _ := auth.NewToken()
	server := New(nil, Options{OwnerHash: auth.HashToken(owner)})
	r := httptest.NewRequest("GET", "/api/v1/agent-access", nil)
	r.Header.Set("Authorization", "Bearer "+owner)
	w := httptest.NewRecorder()
	server.ServeHTTP(w, r)
	if w.Code != 503 {
		t.Fatal("missing Agent resources should be temporarily unavailable", w.Code, w.Body.String())
	}
}

func agentBundleFixture(t *testing.T) (string, map[string]string) {
	t.Helper()
	dir := t.TempDir()
	files := map[string]string{"SKILL.md": "Public Agent guide\n", "references/control-plane.md": "Public reference\n",
		"assets/deployctl.pyz": "bundled-cli-test-bytes", "team-deploy-skill.zip": "bundled-skill-test-bytes",
		"assets/install.sh": "#!/bin/sh\n", "assets/templates/release.yml": "name: release\n",
		"owner.token": "private-owner-marker", "metadata.private": "private-config-marker", "assets/private.key": "private-key-marker"}
	for name, contents := range files {
		path := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(contents), 0644); err != nil {
			t.Fatal(err)
		}
	}
	metadata := map[string]string{"version": "1.12.0"}
	for field, name := range map[string]string{"skill_sha256": "team-deploy-skill.zip", "cli_sha256": "assets/deployctl.pyz"} {
		digest := sha256.Sum256([]byte(files[name]))
		metadata[field] = hex.EncodeToString(digest[:])
		if err := os.WriteFile(filepath.Join(dir, filepath.FromSlash(name))+".sha256", []byte(metadata[field]+"  "+filepath.Base(name)+"\n"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	raw, _ := json.Marshal(metadata)
	if err := os.WriteFile(filepath.Join(dir, "metadata.json"), raw, 0644); err != nil {
		t.Fatal(err)
	}
	return dir, metadata
}

func agentHTTP(t *testing.T, server *httptest.Server, method, path, token string) (*http.Response, []byte) {
	t.Helper()
	r, err := http.NewRequest(method, server.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	r.Header.Set("X-Forwarded-Host", "untrusted.example")
	response, err := server.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return response, raw
}

func TestAgentMetadataUsesAuthenticatedPrincipalsWithoutAddingPrivileges(t *testing.T) {
	db := testutil.Store(t)
	dir, metadata := agentBundleFixture(t)
	owner, _ := auth.NewToken()
	server := httptest.NewServer(New(db, Options{OwnerHash: auth.HashToken(owner), AgentDir: dir, PublicOrigin: "https://ctl.example"}))
	defer server.Close()
	if response, _ := agentHTTP(t, server, "GET", "/api/v1/agent-access", ""); response.StatusCode != 401 {
		t.Fatal("anonymous metadata accepted", response.StatusCode)
	}
	tokens := []string{owner}
	for _, role := range []string{"publisher", "deployer"} {
		raw, _ := auth.NewToken()
		if _, err := db.CreateToken(context.Background(), domain.Token{Name: role, Role: role, Groups: []string{"default"}, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner"); err != nil {
			t.Fatal(err)
		}
		tokens = append(tokens, raw)
	}
	expected := map[string]string{"version": "1.12.0", "server_url": "https://ctl.example", "skill_name": "team-deploy",
		"skill_url": "/agent/team-deploy-skill.zip", "skill_sha256": metadata["skill_sha256"],
		"cli_url": "/agent/deployctl.pyz", "cli_sha256": metadata["cli_sha256"], "entry_url": "/agent/SKILL.md",
		"release_url": "https://github.com/art-shier/deployctl/releases/tag/v1.12.0"}
	for i, token := range tokens {
		response, raw := agentHTTP(t, server, "GET", "/api/v1/agent-access", token)
		var actual map[string]string
		if response.StatusCode != 200 || json.Unmarshal(raw, &actual) != nil || !reflect.DeepEqual(actual, expected) || response.Header.Get("Cache-Control") != "no-store" {
			t.Fatal("invalid authenticated metadata", response.StatusCode, string(raw))
		}
		if i > 0 {
			if response, _ := agentHTTP(t, server, "GET", "/api/v1/tokens", token); response.StatusCode != 403 {
				t.Fatal("Agent metadata added administration privileges", response.StatusCode)
			}
		}
	}
	// Existing browser owner sessions work through the same authentication wrapper.
	session, _ := auth.NewToken()
	if err := db.CreateSession(context.Background(), auth.HashToken(session)); err != nil {
		t.Fatal(err)
	}
	req, _ := http.NewRequest("GET", server.URL+"/api/v1/agent-access", nil)
	req.AddCookie(&http.Cookie{Name: "ctl_session", Value: session})
	response, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal("owner session rejected", response.StatusCode)
	}
}

func TestAgentPublicResourcesDownloadsAndChecksums(t *testing.T) {
	dir, metadata := agentBundleFixture(t)
	server := httptest.NewServer(New(nil, Options{AgentDir: dir}))
	defer server.Close()
	for _, name := range []string{"SKILL.md", "references/control-plane.md", "assets/install.sh", "assets/templates/release.yml", "assets/deployctl.pyz", "deployctl.pyz", "team-deploy-skill.zip", "team-deploy-skill.zip.sha256", "deployctl.pyz.sha256"} {
		response, raw := agentHTTP(t, server, "GET", "/agent/"+name, "")
		if response.StatusCode != 200 || len(raw) == 0 || response.Header.Get("X-Content-Type-Options") != "nosniff" {
			t.Fatal("public resource missing", name, response.StatusCode)
		}
		head, body := agentHTTP(t, server, "HEAD", "/agent/"+name, "")
		if head.StatusCode != 200 || len(body) != 0 || head.ContentLength != int64(len(raw)) {
			t.Fatal("HEAD differs from GET", name, head.StatusCode, head.ContentLength, len(raw))
		}
		if name == "deployctl.pyz" || name == "team-deploy-skill.zip" {
			digest := sha256.Sum256(raw)
			field := "cli_sha256"
			if name == "team-deploy-skill.zip" {
				field = "skill_sha256"
			}
			if hex.EncodeToString(digest[:]) != metadata[field] || !strings.Contains(response.Header.Get("Content-Disposition"), "attachment;") {
				t.Fatal("download digest or attachment mismatch", name)
			}
		}
	}
}

func TestAgentUnknownPrivateAndTraversalPathsNeverFallThroughToSPA(t *testing.T) {
	dir, _ := agentBundleFixture(t)
	web := t.TempDir()
	os.WriteFile(filepath.Join(web, "index.html"), []byte("private-SPA-marker"), 0644)
	server := httptest.NewServer(New(nil, Options{AgentDir: dir, WebDir: web}))
	defer server.Close()
	for _, name := range []string{"", "/", "/unknown", "/owner.token", "/metadata.json", "/metadata.private", "/assets/private.key", "/agents/openai.yaml", "/../SKILL.md", "/%2e%2e/owner.token", "/%252e%252e/owner.token", "/assets/%2e%2e/owner.token", "//SKILL.md", "/assets%5cprivate.key"} {
		response, raw := agentHTTP(t, server, "GET", "/agent"+name, "")
		if response.StatusCode != 404 || strings.Contains(string(raw), "marker") {
			t.Fatal("Agent path leaked or reached SPA", name, response.StatusCode, string(raw))
		}
	}
	if response, _ := agentHTTP(t, server, "POST", "/agent/SKILL.md", ""); response.StatusCode != 404 {
		t.Fatal("public write method accepted", response.StatusCode)
	}
}

func TestMalformedAgentBundlesReturnSanitizedUnavailableAndLeaveOtherAPIsWorking(t *testing.T) {
	for _, invalid := range []string{"missing-metadata", "invalid-json", "extra-field", "bad-version", "bad-hash", "missing-entry", "missing-cli", "corrupt-archive", "bad-checksum", "oversized-cli"} {
		t.Run(invalid, func(t *testing.T) {
			dir, metadata := agentBundleFixture(t)
			switch invalid {
			case "missing-metadata":
				os.Remove(filepath.Join(dir, "metadata.json"))
			case "invalid-json":
				os.WriteFile(filepath.Join(dir, "metadata.json"), []byte("private-invalid-json-marker"), 0644)
			case "extra-field", "bad-version", "bad-hash":
				if invalid == "extra-field" {
					metadata["token"] = "private-key-marker"
				} else if invalid == "bad-version" {
					metadata["version"] = "../../../private-version-marker"
				} else {
					metadata["cli_sha256"] = "private-hash-marker"
				}
				raw, _ := json.Marshal(metadata)
				os.WriteFile(filepath.Join(dir, "metadata.json"), raw, 0644)
			case "missing-entry":
				os.Remove(filepath.Join(dir, "SKILL.md"))
			case "missing-cli":
				os.Remove(filepath.Join(dir, "assets/deployctl.pyz"))
			case "corrupt-archive":
				os.WriteFile(filepath.Join(dir, "team-deploy-skill.zip"), []byte("private-corruption-marker"), 0644)
			case "bad-checksum":
				os.WriteFile(filepath.Join(dir, "team-deploy-skill.zip.sha256"), []byte("private-checksum-marker"), 0644)
			case "oversized-cli":
				if err := os.Truncate(filepath.Join(dir, "assets/deployctl.pyz"), maxAgentFile+1); err != nil {
					t.Fatal(err)
				}
			}
			owner, _ := auth.NewToken()
			server := httptest.NewServer(New(nil, Options{AgentDir: dir, OwnerHash: auth.HashToken(owner)}))
			defer server.Close()
			response, raw := agentHTTP(t, server, "GET", "/api/v1/agent-access", owner)
			if response.StatusCode != 503 || !strings.Contains(string(raw), "agent_resources_unavailable") || strings.Contains(string(raw), "private-") || strings.Contains(string(raw), dir) {
				t.Fatal("malformed bundle did not fail safely", response.StatusCode, string(raw))
			}
			if response, _ := agentHTTP(t, server, "GET", "/api/v1/me", owner); response.StatusCode != 200 {
				t.Fatal("Agent resources broke unrelated endpoint", response.StatusCode)
			}
		})
	}
}

func TestAgentSymlinkResourcesAreNotPublic(t *testing.T) {
	dir, _ := agentBundleFixture(t)
	private := filepath.Join(t.TempDir(), "private.md")
	os.WriteFile(private, []byte("private-symlink-marker"), 0644)
	if err := os.Symlink(private, filepath.Join(dir, "references/linked.md")); err != nil {
		t.Skip("symlinks unavailable on this host")
	}
	server := httptest.NewServer(New(nil, Options{AgentDir: dir}))
	defer server.Close()
	response, raw := agentHTTP(t, server, "GET", "/agent/references/linked.md", "")
	if response.StatusCode != 404 || strings.Contains(string(raw), "marker") {
		t.Fatal("symlink target was public", response.StatusCode, string(raw))
	}
}
