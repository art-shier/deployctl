package registry

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestPublicGHCRByActualDigest(t *testing.T) {
	if os.Getenv("CTL_TEST_PUBLIC_REGISTRY") != "1" {
		t.Skip("optional read-only public registry check")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	repo := "ghcr.io/home-assistant/home-assistant"
	verifier := Verifier{}
	session, err := verifier.newSession(repo, "")
	if err != nil {
		t.Fatal(err)
	}
	response, err := session.request(ctx, "HEAD", "/v2/home-assistant/home-assistant/manifests/stable")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal("public GHCR tag lookup failed", response.StatusCode)
	}
	digest := response.Header.Get("Docker-Content-Digest")
	if err = verifier.CheckManifest(ctx, repo+"@"+digest, repo); err != nil {
		t.Fatal("actual public GHCR digest rejected", err)
	}
}

func TestAnonymousChallengeAndEphemeralPrivateProof(t *testing.T) {
	if ValidateVerificationToken("ctl_"+strings.Repeat("e", 43)) == nil {
		t.Fatal("platform proof must be rejected before any request")
	}
	digest := "sha256:" + strings.Repeat("a", 64)
	var server *httptest.Server
	tokens := 0
	server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			tokens++
			if r.Header.Get("Authorization") != "" || r.URL.Query().Get("scope") != "repository:notes:pull" {
				t.Error("unexpected anonymous credentials/scope")
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"token":"fixture-token"}`))
			return
		}
		if r.Header.Get("Authorization") != "Bearer fixture-token" && r.Header.Get("Authorization") != "Bearer private-proof" {
			w.Header().Set("WWW-Authenticate", fmt.Sprintf(`Bearer realm="%s/token",service="fixture",scope="repository:notes:pull,push"`, server.URL))
			w.WriteHeader(401)
			return
		}
		if strings.Contains(r.URL.Path, "/tags/list") {
			w.Write([]byte(`{"name":"notes","tags":["stable"]}`))
			return
		}
		w.Header().Set("Docker-Content-Digest", digest)
	}))
	defer server.Close()
	host := strings.TrimPrefix(server.URL, "https://")
	verifier := Verifier{PublicHost: "other", Client: server.Client()}
	if err := verifier.CheckManifest(context.Background(), host+"/notes@"+digest, host+"/notes"); err != nil {
		t.Fatal("anonymous public registry rejected", err)
	}
	if tokens != 1 {
		t.Fatal("anonymous challenge not exchanged")
	}
	images, err := verifier.ListImages(context.Background(), host+"/notes")
	if err != nil || len(images) != 1 {
		t.Fatal("public tags rejected", err)
	}
	before := tokens
	if err = verifier.CheckManifestWithToken(context.Background(), host+"/notes@"+digest, host+"/notes", "private-proof"); err != nil {
		t.Fatal("ephemeral proof rejected", err)
	}
	if tokens != before {
		t.Fatal("private proof was forwarded to token realm")
	}
	if err = verifier.CheckManifestWithToken(context.Background(), host+"/notes@"+digest, host+"/notes", "ctl_"+strings.Repeat("e", 43)); err == nil {
		t.Fatal("platform credential accepted as external proof")
	}
	if err = verifier.CheckManifestWithToken(context.Background(), host+"/notes@"+digest, host+"/other", "private-proof"); err == nil {
		t.Fatal("cross repository proof used")
	}
}
