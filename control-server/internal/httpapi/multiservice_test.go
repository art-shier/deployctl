package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type multiVerifier struct {
	images []string
	fail   bool
}

func (v *multiVerifier) CheckManifest(ctx context.Context, image, repository string) error {
	if !strings.HasPrefix(image, repository+"@") {
		return domain.ErrInvalid
	}
	v.images = append(v.images, image)
	if v.fail && len(v.images) == 2 {
		return errors.New("manifest unavailable")
	}
	return nil
}
func TestMultiServicePublishVerifiesEveryImageAndResolvesNewProtocol(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(map[bool]string{false: "success", true: "missing-image"}[fail], func(t *testing.T) {
			db := testutil.Store(t)
			ctx := context.Background()
			if _, err := db.CreateProject(ctx, domain.Project{Slug: "lifelog", Name: "lifelog", ImageRepository: "registry.example/lifelog", DefaultEnvironment: "prod"}, "owner"); err != nil {
				t.Fatal(err)
			}
			verifier := &multiVerifier{fail: fail}
			api := New(db, Options{Verifier: verifier, ArtifactsDir: t.TempDir()})
			raw, err := os.ReadFile("../artifacts/testdata/multiservice/lifelog-v2.0.1.tar.gz")
			if err != nil {
				t.Fatal(err)
			}
			var body bytes.Buffer
			writer := multipart.NewWriter(&body)
			part, err := writer.CreateFormFile("package", "release.tar.gz")
			if err != nil {
				t.Fatal(err)
			}
			if _, err = part.Write(raw); err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(raw)
			for key, value := range map[string]string{"version": "v2.0.1", "channel": "stable", "sha256": hex.EncodeToString(sum[:])} {
				if err = writer.WriteField(key, value); err != nil {
					t.Fatal(err)
				}
			}
			if err = writer.Close(); err != nil {
				t.Fatal(err)
			}
			request := httptest.NewRequest("POST", "/api/v1/projects/lifelog/releases", &body)
			request.SetPathValue("slug", "lifelog")
			request.Header.Set("Content-Type", writer.FormDataContentType())
			response := httptest.NewRecorder()
			err = api.publish(response, request, auth.Principal{ID: "owner", Role: "owner"})
			if len(verifier.images) != 2 {
				t.Fatal("not all unique service/job images verified", verifier.images, err)
			}
			if fail {
				if err == nil {
					t.Fatal("missing image accepted")
				}
				releases, e := db.ListReleases(ctx, "lifelog")
				if e != nil || len(releases) != 0 {
					t.Fatal("failed package published", e)
				}
				return
			}
			if err != nil || response.Code != 201 {
				t.Fatal("publication failed", err, response.Body.String())
			}
			request = httptest.NewRequest("POST", "/api/v1/projects/lifelog/resolve", strings.NewReader(`{"environment":"prod"}`))
			request.Header.Set("Content-Type", "application/json")
			request.SetPathValue("slug", "lifelog")
			response = httptest.NewRecorder()
			if err = api.resolve(response, request, auth.Principal{ID: "owner", Role: "owner"}); err != nil {
				t.Fatal(err)
			}
			var resolution map[string]any
			if err = json.Unmarshal(response.Body.Bytes(), &resolution); err != nil {
				t.Fatal(err)
			}
			if resolution["schema_version"] != float64(3) || resolution["minimum_client_version"] != "1.20.0" {
				t.Fatal("unsafe legacy resolution", response.Body.String())
			}
			images := resolution["release"].(map[string]any)["images"].(map[string]any)
			if len(images) != 3 {
				t.Fatal("job image missing")
			}
		})
	}
}
