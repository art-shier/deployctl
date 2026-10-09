package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type registryRoundTrip func(*http.Request) (*http.Response, error)

func (f registryRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// Real Distribution validates our JWT and enforces repository/action boundaries.
func TestRealRegistryScopesAndIndex(t *testing.T) {
	if os.Getenv("CTL_TEST_REGISTRY") != "1" {
		t.Skip("disposable Docker registry runs in CI")
	}
	db := testutil.Store(t)
	ctx := context.Background()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	certificate := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "ctl-test"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, certificate, certificate, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	certPath := filepath.Join(dir, "signing.crt")
	if err = os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0644); err != nil {
		t.Fatal(err)
	}
	signer := &auth.RegistrySigner{Key: key, Issuer: "ctl-test", Service: "ctl-registry"}
	owner, _ := auth.NewToken()
	api := New(db, Options{OwnerHash: auth.HashToken(owner), Signer: signer})
	apiServer := httptest.NewServer(api)
	defer apiServer.Close()
	name := "ctl-test-" + domain.NewID()
	run := exec.Command("docker", "run", "-d", "--name", name, "-p", "127.0.0.1::5000", "-v", certPath+":/cert.pem:ro", "-e", "REGISTRY_AUTH=token", "-e", "REGISTRY_AUTH_TOKEN_REALM="+apiServer.URL+"/registry/token", "-e", "REGISTRY_AUTH_TOKEN_SERVICE=ctl-registry", "-e", "REGISTRY_AUTH_TOKEN_ISSUER=ctl-test", "-e", "REGISTRY_AUTH_TOKEN_ROOTCERTBUNDLE=/cert.pem", "-e", "REGISTRY_STORAGE_DELETE_ENABLED=true", "registry:2.8.3")
	if out, e := run.CombinedOutput(); e != nil {
		t.Fatalf("registry start: %s", out)
	}
	t.Cleanup(func() { exec.Command("docker", "rm", "-f", "-v", name).Run() })
	out, err := exec.Command("docker", "port", name, "5000/tcp").Output()
	if err != nil {
		t.Fatal(err)
	}
	backendHost := strings.TrimSpace(string(out))
	origin := "http://" + backendHost
	host := strings.TrimPrefix(apiServer.URL, "http://")
	api.options.RegistryPublicHost = host
	verifier := registry.Verifier{InternalURL: origin, PublicHost: host, Signer: signer}
	api.options.Verifier = verifier
	api.options.ArtifactsDir = filepath.Join(dir, "artifacts")
	for _, slug := range []string{"notes", "other"} {
		if _, err = db.CreateProject(ctx, domain.Project{Slug: slug, Name: slug, ImageRepository: host + "/" + slug, DefaultEnvironment: "prod"}, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	publisher, _ := auth.NewToken()
	deployer, _ := auth.NewToken()
	for _, entry := range []struct{ role, raw string }{{"publisher", publisher}, {"deployer", deployer}} {
		_, err = db.CreateToken(ctx, domain.Token{Name: entry.role, Project: "notes", Role: entry.role, Environments: []string{"prod"}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(entry.raw), "owner")
		if err != nil {
			t.Fatal(err)
		}
	}
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	for deadline := time.Now().Add(30 * time.Second); ; {
		res, e := client.Get(origin + "/v2/")
		if e == nil {
			res.Body.Close()
			if res.StatusCode == 401 {
				break
			}
		}
		if time.Now().After(deadline) {
			t.Fatal("registry not ready")
		}
		time.Sleep(100 * time.Millisecond)
	}
	token := func(raw, scope string) string {
		t.Helper()
		req, _ := http.NewRequest("GET", apiServer.URL+"/registry/token?service=ctl-registry&scope="+url.QueryEscape(scope), nil)
		req.SetBasicAuth("ctl", raw)
		res, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer res.Body.Close()
		var value struct {
			Token string `json:"token"`
		}
		if e = json.NewDecoder(res.Body).Decode(&value); e != nil || res.StatusCode != 200 {
			t.Fatal("token request failed", res.StatusCode, e)
		}
		return value.Token
	}
	call := func(method, path, bearer, media string, raw []byte) (int, http.Header) {
		t.Helper()
		req, _ := http.NewRequest(method, apiServer.URL+path, bytes.NewReader(raw))
		req.Header.Set("Authorization", "Bearer "+bearer)
		req.Header.Set("Accept", "application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json")
		if media != "" {
			req.Header.Set("Content-Type", media)
		}
		res, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer res.Body.Close()
		io.Copy(io.Discard, res.Body)
		return res.StatusCode, res.Header
	}
	push := token(publisher, "repository:notes:pull,push")
	blob := []byte(`{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}`)
	sum := sha256.Sum256(blob)
	digest := "sha256:" + hex.EncodeToString(sum[:])
	status, headers := call("POST", "/v2/notes/blobs/uploads/", push, "", nil)
	if status != 202 {
		t.Fatal("upload start", status)
	}
	location, err := url.Parse(headers.Get("Location"))
	if err != nil {
		t.Fatal(err)
	}
	query := location.Query()
	query.Set("digest", digest)
	location.RawQuery = query.Encode()
	status, _ = call("PUT", location.RequestURI(), push, "application/octet-stream", blob)
	if status != 201 {
		t.Fatal("blob commit", status)
	}
	manifest, _ := json.Marshal(map[string]any{"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "config": map[string]any{"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest, "size": len(blob)}, "layers": []any{}})
	status, headers = call("PUT", "/v2/notes/manifests/fixture", push, "application/vnd.oci.image.manifest.v1+json", manifest)
	if status != 201 {
		t.Fatal("manifest push", status)
	}
	manifestDigest := headers.Get("Docker-Content-Digest")
	index, _ := json.Marshal(map[string]any{"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []any{map[string]any{"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": manifestDigest, "size": len(manifest), "platform": map[string]string{"architecture": "amd64", "os": "linux"}}}})
	status, headers = call("PUT", "/v2/notes/manifests/multi", push, "application/vnd.oci.image.index.v1+json", index)
	if status != 201 {
		t.Fatal("index push", status)
	}
	images, err := verifier.ListImages(ctx, host+"/notes")
	if err != nil || len(images) != 2 {
		t.Fatal("real registry image inventory", images, err)
	}
	if err = verifier.CheckManifest(ctx, host+"/notes@"+headers.Get("Docker-Content-Digest"), host+"/notes"); err != nil {
		t.Fatal("index verification", err)
	}
	indexDigest := headers.Get("Docker-Content-Digest")
	apiCall := func(method, path, rawToken string, body io.Reader) (int, []byte) {
		t.Helper()
		req, e := http.NewRequest(method, apiServer.URL+"/api/v1/projects/notes/"+path, body)
		if e != nil {
			t.Fatal(e)
		}
		req.Header.Set("Authorization", "Bearer "+rawToken)
		req.Header.Set("Content-Type", "application/octet-stream")
		res, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer res.Body.Close()
		raw, e := io.ReadAll(res.Body)
		if e != nil {
			t.Fatal(e)
		}
		return res.StatusCode, raw
	}
	if status, _ = apiCall("DELETE", "images/"+manifestDigest, owner, nil); status != 200 {
		t.Fatal("tagged multiarch child deletion rejected", status)
	}
	if status, _ = call("HEAD", "/v2/notes/manifests/"+manifestDigest, push, "", nil); status != 404 {
		t.Fatal("deleted child still available", status)
	}
	// Restore fixture content for the independent publication and upload checks.
	if status, _ = call("PUT", "/v2/notes/manifests/fixture", push, "application/vnd.oci.image.manifest.v1+json", manifest); status != 201 {
		t.Fatal("restore child fixture", status)
	}
	release := domain.Release{Project: "notes", Version: "v0.0.0", Image: host + "/notes@" + indexDigest, SHA256: strings.Repeat("a", 64), Size: 100}
	if _, err = db.PublishRelease(ctx, release, false, "ci"); err != nil {
		t.Fatal(err)
	}
	if status, _ = apiCall("DELETE", "images/"+indexDigest, owner, nil); status != 200 {
		t.Fatal("published release image deletion rejected", status)
	}
	if status, _ = call("HEAD", "/v2/notes/manifests/"+indexDigest, push, "", nil); status != 404 {
		t.Fatal("deleted published index still available", status)
	}
	if status, _ = call("PUT", "/v2/notes/manifests/multi", push, "application/vnd.oci.image.index.v1+json", index); status != 201 {
		t.Fatal("restore published index fixture", status)
	}
	if err = db.RetireRelease(ctx, "notes", release.Version, "owner"); err != nil {
		t.Fatal(err)
	}
	if status, _ = apiCall("DELETE", "images/"+indexDigest, owner, nil); status != 200 {
		t.Fatal("retired release image deletion rejected", status)
	}
	if status, _ = call("HEAD", "/v2/notes/manifests/"+indexDigest, push, "", nil); status != 404 {
		t.Fatal("deleted index still available", status)
	}
	if status, _ = call("PUT", "/v2/notes/manifests/multi", push, "application/vnd.oci.image.index.v1+json", index); status != 201 {
		t.Fatal("restore index fixture", status)
	}
	if status, _ = apiCall("DELETE", "images/"+indexDigest, publisher, nil); status != 403 {
		t.Fatal("publisher allowed delete", status)
	}
	// Import an actual docker-save file, not an SDK-generated lookalike.
	buildDir := filepath.Join(dir, "build")
	if err = os.Mkdir(buildDir, 0700); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(buildDir, "Dockerfile"), []byte("FROM scratch\nCOPY fixture /fixture\n"), 0600)
	os.WriteFile(filepath.Join(buildDir, "fixture"), []byte("image import fixture"), 0600)
	localImage := "ctl-import-" + domain.NewID()
	if out, e := exec.Command("docker", "build", "-t", localImage, buildDir).CombinedOutput(); e != nil {
		t.Fatal("fixture build", string(out))
	}
	t.Cleanup(func() { exec.Command("docker", "image", "rm", "-f", localImage).Run() })
	archive := filepath.Join(dir, "saved.tar")
	if out, e := exec.Command("docker", "save", "--output", archive, localImage).CombinedOutput(); e != nil {
		t.Fatal("Docker save", string(out))
	}
	credentialsDir := filepath.Join(dir, "docker-client")
	if err = os.Mkdir(credentialsDir, 0700); err != nil {
		t.Fatal(err)
	}
	docker := func(args ...string) *exec.Cmd {
		command := exec.Command("docker", args...)
		command.Env = append(os.Environ(), "DOCKER_CONFIG="+credentialsDir)
		return command
	}
	login := docker("login", host, "--username", "ctl", "--password-stdin")
	login.Stdin = strings.NewReader(publisher)
	if _, err = login.CombinedOutput(); err != nil {
		t.Fatal("native Docker login through gateway failed", err)
	}
	nativeTag := host + "/notes:native"
	if _, err = docker("tag", localImage, nativeTag).CombinedOutput(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { exec.Command("docker", "image", "rm", "-f", nativeTag).Run() })
	if _, err = docker("push", nativeTag).CombinedOutput(); err != nil {
		t.Fatal("native Docker push through gateway failed", err)
	}
	status, nativeHeaders := call("HEAD", "/v2/notes/manifests/native", push, "", nil)
	if status != 200 {
		t.Fatal("native push result unavailable", status)
	}
	nativeDigest := nativeHeaders.Get("Docker-Content-Digest")
	var imported registry.Image
	for _, tag := range []string{"manual", "alias"} {
		file, e := os.Open(archive)
		if e != nil {
			t.Fatal(e)
		}
		code, raw := apiCall("POST", "images/upload?tag="+tag, owner, file)
		file.Close()
		if code != 201 || json.Unmarshal(raw, &imported) != nil {
			t.Fatal("image archive import", code, string(raw))
		}
	}
	if details, e := verifier.InspectManifest(ctx, host+"/notes", imported.Digest, ""); e != nil || len(details.Platforms) != 1 || details.SizeBytes <= 0 {
		t.Fatal("actual image details", details, e)
	}
	images, err = verifier.ListImages(ctx, host+"/notes")
	if err != nil || len(images) != 5 {
		t.Fatal("aliases inventory", images, err)
	}
	if status, _ = apiCall("DELETE", "images/"+imported.Digest, deployer, nil); status != 403 {
		t.Fatal("deployer allowed delete", status)
	}
	// Pause deletion after its dependency scan but before Registry mutation.
	// An actual concurrent index PUT must wait, then fail because the child is gone.
	deletionEntered := make(chan struct{})
	allowDeletion := make(chan struct{})
	var allowOnce sync.Once
	defer allowOnce.Do(func() { close(allowDeletion) })
	guarded := verifier
	guarded.Client = &http.Client{Timeout: 10 * time.Second, Transport: registryRoundTrip(func(req *http.Request) (*http.Response, error) {
		if req.Method == "DELETE" && strings.HasSuffix(req.URL.Path, imported.Digest) {
			close(deletionEntered)
			<-allowDeletion
		}
		return http.DefaultTransport.RoundTrip(req)
	})}
	api.options.Verifier = guarded
	deleted := make(chan int, 1)
	go func() { code, _ := apiCall("DELETE", "images/"+imported.Digest, owner, nil); deleted <- code }()
	select {
	case <-deletionEntered:
	case <-time.After(5 * time.Second):
		t.Fatal("deletion did not reach controlled race point")
	}
	// Registry requires authenticated manifest reads; use the existing pull token.
	manifestReq, _ := http.NewRequest("GET", origin+"/v2/notes/manifests/"+imported.Digest, nil)
	manifestReq.Header.Set("Authorization", "Bearer "+push)
	manifestReq.Header.Set("Accept", "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json")
	manifestRes, e := client.Do(manifestReq)
	if e != nil {
		t.Fatal(e)
	}
	manifestRaw, e := io.ReadAll(manifestRes.Body)
	manifestRes.Body.Close()
	if e != nil || manifestRes.StatusCode != 200 {
		t.Fatal("race child read", e)
	}
	candidate, _ := json.Marshal(map[string]any{"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []any{map[string]any{"mediaType": imported.MediaType, "digest": imported.Digest, "size": len(manifestRaw), "platform": map[string]string{"architecture": "amd64", "os": "linux"}}}})
	pushed := make(chan int, 1)
	go func() {
		code, _ := call("PUT", "/v2/notes/manifests/racing-index", push, "application/vnd.oci.image.index.v1+json", candidate)
		pushed <- code
	}()
	select {
	case code := <-pushed:
		t.Fatal("concurrent index write bypassed deletion lock", code)
	case <-time.After(100 * time.Millisecond):
	}
	allowOnce.Do(func() { close(allowDeletion) })
	if code := <-deleted; code != 200 {
		t.Fatal("unreferenced deletion failed", code)
	}
	if code := <-pushed; code != 400 {
		t.Fatal("Registry installed index with deleted child", code)
	}
	images, err = verifier.ListImages(ctx, host+"/notes")
	expected := 3
	if nativeDigest == imported.Digest {
		expected = 2
	}
	if err != nil || len(images) != expected {
		t.Fatal("all aliases must be removed", images, err)
	}
	if err = verifier.CheckManifest(ctx, host+"/notes@"+manifestDigest, host+"/notes"); err != nil {
		t.Fatal("unrelated index child damaged", err)
	}
	files, _ := os.ReadDir(filepath.Join(api.options.ArtifactsDir, ".uploads"))
	if len(files) != 0 {
		t.Fatal("archive cleanup", files)
	}
	pull := token(deployer, "repository:notes:pull,push")
	status, _ = call("HEAD", "/v2/notes/manifests/"+manifestDigest, pull, "", nil)
	if status != 200 {
		t.Fatal("deployer pull", status)
	}
	status, _ = call("POST", "/v2/notes/blobs/uploads/", pull, "", nil)
	if status != 401 {
		t.Fatal("deployer pushed", status)
	}
	cross := token(publisher, "repository:other:pull,push")
	status, _ = call("POST", "/v2/other/blobs/uploads/", cross, "", nil)
	if status != 401 {
		t.Fatal("cross-project push", status)
	}
	p, err := db.Authenticate(ctx, auth.HashToken(publisher))
	if err != nil {
		t.Fatal(err)
	}
	if err = db.RevokeToken(ctx, p.ID, "owner"); err != nil {
		t.Fatal(err)
	}
	req, _ := http.NewRequest("GET", apiServer.URL+"/registry/token?service=ctl-registry", nil)
	req.SetBasicAuth("ctl", publisher)
	res, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatal("revoked credential accepted", res.StatusCode)
	}
}
