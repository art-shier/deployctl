package artifacts

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"gopkg.in/yaml.v3"
	"io"
	"os"
	"testing"
)

func multiContent(t *testing.T, version string) map[string][]byte {
	t.Helper()
	raw, err := os.ReadFile("testdata/multiservice/lifelog-" + version + ".tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	g, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer g.Close()
	r := tar.NewReader(g)
	out := map[string][]byte{}
	for {
		h, err := r.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		out[h.Name], err = io.ReadAll(r)
		if err != nil {
			t.Fatal(err)
		}
	}
	return out
}
func TestMultiServicePythonPackageParity(t *testing.T) {
	for _, version := range []string{"v2.0.0", "v2.0.1"} {
		t.Run(version, func(t *testing.T) {
			content := multiContent(t, version)
			m, err := readYAML(content["release.yaml"])
			if err != nil {
				t.Fatal(err)
			}
			manifest, err := validateManifest(m, "lifelog", content)
			if err != nil {
				t.Fatal(err)
			}
			compose, err := readYAML(content["compose.yaml"])
			if err != nil {
				t.Fatal(err)
			}
			a, _ := json.Marshal(compose)
			b, _ := json.Marshal(renderCompose(manifest))
			if !bytes.Equal(a, b) {
				t.Fatalf("compose mismatch\n%s\n%s", a, b)
			}
		})
	}
}
func TestMultiServiceUnsafeManifestRejected(t *testing.T) {
	cases := map[string]func(map[string]any){
		"capability":    func(m map[string]any) { m["capabilities"] = []any{} },
		"minimum":       func(m map[string]any) { m["minimum_deployctl_version"] = "1.0.0" },
		"hooks":         func(m map[string]any) { m["hooks"] = map[string]any{} },
		"cross-project": func(m map[string]any) { m["deployment"].(map[string]any)["application"] = "other" },
		"unknown":       func(m map[string]any) { multiNode(m, "api")["privileged"] = true },
		"mutable":       func(m map[string]any) { multiNode(m, "postgres")["image"] = "postgres:latest" },
		"build":         func(m map[string]any) { multiNode(m, "api")["build"] = map[string]any{} },
		"profile":       func(m map[string]any) { multiNode(m, "api")["permission_profile"] = "database-init" },
		"mount":         func(m map[string]any) { multiNode(m, "postgres")["volumes"] = map[string]any{"data": "/proc/data"} },
		"overlap": func(m map[string]any) {
			multiNode(m, "postgres")["secret_files"] = map[string]any{"password": "/var/lib/postgresql/password"}
		},
		"cycle":              func(m map[string]any) { multiNode(m, "postgres")["depends_on"] = map[string]any{"api": "healthy"} },
		"unknown-dependency": func(m map[string]any) { multiNode(m, "api")["depends_on"] = map[string]any{"missing": "healthy"} },
		"wrong-edge":         func(m map[string]any) { multiNode(m, "api")["depends_on"] = map[string]any{"postgres": "succeeded"} },
		"reserved-env": func(m map[string]any) {
			multiNode(m, "api")["environment"] = map[string]any{"DEPLOYCTL_NETWORK": map[string]any{"literal": "x"}}
		},
		"volume-retention": func(m map[string]any) {
			m["deployment"].(map[string]any)["volumes"] = map[string]any{"data": map[string]any{"retain": false}}
		},
		"collision": func(m map[string]any) {
			multiNode(m, "postgres")["endpoints"] = map[string]any{"http": map[string]any{"container_port": 5432, "host_port": 8081, "bind_address": "0.0.0.0"}}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			content := multiContent(t, "v2.0.0")
			raw, err := readYAML(content["release.yaml"])
			if err != nil {
				t.Fatal(err)
			}
			m := raw.(map[string]any)
			mutate(m)
			content["release.yaml"], err = yaml.Marshal(m)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := validateManifest(m, "lifelog", content); err == nil {
				t.Fatal("unsafe manifest accepted")
			}
		})
	}
}
func multiNode(m map[string]any, name string) map[string]any {
	return m["deployment"].(map[string]any)["services"].(map[string]any)[name].(map[string]any)
}

func TestMultiServiceArchiveMetadata(t *testing.T) {
	raw, err := os.ReadFile("testdata/multiservice/lifelog-v2.0.1.tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	pack, err := Validate(bytes.NewReader(raw), "", "lifelog")
	if err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(pack.Release)
	if err != nil {
		t.Fatal(err)
	}
	var metadata map[string]any
	if err = json.Unmarshal(data, &metadata); err != nil {
		t.Fatal(err)
	}
	if metadata["schema_version"] != float64(3) || metadata["minimum_deployctl_version"] != "1.20.0" {
		t.Fatalf("missing compatibility metadata: %s", data)
	}
	images, ok := metadata["images"].(map[string]any)
	if !ok || len(images) != 3 {
		t.Fatalf("missing image inventory: %s", data)
	}
	if metadata["image"] != "" {
		t.Fatal("must not disguise a multi-service release as a single image")
	}
}

func TestMultiServiceArchiveRejectsComposeTampering(t *testing.T) {
	content := multiContent(t, "v2.0.1")
	for _, mutation := range []string{"privileged: true", "network_mode: host"} {
		t.Run(mutation, func(t *testing.T) {
			var buffer bytes.Buffer
			gz := gzip.NewWriter(&buffer)
			archive := tar.NewWriter(gz)
			for name, raw := range content {
				if name == "compose.yaml" {
					raw = bytes.Replace(raw, []byte("  postgres:\n"), []byte("  postgres:\n    "+mutation+"\n"), 1)
				}
				if err := archive.WriteHeader(&tar.Header{Name: name, Size: int64(len(raw)), Mode: 0644}); err != nil {
					t.Fatal(err)
				}
				if _, err := archive.Write(raw); err != nil {
					t.Fatal(err)
				}
			}
			if err := archive.Close(); err != nil {
				t.Fatal(err)
			}
			if err := gz.Close(); err != nil {
				t.Fatal(err)
			}
			if _, err := Validate(bytes.NewReader(buffer.Bytes()), "", "lifelog"); err == nil {
				t.Fatal("tampered compose accepted")
			}
		})
	}
}
