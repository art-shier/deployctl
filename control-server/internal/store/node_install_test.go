package store

import (
	"context"
	"encoding/json"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/nodeinstall"
	"strings"
	"testing"
)

func TestNodeInstallationLifecycle(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := nodeinstall.Request{Target: nodeinstall.Target{Host: "10.0.0.1", Port: 22, User: "root"}, ID: domain.NewID(), Fingerprint: "SHA256:key", Password: "ssh-password", SudoPassword: "sudo-password"}
	j, token, err := s.CreateNodeInstallation(ctx, r, "owner")
	if err != nil {
		t.Fatal(err)
	}
	again, duplicate, err := s.CreateNodeInstallation(ctx, r, "owner")
	if err != nil || duplicate != "" || again.ID != j.ID {
		t.Fatal("idempotency", err)
	}
	r.ID = domain.NewID()
	if _, _, err = s.CreateNodeInstallation(ctx, r, "owner"); err != domain.ErrConflict {
		t.Fatal("host mutex", err)
	}
	node, _, err := s.EnrollNode(ctx, auth.HashToken(token), "test", "linux")
	if err != nil {
		t.Fatal(err)
	}
	j, err = s.GetNodeInstallation(ctx, j.ID)
	if err != nil || j.NodeID != node {
		t.Fatal("registration correlation", j, err)
	}
	if ready, err := s.InstallationHeartbeat(ctx, j.ID); ready || err != nil {
		t.Fatal("false success", err)
	}
	_, err = s.pool.Exec(ctx, `UPDATE ctl_nodes SET data=jsonb_set(data,'{last_seen}',to_jsonb(now())) WHERE id=$1`, node)
	if err != nil {
		t.Fatal(err)
	}
	if ready, err := s.InstallationHeartbeat(ctx, j.ID); !ready || err != nil {
		t.Fatal("heartbeat", err)
	}
	if err = s.UpdateNodeInstallation(ctx, j.ID, "succeeded", "complete", ""); err != nil {
		t.Fatal(err)
	}
	jobs, err := s.ListNodeInstallations(ctx)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(jobs)
	if strings.Contains(string(raw), "password") || strings.Contains(string(raw), token) {
		t.Fatal("secret in job")
	}
	var row string
	if err = s.pool.QueryRow(ctx, `SELECT row_to_json(j)::text FROM ctl_node_installations j WHERE id=$1`, j.ID).Scan(&row); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(row, r.Password) || strings.Contains(row, r.SudoPassword) || strings.Contains(row, token) {
		t.Fatal("persisted secret")
	}
}
func TestNodeInstallationExpiryRevokesEnrollment(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := nodeinstall.Request{Target: nodeinstall.Target{Host: "10.0.0.2", Port: 22, User: "root"}, ID: domain.NewID(), Fingerprint: "SHA256:key", Password: "secret"}
	j, token, err := s.CreateNodeInstallation(ctx, r, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if err = s.UpdateNodeInstallation(ctx, j.ID, "failed", "checking", "agent_exists"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.EnrollNode(ctx, auth.HashToken(token), "test", "linux"); err == nil {
		t.Fatal("failed token usable")
	}
	r.ID = domain.NewID()
	j, _, err = s.CreateNodeInstallation(ctx, r, "owner")
	if err != nil {
		t.Fatal(err)
	}
	s.pool.Exec(ctx, `UPDATE ctl_node_installations SET expires_at=now()-interval '1 minute' WHERE id=$1`, j.ID)
	jobs, err := s.ListNodeInstallations(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, job := range jobs {
		if job.ID == j.ID && (job.Status != "failed" || job.ErrorCode != "interrupted") {
			t.Fatal(job)
		}
	}
}

func TestNodeInstallationCapacityAndRequestConflict(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	r := nodeinstall.Request{Target: nodeinstall.Target{Host: "10.0.0.1", Port: 22, User: "root"}, ID: domain.NewID(), Fingerprint: "SHA256:key", Password: "secret"}
	first, _, err := s.CreateNodeInstallation(ctx, r, "owner")
	if err != nil {
		t.Fatal(err)
	}
	r.Host = "10.0.0.2"
	if _, _, err = s.CreateNodeInstallation(ctx, r, "owner"); err != domain.ErrConflict {
		t.Fatal("id reused for other target", err)
	}
	for _, host := range []string{"10.0.0.2", "10.0.0.3", "10.0.0.4"} {
		r.Host = host
		r.ID = domain.NewID()
		if _, _, err = s.CreateNodeInstallation(ctx, r, "owner"); err != nil {
			t.Fatal(err)
		}
	}
	r.Host = "10.0.0.5"
	r.ID = domain.NewID()
	if _, _, err = s.CreateNodeInstallation(ctx, r, "owner"); err != domain.ErrConflict {
		t.Fatal("capacity not enforced", err)
	}
	if err = s.UpdateNodeInstallation(ctx, first.ID, "failed", "connecting", "ssh_unreachable"); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.CreateNodeInstallation(ctx, r, "owner"); err != nil {
		t.Fatal("capacity not released", err)
	}
}
