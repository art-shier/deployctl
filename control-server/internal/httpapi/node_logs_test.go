package httpapi

import (
	"context"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLogRelayIsolationCancellationAndCapacity(t *testing.T) {
	b := newLogBroker()
	q := domain.LogQuery{NodeID: "node", Project: "notes", InstanceID: "instance"}
	ticket, err := b.add(q, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if got := b.take("other"); got != nil {
		t.Fatal("cross-node delivery")
	}
	got := b.take("node")
	if got == nil || got.query.ID != ticket.query.ID {
		t.Fatal("missing query")
	}
	if b.finish("other", ticket.query.ID, domain.LogResult{Text: "secret"}) {
		t.Fatal("cross-node response accepted")
	}
	if !b.finish("node", ticket.query.ID, domain.LogResult{Text: "hello"}) {
		t.Fatal("response missing")
	}
	select {
	case result := <-ticket.result:
		if result.Text != "hello" {
			t.Fatal(result)
		}
	default:
		t.Fatal("no result")
	}
	b.remove(ticket.query.ID)
	if b.finish("node", ticket.query.ID, domain.LogResult{}) {
		t.Fatal("late result accepted")
	}
	a, _ := b.add(q, "owner")
	defer b.remove(a.query.ID)
	c, _ := b.add(q, "owner")
	defer b.remove(c.query.ID)
	if _, err := b.add(q, "owner"); err == nil {
		t.Fatal("unbounded node requests")
	}
}

func TestLogQueryValidation(t *testing.T) {
	q := domain.LogQuery{Source: "runtime", Tail: 200}
	if q.Validate() != nil {
		t.Fatal("valid query rejected")
	}
	for _, bad := range []domain.LogQuery{{Source: "shell", Tail: 200}, {Source: "archive", ArchiveID: "../secret", Tail: 200}, {Source: "runtime", Tail: 2001}, {Source: "runtime", Tail: 200, Since: "yesterday"}} {
		if bad.Validate() == nil {
			t.Fatal("invalid query accepted", bad)
		}
	}
}

func TestLogRelayWaitCancellation(t *testing.T) {
	b := newLogBroker()
	ticket, _ := b.add(domain.LogQuery{NodeID: "node"}, "owner")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	_, err := b.wait(ctx, ticket)
	if err == nil || time.Since(started) > time.Second {
		t.Fatal("cancel not propagated")
	}
	if b.take("node") != nil {
		t.Fatal("cancelled request retained")
	}
}

func TestLogHTTPAuthorizationAndRevocation(t *testing.T) {
	db := testutil.Store(t)
	ctx := context.Background()
	_, err := db.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.example/notes"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{}, "stable", "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 7}, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	node, once, _, err := db.CreateNode(ctx, domain.NodeInput{Name: "host", Enabled: true, Projects: []string{"notes"}, Environments: []string{"prod"}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, nodeToken, err := db.EnrollNode(ctx, auth.HashToken(once), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = db.HeartbeatNode(ctx, node.ID, "test", "linux", true, nil)
	if err != nil {
		t.Fatal(err)
	}
	task, err := db.CreateTask(ctx, "notes", domain.TaskRequest{NodeID: node.ID, Environment: "prod", Action: "install", RequestID: domain.NewID()}, auth.Principal{ID: "owner", Role: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	srv := New(db, Options{OwnerHash: auth.HashToken("owner")})
	call := func(path, token, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		srv.ServeHTTP(w, r)
		return w
	}

	for _, scope := range []struct{ role, env string }{{"publisher", "prod"}, {"deployer", "staging"}} {
		raw := "token-" + scope.role
		_, err := db.CreateToken(ctx, domain.Token{Name: "restricted", Role: scope.role, Project: "notes", Environments: []string{scope.env}, ExpiresAt: time.Now().Add(time.Hour)}, auth.HashToken(raw), "owner")
		if err != nil {
			t.Fatal(err)
		}
		if w := call("/api/v1/projects/notes/instances/"+task.InstanceID+"/logs", raw, `{"source":"runtime","tail":200}`); w.Code != 403 {
			t.Fatal("scope escaped", scope, w.Code, w.Body.String())
		}
	}
	path := "/api/v1/projects/notes/instances/" + task.InstanceID + "/logs"
	if w := call(path, nodeToken, `{"source":"runtime","tail":200}`); w.Code != 401 {
		t.Fatal("node escaped identity boundary", w.Code)
	}
	if w := call(path, "owner", `{"source":"deployment","tail":200,"task_id":"`+domain.NewID()+`"}`); w.Code != 404 {
		t.Fatal("foreign task accepted", w.Code, w.Body.String())
	}
	for _, revoke := range []bool{false, true} {
		result := make(chan *httptest.ResponseRecorder, 1)
		go func() { result <- call(path, "owner", `{"source":"runtime","tail":200}`) }()
		poll := call("/api/v1/node/logs/poll", nodeToken, `{}`)
		if poll.Code != 200 {
			t.Fatal(poll.Code, poll.Body.String())
		}
		var payload struct {
			Query domain.LogQuery `json:"query"`
		}
		json.Unmarshal(poll.Body.Bytes(), &payload)
		if payload.Query.Project != "notes" || payload.Query.Environment != "prod" || payload.Query.InstanceID != task.InstanceID {
			t.Fatal(payload)
		}
		if revoke {
			in := node.NodeInput
			in.Enabled = false
			if _, err := db.UpdateNode(ctx, node.ID, in, "owner"); err != nil {
				t.Fatal(err)
			}
		}
		w := call("/api/v1/node/logs/"+payload.Query.ID+"/result", nodeToken, `{"text":"diagnostic evidence","sources":[],"truncated":false,"error":""}`)
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		response := <-result
		if revoke {
			if response.Code != 403 || strings.Contains(response.Body.String(), "diagnostic evidence") {
				t.Fatal("revocation leaked logs", response.Code)
			}
		} else if response.Code != 200 || !strings.Contains(response.Body.String(), "diagnostic evidence") {
			t.Fatal(response.Code, response.Body.String())
		}
	}
}
