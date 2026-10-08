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
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

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
	run := exec.Command("docker", "run", "-d", "--name", name, "-p", "127.0.0.1::5000", "-v", certPath+":/cert.pem:ro", "-e", "REGISTRY_AUTH=token", "-e", "REGISTRY_AUTH_TOKEN_REALM="+apiServer.URL+"/registry/token", "-e", "REGISTRY_AUTH_TOKEN_SERVICE=ctl-registry", "-e", "REGISTRY_AUTH_TOKEN_ISSUER=ctl-test", "-e", "REGISTRY_AUTH_TOKEN_ROOTCERTBUNDLE=/cert.pem", "registry:2.8.3")
	if out, e := run.CombinedOutput(); e != nil {
		t.Fatalf("registry start: %s", out)
	}
	t.Cleanup(func() { exec.Command("docker", "rm", "-f", "-v", name).Run() })
	out, err := exec.Command("docker", "port", name, "5000/tcp").Output()
	if err != nil {
		t.Fatal(err)
	}
	host := strings.TrimSpace(string(out))
	origin := "http://" + host
	api.options.RegistryPublicHost = host
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
		req, _ := http.NewRequest(method, origin+path, bytes.NewReader(raw))
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
	verifier := registry.Verifier{InternalURL: origin, PublicHost: host, Signer: signer}
	images, err := verifier.ListImages(ctx, host+"/notes")
	if err != nil || len(images) != 2 {
		t.Fatal("real registry image inventory", images, err)
	}
	if err = verifier.CheckManifest(ctx, host+"/notes@"+headers.Get("Docker-Content-Digest"), host+"/notes"); err != nil {
		t.Fatal("index verification", err)
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
