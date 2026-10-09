package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestPublicationRechecksCurrentMembershipBeforeRegistryAndArtifactWrites(t *testing.T) {
	ctx := context.Background()
	db := testutil.Store(t)
	if _, err := db.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes"}, "owner"); err != nil {
		t.Fatal(err)
	}
	principal := func(projects []string) auth.Principal {
		t.Helper()
		raw, _ := auth.NewToken()
		if _, err := db.CreateToken(ctx, domain.Token{Name: "publisher", Role: "publisher", Groups: []string{"default"}, Projects: projects, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner"); err != nil {
			t.Fatal(err)
		}
		p, err := db.Authenticate(ctx, auth.HashToken(raw))
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	stale, direct := principal(nil), principal([]string{"notes"})
	if _, err := db.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../artifacts/testdata/v1.tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	var buffer bytes.Buffer
	form := multipart.NewWriter(&buffer)
	part, _ := form.CreateFormFile("package", "release.tar.gz")
	part.Write(raw)
	form.WriteField("version", "v1.0.0")
	sum := sha256.Sum256(raw)
	form.WriteField("sha256", hex.EncodeToString(sum[:]))
	form.Close()
	dir := t.TempDir()
	verifier := &proofVerifier{}
	api := New(db, Options{Verifier: verifier, ArtifactsDir: dir})
	publish := func(p auth.Principal) (*httptest.ResponseRecorder, error) {
		req := httptest.NewRequest("POST", "/api/v1/projects/notes/releases", bytes.NewReader(buffer.Bytes()))
		req.Header.Set("Content-Type", form.FormDataContentType())
		req.Header.Set("X-Registry-Verification-Token", "short-lived-private-proof")
		req.SetPathValue("slug", "notes")
		out := httptest.NewRecorder()
		return out, api.publish(out, req, p)
	}
	if _, err = publish(stale); !errors.Is(err, errForbidden) || verifier.received {
		t.Fatal("stale group publication reached registry", err, verifier.received)
	}
	if _, err = db.GetReleaseByVersion(ctx, "notes", "v1.0.0"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("denied release persisted", err)
	}
	if files, err := filepath.Glob(filepath.Join(dir, "*.tar.gz")); err != nil || len(files) != 0 {
		t.Fatal("denied publication persisted artifact", files, err)
	}
	if out, err := publish(direct); err != nil || out.Code != 201 || !verifier.received {
		t.Fatal("direct project publication denied after move", err, out.Code)
	}
}
