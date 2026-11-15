package domain

import (
	"strings"
	"testing"
)

func multiRelease() Release {
	return Release{SchemaVersion: 3, Images: map[string]string{"api": "registry.example/notes@sha256:" + strings.Repeat("a", 64), "postgres": "registry.example/notes@sha256:" + strings.Repeat("b", 64)}, Capabilities: []string{"multi_service_v1"}, MinimumDeployctlVersion: "1.20.0"}
}
func TestMultiReleaseImagesAreScopedAndComplete(t *testing.T) {
	r := multiRelease()
	if r.ValidateDockerImages() != nil || r.ImageRepository() != "registry.example/notes" || len(r.ImageReferences()) != 2 {
		t.Fatal("valid release rejected")
	}
	cases := []func(*Release){
		func(r *Release) { r.Image = r.Images["api"] }, func(r *Release) { r.Capabilities = nil }, func(r *Release) { r.MinimumDeployctlVersion = "1.19.5" },
		func(r *Release) { r.Images["postgres"] = "registry.example/other@sha256:" + strings.Repeat("b", 64) }, func(r *Release) { r.Images["api"] = "notes:latest" },
		func(r *Release) { r.Images = nil }, func(r *Release) { r.SchemaVersion = 4 },
	}
	for _, mutate := range cases {
		r := multiRelease()
		mutate(&r)
		if r.ValidateDockerImages() == nil {
			t.Fatal("ambiguous release accepted")
		}
	}
	legacy := Release{Image: multiRelease().Images["api"]}
	if legacy.ValidateDockerImages() != nil || len(legacy.ImageReferences()) != 1 {
		t.Fatal("legacy broken")
	}
	legacy.Images = multiRelease().Images
	if legacy.ValidateDockerImages() == nil {
		t.Fatal("mixed legacy accepted")
	}
}
func TestMultiReleaseRequiresExplicitNodeCapability(t *testing.T) {
	r := multiRelease()
	for _, n := range []Node{{AgentVersion: "1.20.0"}, {AgentVersion: "1.19.5", Capabilities: []string{"multi_service_v1"}}, {AgentVersion: "invalid", Capabilities: []string{"multi_service_v1"}}} {
		if n.SupportsRelease(r) {
			t.Fatal("incapable agent accepted")
		}
	}
	if !(Node{AgentVersion: "1.20.0", Capabilities: []string{"multi_service_v1"}}).SupportsRelease(r) {
		t.Fatal("capable agent rejected")
	}
	if !(Node{}).SupportsRelease(Release{Image: r.Images["api"]}) {
		t.Fatal("legacy blocked")
	}
}

func TestMultiReleaseResolutionPreservesSecretClassification(t *testing.T) {
	r := multiRelease()
	r.Project = "notes"
	r.ID = strings.Repeat("a", 32)
	r.Version = "v2.0.0"
	r.SHA256 = strings.Repeat("b", 64)
	rev := Revision{ID: strings.Repeat("c", 32), Revision: 1, Configuration: Configuration{RuntimeEnv: map[string]Variable{"HOST": {Value: "postgres"}, "PASSWORD": {Value: "test-secret", Secret: true}}}}
	resolution := r.DockerResolution("prod", "/package", rev)
	if resolution["schema_version"] != 3 || resolution["minimum_client_version"] != "1.20.0" {
		t.Fatal("old-client gate absent")
	}
	cfg := resolution["configuration"].(map[string]any)
	if cfg["config"].(map[string]string)["HOST"] != "postgres" || cfg["secrets"].(map[string]string)["PASSWORD"] != "test-secret" {
		t.Fatal("classification lost")
	}
	if _, exists := cfg["config"].(map[string]string)["PASSWORD"]; exists {
		t.Fatal("secret leaked into public configuration")
	}
	legacy := Release{Image: r.Images["api"]}
	if legacy.DockerResolution("prod", "/package", rev)["schema_version"] != 1 {
		t.Fatal("legacy protocol changed")
	}
}
