package domain

import "testing"

func TestStaticTypesAndTargetDirectories(t *testing.T) {
	for _, value := range []string{"", "docker", "static"} {
		got, err := DeploymentType(value)
		want := value
		if want == "" {
			want = "docker"
		}
		if err != nil || got != want {
			t.Fatalf("type %q: %q %v", value, got, err)
		}
	}
	if _, err := DeploymentType("website"); err == nil {
		t.Fatal("unknown type accepted")
	}
	for _, value := range []string{"", "/var/www/project-a", "/data/静态 文件"} {
		if ValidateTargetDir(value) != nil {
			t.Fatalf("valid target rejected: %q", value)
		}
	}
	for _, value := range []string{"/", "relative", "/var/../etc", "/var//www", "/var/www/", "/var/\\file", "/var/\nfile"} {
		if ValidateTargetDir(value) == nil {
			t.Fatalf("invalid target accepted: %q", value)
		}
	}
}
