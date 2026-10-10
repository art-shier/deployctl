package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"io"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func staticUpload(t *testing.T, raw []byte, version, commit string) ([]byte, string) {
	t.Helper()
	var buffer bytes.Buffer
	form := multipart.NewWriter(&buffer)
	part, _ := form.CreateFormFile("package", "files.zip")
	part.Write(raw)
	sum := sha256.Sum256(raw)
	form.WriteField("sha256", hex.EncodeToString(sum[:]))
	form.WriteField("version", version)
	form.WriteField("channel", "stable")
	form.WriteField("commit", commit)
	form.Close()
	return buffer.Bytes(), form.FormDataContentType()
}

func TestStaticPublishResolveDownloadNoRegistry(t *testing.T) {
	for _, format := range []string{"zip", "tar.gz"} {
		t.Run(format, func(t *testing.T) {
			db := testutil.Store(t)
			ctx := context.Background()
			token, _ := auth.NewToken()
			db.CreateProject(ctx, domain.Project{Slug: "static-a", Name: "A", DeploymentType: "static"}, "owner")
			db.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{DeploymentDefaults: domain.DeploymentDefaults{TargetDir: "/var/www/group"}, RuntimeEnv: map[string]domain.Variable{"SECRET": {Value: "never-publish", Secret: true}}}, "owner")
			srv := New(db, Options{OwnerHash: auth.HashToken(token), ArtifactsDir: t.TempDir()})
			call := func(method, path, ctype string, raw []byte, want int) *httptest.ResponseRecorder {
				t.Helper()
				req := httptest.NewRequest(method, path, bytes.NewReader(raw))
				req.Header.Set("Authorization", "Bearer "+token)
				req.Header.Set("Content-Type", ctype)
				out := httptest.NewRecorder()
				srv.ServeHTTP(out, req)
				if out.Code != want {
					t.Fatalf("%s %s %d: %s", method, path, out.Code, out.Body.String())
				}
				return out
			}
			raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "static-archives", "valid."+format))
			if err != nil {
				t.Fatal(err)
			}
			body, ctype := staticUpload(t, raw, "v1.0.0", strings.Repeat("a", 40))
			published := call("POST", "/api/v1/projects/static-a/releases", ctype, body, 201)
			var release domain.Release
			json.Unmarshal(published.Body.Bytes(), &release)
			if release.Image != "" || release.ArchiveFormat != format || release.DeploymentType != "static" {
				t.Fatal(release)
			}
			again := call("POST", "/api/v1/projects/static-a/releases", ctype, body, 201)
			var retry domain.Release
			json.Unmarshal(again.Body.Bytes(), &retry)
			if retry.ID != release.ID {
				t.Fatal("retry replaced release")
			}
			changed, newtype := staticUpload(t, raw, "v1.0.0", strings.Repeat("b", 40))
			call("POST", "/api/v1/projects/static-a/releases", newtype, changed, 409)
			resolved := call("POST", "/api/v1/projects/static-a/resolve", "application/json", []byte(`{"environment":"prod"}`), 200)
			var result map[string]any
			json.Unmarshal(resolved.Body.Bytes(), &result)
			if result["schema_version"] != float64(2) || result["deployment_type"] != "static" || strings.Contains(resolved.Body.String(), "never-publish") || strings.Contains(resolved.Body.String(), `"image"`) {
				t.Fatal(resolved.Body.String())
			}
			cfg := result["configuration"].(map[string]any)
			if cfg["deployment_defaults"].(map[string]any)["target_dir"] != "/var/www/group" {
				t.Fatal(cfg)
			}
			download := call("GET", "/api/v1/projects/static-a/artifacts/"+release.ID, "", nil, 200)
			if !bytes.Equal(download.Body.Bytes(), raw) {
				t.Fatal("artifact differs")
			}
		})
	}
}

type moveOnRead struct {
	io.Reader
	before func()
	done   bool
}

func (r *moveOnRead) Read(p []byte) (int, error) {
	if !r.done {
		r.done = true
		r.before()
	}
	return r.Reader.Read(p)
}

func TestStaticPublishReauthorizesCurrentGroup(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	db.CreateGroup(ctx, domain.Group{Slug: "other", Name: "Other"}, "owner")
	db.CreateProject(ctx, domain.Project{Slug: "static-a", Name: "A", DeploymentType: "static"}, "owner")
	token, _ := auth.NewToken()
	db.CreateToken(ctx, domain.Token{Name: "pub", Role: "publisher", Groups: []string{"default"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(token), "owner")
	principal, err := db.Authenticate(ctx, auth.HashToken(token))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "static-archives", "valid.zip"))
	if err != nil {
		t.Fatal(err)
	}
	body, ctype := staticUpload(t, raw, "v1.0.0", "")
	root := t.TempDir()
	srv := New(db, Options{ArtifactsDir: root})
	reader := &moveOnRead{Reader: bytes.NewReader(body), before: func() {
		if _, e := db.MoveProjectGroup(ctx, "static-a", "default", "other", "owner"); e != nil {
			t.Fatal(e)
		}
	}}
	req := httptest.NewRequest("POST", "/api/v1/projects/static-a/releases", reader)
	req.SetPathValue("slug", "static-a")
	req.Header.Set("Content-Type", ctype)
	if err = srv.publish(httptest.NewRecorder(), req, principal); !errors.Is(err, errForbidden) {
		t.Fatal("stale rights accepted", err)
	}
	if _, err = db.GetReleaseByVersion(ctx, "static-a", "v1.0.0"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("release persisted", err)
	}
	files, _ := filepath.Glob(filepath.Join(root, "*.zip"))
	if len(files) > 0 {
		t.Fatal("denied artifact persisted")
	}
}
