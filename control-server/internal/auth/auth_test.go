package auth

import "testing"

func TestPublisherCannotReadProduction(t *testing.T) {
	p := Principal{Role: "publisher", Project: "notes"}
	if !p.Can("release.publish", "notes", "") || !p.Can("registry.push", "notes", "") {
		t.Fatal("publisher denied")
	}
	for _, action := range []string{"resolve", "configuration.read", "token.create", "unknown"} {
		if p.Can(action, "notes", "prod") {
			t.Fatal("publisher overprivileged", action)
		}
	}
	if p.Can("release.publish", "other", "") {
		t.Fatal("cross project")
	}
}
func TestDeployerProjectEnvironmentScope(t *testing.T) {
	p := Principal{Role: "deployer", Project: "notes", Environments: []string{"prod"}}
	if !p.Can("resolve", "notes", "prod") || !p.Can("registry.pull", "notes", "") {
		t.Fatal("deployer denied")
	}
	if p.Can("resolve", "notes", "test") || p.Can("resolve", "other", "prod") || p.Can("registry.push", "notes", "") {
		t.Fatal("scope escaped")
	}
}
func TestOpaqueTokenHash(t *testing.T) {
	token, err := NewToken()
	if err != nil {
		t.Fatal(err)
	}
	if len(token) < 40 || HashToken(token) == token || HashToken(token) != HashToken(token) {
		t.Fatal("invalid token hash")
	}
	other, _ := NewToken()
	if token == other {
		t.Fatal("reused token")
	}
}
