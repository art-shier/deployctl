package store

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func TestDiscoveredNodeRequiresAuthorization(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	token, expires, err := s.CreateNodeEnrollment(ctx, "owner")
	if err != nil || token == "" || !expires.After(time.Now()) {
		t.Fatal("credential", err)
	}
	nodes, err := s.ListNodes(ctx)
	if err != nil || len(nodes) != 0 {
		t.Fatal("credential must not create node", nodes, err)
	}
	id, credential, err := s.EnrollNodeNamed(ctx, auth.HashToken(token), "test", "linux", "host-a")
	if err != nil {
		t.Fatal(err)
	}
	if got, err := s.AuthenticateNode(ctx, auth.HashToken(credential)); err != nil || got != id {
		t.Fatal("node identity", err)
	}
	nodes, err = s.ListNodes(ctx)
	if err != nil || len(nodes) != 1 {
		t.Fatal(nodes, err)
	}
	n := nodes[0]
	if !n.PendingAuthorization || n.Name != "host-a" || !n.Enabled || len(n.Projects)+len(n.Groups)+len(n.Environments) != 0 {
		t.Fatal("discovery", n)
	}
	if _, _, err = s.HeartbeatNode(ctx, id, "test", "linux", true, nil); err != nil {
		t.Fatal(err)
	}
	if task, err := s.PollNode(ctx, id); err != nil || task != nil {
		t.Fatal("pending poll", task, err)
	}
	req := domain.TaskRequest{NodeID: id, Environment: "prod", Action: "install", RequestID: domain.NewID()}
	if _, err = s.CreateTask(ctx, "notes", req, auth.Principal{ID: "owner", Role: "owner"}); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("pending task accepted", err)
	}
	n.Name = "renamed"
	n, err = s.UpdateNode(ctx, id, n.NodeInput, "owner")
	if err != nil || !n.PendingAuthorization {
		t.Fatal("rename approved node", err)
	}
	n.Projects = []string{"notes"}
	n, err = s.UpdateNode(ctx, id, n.NodeInput, "owner")
	if err != nil || !n.PendingAuthorization {
		t.Fatal("incomplete grants approved node", err)
	}
	n.Environments = []string{"prod"}
	n, err = s.UpdateNode(ctx, id, n.NodeInput, "owner")
	if err != nil || n.PendingAuthorization || !n.Allows(domain.Project{Slug: "notes"}, "prod") {
		t.Fatal("authorization", n, err)
	}
	// Pending state is a separate boundary even if a record has scopes.
	n.PendingAuthorization = true
	if n.Allows(domain.Project{Slug: "notes"}, "prod") {
		t.Fatal("pending state ignored")
	}
	rotated, _, err := s.RotateNodeEnrollment(ctx, id, "owner")
	if err != nil {
		t.Fatal(err)
	}
	same, _, err := s.EnrollNodeNamed(ctx, auth.HashToken(rotated), "test", "linux", "changed-hostname")
	if err != nil || same != id {
		t.Fatal("rotation changed identity", err)
	}
	nodes, _ = s.ListNodes(ctx)
	if nodes[0].Name != "renamed" || nodes[0].PendingAuthorization || len(nodes[0].Projects) != 1 {
		t.Fatal("rotation lost grants", nodes)
	}
}

func TestDiscoveryCredentialExpiresAndIsSingleUse(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	expired, _, err := s.CreateNodeEnrollment(ctx, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, "UPDATE ctl_node_enrollments SET expires_at=now()-interval '1 second'"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(expired), "test", "linux"); !errors.Is(err, domain.ErrUnauthorized) {
		t.Fatal("expired accepted", err)
	}
	token, _, err := s.CreateNodeEnrollment(ctx, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNodeNamed(ctx, auth.HashToken(token), "test", "linux", "bad\nname"); !errors.Is(err, domain.ErrInvalid) {
		t.Fatal("invalid name", err)
	}
	results := make(chan error, 8)
	for i := 0; i < 8; i++ {
		go func() { _, _, e := s.EnrollNode(ctx, auth.HashToken(token), "test", "linux"); results <- e }()
	}
	successes := 0
	for i := 0; i < 8; i++ {
		e := <-results
		if e == nil {
			successes++
		} else if !errors.Is(e, domain.ErrUnauthorized) {
			t.Fatal(e)
		}
	}
	nodes, err := s.ListNodes(ctx)
	if successes != 1 || err != nil || len(nodes) != 1 || nodes[0].Name == "" {
		t.Fatal("one-use", successes, nodes, err)
	}
}
