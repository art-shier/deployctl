package registry

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestManifestMustMatchRegisteredRepositoryAndDigest(t *testing.T) {
	digest := "sha256:" + strings.Repeat("a", 64)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "HEAD" || r.URL.Path != "/v2/notes/manifests/"+digest {
			t.Fatal(r.URL.Path)
		}
		w.Header().Set("Docker-Content-Digest", digest)
	}))
	defer server.Close()
	verifier := Verifier{InternalURL: server.URL, PublicHost: "registry.example"}
	if err := verifier.CheckManifest(context.Background(), "registry.example/notes@"+digest, "registry.example/notes"); err != nil {
		t.Fatal(err)
	}
	if verifier.CheckManifest(context.Background(), "registry.example/other@"+digest, "registry.example/notes") == nil {
		t.Fatal("cross repo accepted")
	}
}
