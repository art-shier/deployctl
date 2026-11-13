package nodeinstall

import (
	"os"
	"os/exec"
	"testing"
)

func TestVersionFloor(t *testing.T) {
	for _, tc := range []struct {
		v, floor string
		ok       bool
	}{{"20.10.1", "20.10", true}, {"19.3", "20.10", false}, {"v2.30.0", "2.30", true}, {"2.29.9", "2.30", false}, {"garbage", "2.30", false}} {
		err := exec.Command("bash", "-c", `source ./bootstrap.sh; version_at_least "$1" "$2"`, "test", tc.v, tc.floor).Run()
		if (err == nil) != tc.ok {
			t.Errorf("%s >= %s: %v", tc.v, tc.floor, err)
		}
	}
}

func TestExistingDockerPolicy(t *testing.T) {
	for _, tc := range []struct {
		name, engine, compose string
		fail                  bool
	}{{"ready", "27.0.1", "v2.30.1", false}, {"old_engine", "19.3", "v2.30.1", true}, {"old_compose", "27.0.1", "v2.29.0", true}, {"invalid_version", "nonsense", "v2.30.1", true}} {
		t.Run(tc.name, func(t *testing.T) {
			// Function mocks ensure this test can never reach real package/service managers.
			script := `source ./bootstrap.sh; exec 3>&1; docker() { case "$1" in version) echo "$ENGINE";; compose) echo "$COMPOSE";; info) return 0;; *) exit 88;; esac; }; check_docker`
			cmd := exec.Command("bash", "-c", script)
			cmd.Env = append(os.Environ(), "ENGINE="+tc.engine, "COMPOSE="+tc.compose)
			out, err := cmd.CombinedOutput()
			if (err != nil) != tc.fail {
				t.Fatalf("%s %v", out, err)
			}
		})
	}
}
func TestSupportedDistribution(t *testing.T) {
	for _, tc := range []struct {
		id, v string
		ok    bool
	}{{"ubuntu", "22.04", true}, {"ubuntu", "20.04", false}, {"debian", "12", true}, {"debian", "11", false}, {"centos", "9", false}} {
		err := exec.Command("bash", "-c", `source ./bootstrap.sh; ID=$1; VERSION_ID=$2; supported_apt`, "test", tc.id, tc.v).Run()
		if (err == nil) != tc.ok {
			t.Fatal(tc, err)
		}
	}
}
