package httpapi

import (
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestNodeInstallOwnerAndTargetBoundary(t *testing.T) {
	s := New(nil, Options{OwnerHash: auth.HashToken("owner")})
	for _, tc := range []struct {
		method, path, token, body string
		status                    int
	}{
		{"GET", "/api/v1/node-installations", "", "", 401},
		{"POST", "/api/v1/node-installations", "", `{}`, 401},
		{"POST", "/api/v1/node-installations/probe", "", `{}`, 401},
		{"POST", "/api/v1/node-installations/probe", "owner", `{"host":"127.0.0.1","port":22,"user":"root"}`, 422},
		{"POST", "/api/v1/node-installations/probe", "owner", `{"host":"server","port":22,"user":"root","password":"secret"}`, 400},
		{"POST", "/api/v1/node-installations", "owner", `{}`, 400},
	} {
		r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
		r.Header.Set("Content-Type", "application/json")
		if tc.token != "" {
			r.Header.Set("Authorization", "Bearer "+tc.token)
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Fatalf("%s %d %s", tc.path, w.Code, w.Body.String())
		}
		if strings.Contains(w.Body.String(), "secret") {
			t.Fatal("password reflected")
		}
	}
}
