package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

type updateProtocolReleases struct{}

func (updateProtocolReleases) Releases(context.Context) ([]domain.SystemRelease, error) {
	out := []domain.SystemRelease{}
	for _, version := range []string{"v1.16.0", "v1.17.0"} {
		out = append(out, domain.SystemRelease{Version: version, PublishedAt: time.Now().UTC(), CLISHA256: strings.Repeat("a", 64), ServerSHA256: strings.Repeat("b", 64)})
	}
	return out, nil
}

func TestSystemUpdatesPythonProtocol(t *testing.T) {
	python := os.Getenv("CTL_TEST_PYTHON")
	if python == "" {
		t.Skip("set CTL_TEST_PYTHON for real updater protocol integration")
	}
	db := testutil.Store(t)
	token, err := auth.NewToken()
	if err != nil {
		t.Fatal(err)
	}
	api := New(db, Options{OwnerHash: auth.HashToken(token), SystemReleases: updateProtocolReleases{}})
	server := httptest.NewServer(api)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	input, _ := json.Marshal(map[string]string{"origin": server.URL, "token": token})
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, python, filepath.Join(root, "tests", "system_updates_protocol.py"))
	command.Dir = root
	command.Stdin = bytes.NewReader(input)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("Updater Python protocol fixture: %v\n%s", err, output)
	}
	if bytes.Contains(output, []byte(token)) {
		t.Fatal("fixture leaked owner credential")
	}
	t.Log(string(output))
}
