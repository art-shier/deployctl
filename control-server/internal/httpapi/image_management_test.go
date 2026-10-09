package httpapi

import (
	"context"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type imageManagerFixture struct {
	imported bool
	deleted  bool
}

func (*imageManagerFixture) CheckManifest(context.Context, string, string) error { return nil }
func (*imageManagerFixture) ListImages(context.Context, string) ([]registry.Image, error) {
	return []registry.Image{}, nil
}
func (m *imageManagerFixture) ImportDockerArchive(_ context.Context, _, _, file string) (registry.Image, error) {
	raw, _ := os.ReadFile(file)
	if string(raw) != "archive fixture" {
		return registry.Image{}, registry.ErrArchive
	}
	m.imported = true
	return registry.Image{Tag: "manual", Digest: "sha256:" + strings.Repeat("a", 64)}, nil
}
func (m *imageManagerFixture) DeleteManifest(_ context.Context, _, digest string) error {
	m.deleted = true
	return nil
}
func (*imageManagerFixture) InspectManifest(context.Context, string, string, string) (registry.ManifestDetails, error) {
	return registry.ManifestDetails{Platforms: []string{"linux/amd64"}}, nil
}

func TestImageEndpointsRequireOwnerBeforeReadingBody(t *testing.T) {
	api := New(nil, Options{})
	r := httptest.NewRequest("POST", "/api/v1/projects/notes/images/upload?tag=manual", strings.NewReader("never read"))
	r.SetPathValue("slug", "notes")
	if err := api.uploadImage(httptest.NewRecorder(), r, auth.Principal{Role: "publisher", Project: "notes"}); err != errForbidden {
		t.Fatal("publisher allowed archive upload", err)
	}
	if err := api.deleteImage(httptest.NewRecorder(), r, auth.Principal{Role: "deployer", Project: "notes"}); err != errForbidden {
		t.Fatal("deployer allowed image deletion", err)
	}
}

func TestOversizedArchiveIsRejectedBeforeAllocationOrDatabase(t *testing.T) {
	api := New(nil, Options{})
	r := httptest.NewRequest("POST", "/api/v1/projects/notes/images/upload?tag=manual", strings.NewReader(""))
	r.Header.Set("Content-Type", "application/octet-stream")
	r.ContentLength = registry.MaxImageArchive + 1
	if err := api.uploadImage(httptest.NewRecorder(), r, auth.Principal{Role: "owner"}); err != errImageTooLarge {
		t.Fatal("oversize rejected too late", err)
	}
}

func TestImageUploadCleanupAndReferencedDeletionAllowed(t *testing.T) {
	db := testutil.Store(t)
	owner, _ := auth.NewToken()
	manager := &imageManagerFixture{}
	artifacts := t.TempDir()
	_, err := db.CreateProject(context.Background(), domain.Project{Slug: "notes", Name: "notes", DefaultEnvironment: "prod", ImageRepository: "registry.test/notes"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	api := New(db, Options{OwnerHash: auth.HashToken(owner), RegistryPublicHost: "registry.test", ArtifactsDir: artifacts, Verifier: manager})
	call := func(method, path, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+owner)
		r.Header.Set("Content-Type", "application/octet-stream")
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		return w
	}
	if w := call("POST", "/api/v1/projects/notes/images/upload?tag=manual", "archive fixture"); w.Code != 201 || !manager.imported {
		t.Fatal(w.Code, w.Body.String())
	}
	files, _ := os.ReadDir(artifacts + "/.uploads")
	if len(files) != 0 {
		t.Fatal("private archive retained after import")
	}
	manager.imported = false
	if w := call("POST", "/api/v1/projects/notes/images/upload?tag=manual", "invalid archive"); w.Code != 400 || manager.imported {
		t.Fatal(w.Code, w.Body.String())
	}
	files, _ = os.ReadDir(artifacts + "/.uploads")
	if len(files) != 0 {
		t.Fatal("failed import archive leaked")
	}
	if w := call("POST", "/api/v1/projects/notes/images/upload?tag=../bad", "archive fixture"); w.Code != 400 {
		t.Fatal("invalid tag accepted", w.Code)
	}
	digest := "sha256:" + strings.Repeat("a", 64)
	release := domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.test/notes@" + digest, SHA256: strings.Repeat("b", 64), Size: 1}
	if _, err = db.PublishRelease(context.Background(), release, false, "ci"); err != nil {
		t.Fatal(err)
	}
	if w := call("DELETE", "/api/v1/projects/notes/images/"+digest, ""); w.Code != 200 || !manager.deleted {
		t.Fatal("referenced deletion", w.Code, w.Body.String())
	}
	releases, err := db.ListReleases(context.Background(), "notes")
	if err != nil || len(releases) != 1 || releases[0].Image != release.Image {
		t.Fatal("image deletion changed release history", releases, err)
	}
}
