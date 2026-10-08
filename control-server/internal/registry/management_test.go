package registry

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInventoryFollowsPagesWithoutSendingCredentialsToLinks(t *testing.T) {
	digest := "sha256:" + strings.Repeat("a", 64)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "tags/list") {
			tags := []string{}
			start := 0
			if r.URL.Query().Get("last") != "" {
				start = 100
			}
			end := 101
			if start == 0 {
				end = 100
				w.Header().Set("Link", `<https://untrusted.example/v2/notes/tags/list?last=tag099>; rel="next"`)
			}
			for n := start; n < end; n++ {
				tags = append(tags, fmt.Sprintf("tag%03d", n))
			}
			json.NewEncoder(w).Encode(map[string]any{"name": "notes", "tags": tags})
			return
		}
		w.Header().Set("Docker-Content-Digest", digest)
	}))
	defer server.Close()
	images, err := (Verifier{InternalURL: server.URL, PublicHost: "registry.test"}).ListImages(context.Background(), "registry.test/notes")
	if err != nil || len(images) != 101 {
		t.Fatalf("complete inventory: got %d, %v", len(images), err)
	}
}

func writeArchive(t *testing.T, files map[string]string) string {
	t.Helper()
	file := filepath.Join(t.TempDir(), "image.tar")
	f, err := os.Create(file)
	if err != nil {
		t.Fatal(err)
	}
	writer := tar.NewWriter(f)
	for name, data := range files {
		if err = writer.WriteHeader(&tar.Header{Name: name, Mode: 0600, Size: int64(len(data))}); err != nil {
			t.Fatal(err)
		}
		if _, err = writer.Write([]byte(data)); err != nil {
			t.Fatal(err)
		}
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	return file
}
func archiveFiles() map[string]string {
	return map[string]string{"manifest.json": `[{"Config":"config.json","RepoTags":["example/image:local"],"Layers":[]}]`, "config.json": `{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}`}
}
func TestDockerArchiveRejectsTraversalMultipleImagesAndMissingLayers(t *testing.T) {
	if _, err := ReadDockerArchive(writeArchive(t, archiveFiles())); err != nil {
		t.Fatal("valid single image", err)
	}
	for _, kind := range []string{"traversal", "multiple", "missing", "config-size"} {
		t.Run(kind, func(t *testing.T) {
			files := archiveFiles()
			switch kind {
			case "traversal":
				files["../outside"] = "no"
			case "multiple":
				files["manifest.json"] = `[{"Config":"config.json","Layers":[]},{"Config":"config.json","Layers":[]}]`
			case "missing":
				files["manifest.json"] = `[{"Config":"config.json","Layers":["missing/layer.tar"]}]`
			case "config-size":
				files["config.json"] = strings.Repeat(" ", 1024*1024+1)
			}
			if _, err := ReadDockerArchive(writeArchive(t, files)); err == nil {
				t.Fatal("unsafe archive accepted")
			}
		})
	}
}

func TestDockerArchiveChecksActualLayerContentAgainstDiffID(t *testing.T) {
	files := archiveFiles()
	files["manifest.json"] = `[{"Config":"config.json","Layers":["layer.tar"]}]`
	files["config.json"] = `{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":["sha256:` + strings.Repeat("0", 64) + `"]}}`
	files["layer.tar"] = "content that does not match the config digest"
	if _, err := ReadDockerArchive(writeArchive(t, files)); err == nil {
		t.Fatal("layer differs from config but was accepted")
	}
}

func TestDockerSaveCompressedLayerIsSupportedAndVerified(t *testing.T) {
	files := archiveFiles()
	raw := []byte("fixture uncompressed layer")
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	zw.Write(raw)
	zw.Close()
	hash := sha256.Sum256(raw)
	files["manifest.json"] = `[{"Config":"config.json","Layers":["layer.tar.gz"]}]`
	files["config.json"] = `{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":["sha256:` + hex.EncodeToString(hash[:]) + `"]}}`
	files["layer.tar.gz"] = buf.String()
	if _, err := ReadDockerArchive(writeArchive(t, files)); err != nil {
		t.Fatal("valid compressed Docker save layer", err)
	}
}

func TestDockerSaveLayerSourcesRejectsForeignURLs(t *testing.T) {
	files := archiveFiles()
	raw := []byte("raw layer")
	hash := sha256.Sum256(raw)
	digest := "sha256:" + hex.EncodeToString(hash[:])
	files["layer.tar"] = string(raw)
	files["config.json"] = `{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":["` + digest + `"]}}`
	source := map[string]any{"digest": digest, "size": len(raw), "mediaType": "application/vnd.docker.image.rootfs.diff.tar"}
	document := []any{map[string]any{"Config": "config.json", "Layers": []string{"layer.tar"}, "LayerSources": map[string]any{digest: source}}}
	content, _ := json.Marshal(document)
	files["manifest.json"] = string(content)
	if _, err := ReadDockerArchive(writeArchive(t, files)); err != nil {
		t.Fatal("Docker25 source descriptor", err)
	}
	source["urls"] = []string{"https://untrusted.example/layer"}
	content, _ = json.Marshal(document)
	files["manifest.json"] = string(content)
	if _, err := ReadDockerArchive(writeArchive(t, files)); err == nil {
		t.Fatal("foreign source can escape managed registry")
	}
}
