package domain

import "testing"

func TestRemoteValidation(t *testing.T) {
	for _, action := range []string{"install", "upgrade", "rollback"} {
		if err := (TaskRequest{NodeID: NewID(), Environment: "prod", Action: action, RequestID: NewID()}).Validate(); err != nil {
			t.Fatal(action, err)
		}
	}
	for _, r := range []TaskRequest{{NodeID: NewID(), Environment: "prod", Action: "shell", RequestID: NewID()}, {NodeID: NewID(), Environment: "prod", Action: "rollback", Version: "v1.0.0", RequestID: NewID()}} {
		if r.Validate() == nil {
			t.Fatal("invalid request accepted")
		}
	}
	if ValidTaskStage("secret output") {
		t.Fatal("arbitrary progress accepted")
	}
	if !ValidTaskStage("interrupted") {
		t.Fatal("fixed stage rejected")
	}
}

func TestRemoteNodeGrantIntersection(t *testing.T) {
	p := Project{Slug: "notes", Group: "team"}
	n := Node{NodeInput: NodeInput{Enabled: true, Groups: []string{"team"}, Environments: []string{"prod"}}}
	if !n.Allows(p, "prod") || n.Allows(p, "test") {
		t.Fatal("group/environment intersection")
	}
	p.DeploymentType = "static"
	if n.Allows(p, "prod") {
		t.Fatal("Docker agent accepted a static project")
	}
	p.DeploymentType = "docker"
	n.ExcludedProjects = []string{"notes"}
	if n.Allows(p, "prod") {
		t.Fatal("exclusion ignored")
	}
	n.ExcludedProjects = nil
	n.Environments = nil
	if n.Allows(p, "prod") {
		t.Fatal("empty environment grant")
	}
	n.Environments = []string{"prod"}
	n.Enabled = false
	if n.Allows(p, "prod") {
		t.Fatal("disabled node grant")
	}
}

func TestRemoteReportProtocolValidation(t *testing.T) {
	valid := []TaskReport{{Status: "running", Stage: "deploying"}, {Status: "succeeded", Stage: "completed"}, {Status: "failed", Stage: "execution_failed"}, {Status: "needs_attention", Stage: "interrupted"}}
	for _, r := range valid {
		if err := r.Validate(); err != nil {
			t.Fatal(r, err)
		}
	}
	invalid := []TaskReport{{Status: "cancelled", Stage: "accepted"}, {Status: "succeeded", Stage: "deploying"}, {Status: "failed", Stage: "completed"}, {Status: "running", Stage: "completed"}, {Status: "needs_attention", Stage: "arbitrary output"}}
	for _, r := range invalid {
		if r.Validate() == nil {
			t.Fatal("unsafe report accepted", r)
		}
	}
}
