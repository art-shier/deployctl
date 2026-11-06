package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type remoteFixtureVerifier struct{}

func (remoteFixtureVerifier) CheckManifest(context.Context, string, string) error { return nil }

// Real HTTP, SQL, crypto, archives and Python runtime; only Docker is simulated.
func TestRemotePythonAgentProtocol(t *testing.T) {
	python := os.Getenv("CTL_TEST_PYTHON")
	if python == "" {
		t.Skip("set CTL_TEST_PYTHON for Go/Python protocol integration")
	}
	db := testutil.Store(t)
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	token, err := auth.NewToken()
	if err != nil {
		t.Fatal(err)
	}
	api := New(db, Options{OwnerHash: auth.HashToken(token), ArtifactsDir: t.TempDir(),
		RegistryPublicHost: "registry.example", Verifier: remoteFixtureVerifier{},
		Signer: &auth.RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}})
	server := httptest.NewServer(api)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	input, _ := json.Marshal(map[string]string{"origin": server.URL, "token": token})
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, python, filepath.Join(root, "tests", "remote_agent_protocol.py"))
	command.Dir = root
	command.Stdin = bytes.NewReader(input)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("Python protocol fixture: %v\n%s", err, output)
	}
	t.Log(string(output))
}
