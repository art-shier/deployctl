package registry

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestListImagesValidatesRepositoryAndDigest(t *testing.T) {
	digest := "sha256:" + strings.Repeat("a", 64)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "tags/list") {
			w.Write([]byte(`{"name":"notes","tags":["v1.0.0","orphan"]}`))
			return
		}
		w.Header().Set("Docker-Content-Digest", digest)
		w.Header().Set("Content-Type", "application/vnd.oci.image.manifest.v1+json")
	}))
	defer server.Close()
	verifier := Verifier{InternalURL: server.URL, PublicHost: "registry.test"}
	images, err := verifier.ListImages(context.Background(), "registry.test/notes")
	if err != nil || len(images) != 2 || images[0].Digest != digest {
		t.Fatal(images, err)
	}
}
