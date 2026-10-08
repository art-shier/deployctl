package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type proofVerifier struct{ received bool }

func (v *proofVerifier) CheckManifest(context.Context, string, string) error {
	return domain.ErrInvalid
}
func (v *proofVerifier) CheckManifestWithToken(_ context.Context, image, allowed, proof string) error {
	if allowed != "registry.example/notes" || !strings.HasPrefix(image, allowed+"@") || proof != "short-lived-private-proof" {
		return domain.ErrInvalid
	}
	v.received = true
	return nil
}
func TestPublicationProofIsEphemeral(t *testing.T) {
	db := testutil.Store(t)
	owner, _ := auth.NewToken()
	v := &proofVerifier{}
	api := New(db, Options{OwnerHash: auth.HashToken(owner), Verifier: v, ArtifactsDir: t.TempDir()})
	_, err := db.CreateProject(context.Background(), domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes", DefaultEnvironment: "prod"}, "owner")
	if err != nil {
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
	req := httptest.NewRequest("POST", "/api/v1/projects/notes/releases", &buffer)
	req.Header.Set("Content-Type", form.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+owner)
	req.Header.Set("X-Registry-Verification-Token", "short-lived-private-proof")
	response := httptest.NewRecorder()
	api.ServeHTTP(response, req)
	if response.Code != 201 || !v.received {
		t.Fatal("proof not used for this check", response.Code)
	}
	if strings.Contains(response.Body.String(), "short-lived-private-proof") {
		t.Fatal("proof in response")
	}
	release, err := db.GetReleaseByVersion(context.Background(), "notes", "v1.0.0")
	if err != nil {
		t.Fatal(err)
	}
	persisted, _ := json.Marshal(release)
	if bytes.Contains(persisted, []byte("short-lived-private-proof")) {
		t.Fatal("proof persisted in release metadata")
	}
}

func TestInvalidImagePublicationDoesNotPersistPackage(t *testing.T) {
	db := testutil.Store(t)
	owner, _ := auth.NewToken()
	dir := t.TempDir()
	api := New(db, Options{OwnerHash: auth.HashToken(owner), Verifier: &proofVerifier{}, ArtifactsDir: dir})
	_, err := db.CreateProject(context.Background(), domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes", DefaultEnvironment: "prod"}, "owner")
	if err != nil {
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
	hash := sha256.Sum256(raw)
	form.WriteField("sha256", hex.EncodeToString(hash[:]))
	form.Close()
	req := httptest.NewRequest("POST", "/api/v1/projects/notes/releases", &buffer)
	req.Header.Set("Content-Type", form.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+owner)
	response := httptest.NewRecorder()
	api.ServeHTTP(response, req)
	if response.Code != 400 {
		t.Fatal(response.Code)
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.tar.gz"))
	if err != nil || len(files) != 0 {
		t.Fatal("failed verification persisted orphan package", files, err)
	}
}
