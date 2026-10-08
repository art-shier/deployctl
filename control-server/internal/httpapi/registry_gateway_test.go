package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
)

func TestNativeManifestPushWaitsForRepositoryOperation(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	_, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.test/notes", DefaultEnvironment: "prod"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	backendCalled := make(chan struct{}, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { backendCalled <- struct{}{}; w.WriteHeader(201) }))
	defer backend.Close()
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	signer := &auth.RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}
	api := New(db, Options{RegistryPublicHost: "registry.test", Signer: signer, Verifier: registry.Verifier{PublicHost: "registry.test", InternalURL: backend.URL, Signer: signer}})
	token, _ := signer.Sign(auth.Principal{Role: "publisher", Project: "notes"}, []auth.Access{{Type: "repository", Name: "notes", Actions: []string{"pull", "push"}}}, time.Now())
	entered := make(chan struct{})
	resume := make(chan struct{})
	finished := make(chan error, 1)
	var once sync.Once
	defer once.Do(func() { close(resume) })
	go func() {
		finished <- db.ManageImages(ctx, "notes", "owner", "image.test", func(domain.Project, []string) error { close(entered); <-resume; return nil })
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("image lock never acquired")
	}
	result := make(chan int, 1)
	go func() {
		req := httptest.NewRequest("PUT", "/v2/notes/manifests/native", bytes.NewBufferString(`{"schemaVersion":2}`))
		req.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		api.ServeHTTP(w, req)
		result <- w.Code
	}()
	select {
	case status := <-result:
		t.Fatal("native push bypassed shared lock", status)
	case <-backendCalled:
		t.Fatal("backend mutation occurred under image lock")
	case <-time.After(100 * time.Millisecond):
	}
	once.Do(func() { close(resume) })
	if err = <-finished; err != nil {
		t.Fatal(err)
	}
	select {
	case status := <-result:
		if status != 201 {
			t.Fatal("gateway did not forward push", status)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("native push stuck after release")
	}
}

func TestUnauthenticatedManifestWriteNeverReachesRegistry(t *testing.T) {
	calls := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(201) }))
	defer backend.Close()
	api := New(nil, Options{PublicOrigin: "https://ctl.test", RegistryPublicHost: "registry.test", Verifier: registry.Verifier{InternalURL: backend.URL, PublicHost: "registry.test"}})
	req := httptest.NewRequest("PUT", "/v2/notes/manifests/native", bytes.NewBufferString(`{}`))
	w := httptest.NewRecorder()
	api.ServeHTTP(w, req)
	if w.Code != 401 || calls != 0 || w.Header().Get("WWW-Authenticate") == "" {
		t.Fatal("uncoordinated write forwarded", w.Code, calls)
	}
}
