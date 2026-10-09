package registry

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/art-shier/deployctl/control-server/internal/auth"
)

func TestDeleteAllowsPublishedAndTaggedIndexChildrenAndRejectsExternal(t *testing.T) {
	child := []byte(`{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","layers":[],"config":{"digest":"sha256:` + strings.Repeat("c", 64) + `","size":42}}`)
	hash := sha256.Sum256(child)
	digest := "sha256:" + hex.EncodeToString(hash[:])
	parent, _ := json.Marshal(map[string]any{"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []any{map[string]any{"digest": digest, "platform": map[string]string{"os": "linux", "architecture": "amd64"}}}})
	hash = sha256.Sum256(parent)
	index := "sha256:" + hex.EncodeToString(hash[:])
	deleted := 0
	tagged := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			t.Error("missing scoped credential")
		}
		if strings.HasSuffix(r.URL.Path, "tags/list") {
			tags := []string{"child"}
			if tagged {
				tags = append(tags, "index")
			}
			json.NewEncoder(w).Encode(map[string]any{"name": "notes", "tags": tags})
			return
		}
		selected := digest
		raw := child
		if strings.HasSuffix(r.URL.Path, index) || strings.HasSuffix(r.URL.Path, "/index") {
			selected = index
			raw = parent
		}
		w.Header().Set("Docker-Content-Digest", selected)
		if selected == index {
			w.Header().Set("Content-Type", "application/vnd.oci.image.index.v1+json")
		} else {
			w.Header().Set("Content-Type", "application/vnd.oci.image.manifest.v1+json")
		}
		if r.Method == "DELETE" {
			deleted++
			w.WriteHeader(202)
			return
		}
		w.Write(raw)
	}))
	defer server.Close()
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	v := Verifier{InternalURL: server.URL, PublicHost: "registry.test", Signer: &auth.RegistrySigner{Key: key, Issuer: "test", Service: "test"}}
	ctx := context.Background()
	if err := v.DeleteManifest(ctx, "registry.test/notes", digest); err != nil || deleted != 1 {
		t.Fatal("published index child deletion rejected", deleted, err)
	}
	tagged = true
	if err := v.DeleteManifest(ctx, "registry.test/notes", digest); err != nil || deleted != 2 {
		t.Fatal("tagged index child deletion rejected", deleted, err)
	}
	tagged = false
	if err := v.DeleteManifest(ctx, "registry.test/notes", digest); err != nil || deleted != 3 {
		t.Fatal("unused image deletion", deleted, err)
	}
	if err := v.DeleteManifest(ctx, "external.test/notes", digest); !errors.Is(err, ErrExternal) {
		t.Fatal("external deletion accepted", err)
	}
	if deleted != 3 {
		t.Fatal("forbidden deletion reached registry")
	}
}
