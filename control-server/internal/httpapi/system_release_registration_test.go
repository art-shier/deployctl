package httpapi

import (
	"context"
	"crypto/sha256"
	"fmt"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRegistrationHTTPAuthorizationAndOfflineCatalog(t *testing.T) {
	db := testutil.Store(t)
	s := New(db, Options{OwnerHash: auth.HashToken("owner")})
	call := func(method, path, token, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+token)
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	body := `{"version":"v1.17.0","published_at":"2026-11-01T00:00:00Z","cli_url":"https://unreachable.invalid/cli","server_url":"https://unreachable.invalid/server","cli_sha256":"` + strings.Repeat("a", 64) + `","server_sha256":"` + strings.Repeat("b", 64) + `"}`
	token, err := db.RotateReleaseRegistrationToken(context.Background(), "owner")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/api/v1/system-updates/releases", "/api/v1/system-updates/registration-token"} {
		if w := call("POST", path, token, body); w.Code != 401 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
	if w := call("POST", "/api/v1/system-updates/registrations", "owner", body); w.Code != 401 {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, code := range []int{201, 200} {
		if w := call("POST", "/api/v1/system-updates/registrations", token, body); w.Code != code {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	// A fresh server still sees the database record without a network lookup.
	s = New(db, Options{OwnerHash: auth.HashToken("owner")})
	if w := call("GET", "/api/v1/system-updates/catalog", "owner", ""); w.Code != 200 || !strings.Contains(w.Body.String(), "v1.17.0") {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestRegisteredPackageChecksumAndCache(t *testing.T) {
	p := NewRegisteredSystemCatalog(nil, t.TempDir())
	raw := "package"
	calls := 0
	p.client = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Header.Get("Authorization") != "" {
			t.Fatal("credential leaked")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(raw)), Header: make(http.Header)}, nil
	})}
	j := domain.UpdateJob{Kind: "node", Version: "v1.17.0", CLIURL: "https://packages.example/cli", CLISHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(raw)))}
	for i := 0; i < 2; i++ {
		if b, err := p.Artifact(context.Background(), j); err != nil || string(b) != raw {
			t.Fatal(string(b), err)
		}
	}
	if calls != 1 {
		t.Fatal("cache missed", calls)
	}
	j.CLISHA256 = strings.Repeat("a", 64)
	if _, err := p.Artifact(context.Background(), j); err == nil {
		t.Fatal("accepted mismatched checksum")
	}
}
