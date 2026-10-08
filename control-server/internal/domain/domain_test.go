package domain

import (
	"fmt"
	"strings"
	"testing"
)

func TestValidateValuesLiteralAndLimits(t *testing.T) {
	values := map[string]Variable{"TEXT": {Value: " $ # = "}, "EMPTY": {Value: ""}, "LONG": {Value: strings.Repeat("字", 4096)}}
	if err := ValidateVariables(values, true); err != nil {
		t.Fatal(err)
	}
	values["LONG"] = Variable{Value: strings.Repeat("a", 4097)}
	if ValidateVariables(values, true) == nil {
		t.Fatal("oversized value accepted")
	}
	values = map[string]Variable{}
	for i := 0; i < 128; i++ {
		values[fmt.Sprintf("KEY_%d", i)] = Variable{Value: "x"}
	}
	if err := ValidateVariables(values, true); err != nil {
		t.Fatal(err)
	}
	values["EXTRA"] = Variable{Value: "x"}
	if ValidateVariables(values, true) == nil {
		t.Fatal("129 keys accepted")
	}
	for _, key := range []string{"APP_VERSION", "DEPLOYCTL_FAKE", "BASH_ENV", "lower"} {
		if ValidateVariables(map[string]Variable{key: {Value: "x"}}, true) == nil {
			t.Fatal("reserved or invalid key accepted", key)
		}
	}
	for _, value := range []string{"line\nnext", "bad\x00", "split\u2028", "bad\x80"} {
		if ValidateVariables(map[string]Variable{"TEXT": {Value: value}}, true) == nil {
			t.Fatal("invalid value accepted")
		}
	}
}

func TestSecretKeepSetRemoveAndEmpty(t *testing.T) {
	original := map[string]Variable{"PASSWORD": {Value: "secret", Secret: true}, "REMOVE": {Value: "old"}}
	next, err := ApplyChanges(original, []Change{{Key: "PASSWORD", Operation: "keep"}, {Key: "EMPTY", Operation: "set", Value: stringPointer("")}, {Key: "REMOVE", Operation: "remove"}}, true)
	if err != nil {
		t.Fatal(err)
	}
	if next["PASSWORD"] != original["PASSWORD"] || next["EMPTY"].Value != "" || len(next) != 2 {
		t.Fatal("keep/remove/empty lost")
	}
	if len(original) != 2 {
		t.Fatal("mutated old revision")
	}
	_, err = ApplyChanges(original, []Change{{Key: "PASSWORD", Operation: "keep", Value: stringPointer("******")}}, true)
	if err == nil {
		t.Fatal("placeholder accepted")
	}
	if _, err = ApplyChanges(original, []Change{{Key: "MISSING", Operation: "keep"}}, true); err == nil {
		t.Fatal("kept nonexistent value")
	}
	if _, err = ApplyChanges(original, []Change{{Key: "PASSWORD", Operation: "remove"}, {Key: "PASSWORD", Operation: "set", Value: stringPointer("x")}}, true); err == nil {
		t.Fatal("duplicate accepted")
	}
}
func stringPointer(s string) *string { return &s }

func TestProjectAndEnvironmentIdentifiers(t *testing.T) {
	for _, s := range []string{"notes", "project-a", "ci-123"} {
		if ValidateName(s, 48) != nil {
			t.Fatal(s)
		}
	}
	for _, s := range []string{"../notes", "NOTES", "a--b", "x\n", strings.Repeat("a", 49)} {
		if ValidateName(s, 48) == nil {
			t.Fatal(s)
		}
	}
	if ValidateDeploymentDefaults(DeploymentDefaults{HostPort: 70000}) == nil {
		t.Fatal("invalid port accepted")
	}
	if ValidateDeploymentDefaults(DeploymentDefaults{BindAddress: "0.0.0.0;bad"}) == nil {
		t.Fatal("invalid bind accepted")
	}
}
