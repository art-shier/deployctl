package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"net/http/httptest"
	"testing"
)

func TestApplicationLogHTTP(t *testing.T) {
	db := testutil.Store(t)
	db.CreateProject(context.Background(), domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.test/notes"}, "owner")
	owner, _ := auth.NewToken()
	s := New(db, Options{OwnerHash: auth.HashToken(owner)})
	base := "/api/v1/projects/notes/environments/prod/logging"
	call := func(method, path, body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, bytes.NewBufferString(body))
		r.Header.Set("Content-Type", "application/json")
		if token != "" {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	if w := call("GET", base+"/rules", "", owner); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("GET", base+"/events", "", ""); w.Code != 401 {
		t.Fatal(w.Code)
	}
	w := call("POST", base+"/query-permission", `{"attributes":[{"type":"device","value":"d"}]}`, owner)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var d domain.LogDecision
	json.Unmarshal(w.Body.Bytes(), &d)
	if d.Allowed {
		t.Fatal("default allowed")
	}
	w = call("PUT", base+"/rules", `{"version":0,"rules":[{"id":"g","name":"all","kind":"percentage","enabled":true,"percentage":100}]}`, owner)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", base+"/events?limit=bad", "", owner); w.Code != 400 {
		t.Fatal(w.Code)
	}
	w = call("POST", "/api/v1/tokens", `{"name":"scoped","role":"deployer","projects":["notes"],"environments":["prod"]}`, owner)
	var minted struct {
		Token string `json:"token"`
	}
	json.Unmarshal(w.Body.Bytes(), &minted)
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", base+"/events", "", minted.Token); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w = call("GET", "/api/v1/projects/notes/environments/other/logging/events", "", minted.Token); w.Code != 403 {
		t.Fatal("env isolation", w.Code)
	}
	if w = call("PUT", base+"/rules", `{"version":1,"rules":[]}`, minted.Token); w.Code != 403 {
		t.Fatal("rule admin", w.Code)
	}
}
