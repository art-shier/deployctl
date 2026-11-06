package httpapi

import (
	"context"
	"crypto/sha256"
	"fmt"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestMirrorCatalogAndArtifactCache(t *testing.T) {
	content := []byte("verified package")
	requests := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path == "/ctl/update-index.json" {
			fmt.Fprint(w, catalogFixture("2026-11-06T00:00:00Z", "v1.17.0"))
			return
		}
		if r.URL.Path != "/ctl/v1.17.0/deployctl.pyz" {
			t.Errorf("unexpected URL %s", r.URL.Path)
		}
		w.Write(content)
	}))
	defer server.Close()
	dir := t.TempDir()
	p := NewSystemReleaseCatalog(dir, "missing", server.URL+"/ctl")
	p.client = server.Client()
	p.artifactClient = server.Client()
	if p.Sync(context.Background()).SyncError != "" {
		t.Fatal("mirror catalog failed")
	}
	job := domain.UpdateJob{Kind: "node", Version: "v1.17.0", CLISHA256: fmt.Sprintf("%x", sha256.Sum256(content))}
	if raw, err := p.Artifact(context.Background(), job); err != nil || string(raw) != string(content) {
		t.Fatal(err)
	}
	before := requests
	server.Close()
	if _, err := p.Artifact(context.Background(), job); err != nil || requests != before {
		t.Fatal("verified offline cache failed", err)
	}
	os.WriteFile(filepath.Join(dir, "system-update-packages", job.CLISHA256), []byte("corrupt"), 0600)
	if _, err := p.Artifact(context.Background(), job); err == nil {
		t.Fatal("corrupt cache accepted")
	}
}
func TestUpdateSourceValidationAndRedirectBoundary(t *testing.T) {
	for _, value := range []string{"http://mirror.example", "https://u:p@mirror.example", "https://mirror.example/a/../b", "https://mirror.example/?key=secret", "https://mirror.example/a%2fb", "https://mirror.example/$VAR"} {
		if ValidateUpdateSource(value) == nil {
			t.Errorf("accepted %q", value)
		}
	}
	for _, value := range []string{"", "https://mirror.example/ctl", "https://mirror.example:8443/ctl"} {
		if ValidateUpdateSource(value) != nil {
			t.Errorf("rejected %q", value)
		}
	}
	client := sourceHTTPClient("https://mirror.example/ctl", catalogTimeout)
	r, _ := http.NewRequest("GET", "https://github.com/unexpected", nil)
	if client.CheckRedirect(r, nil) == nil {
		t.Fatal("mirror silently redirected to GitHub")
	}
}
