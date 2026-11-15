package domain

import (
	"slices"
	"strings"
)

// ImageReferences returns every unique digest used by services and one-shot jobs.
func (r Release) ImageReferences() []string {
	if r.SchemaVersion != 3 {
		if r.Image == "" {
			return nil
		}
		return []string{r.Image}
	}
	out := []string{}
	for _, image := range r.Images {
		if !slices.Contains(out, image) {
			out = append(out, image)
		}
	}
	slices.Sort(out)
	return out
}
func (r Release) ImageRepository() string {
	refs := r.ImageReferences()
	if len(refs) == 0 {
		return ""
	}
	return strings.Split(refs[0], "@")[0]
}
func (r Release) ValidateDockerImages() error {
	if r.SchemaVersion != 3 {
		if r.SchemaVersion != 0 && r.SchemaVersion != 1 && r.SchemaVersion != 2 || len(r.Images) != 0 || len(r.Capabilities) != 0 || r.MinimumDeployctlVersion != "" {
			return ErrInvalid
		}
		return ValidateImage(r.Image)
	}
	if r.Image != "" || len(r.Images) < 1 || len(r.Images) > 64 || !slices.Equal(r.Capabilities, []string{"multi_service_v1"}) || r.MinimumDeployctlVersion != "1.20.0" {
		return ErrInvalid
	}
	repository := ""
	for name, image := range r.Images {
		if ValidateName(name, 48) != nil || ValidateImage(image) != nil {
			return ErrInvalid
		}
		current := strings.Split(image, "@")[0]
		if repository != "" && current != repository {
			return ErrInvalid
		}
		repository = current
	}
	return nil
}
func ValidateCapabilities(values []string) error {
	if len(values) > 32 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, v := range values {
		if v == "" || len(v) > 64 || seen[v] {
			return ErrInvalid
		}
		for _, c := range v {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '_') {
				return ErrInvalid
			}
		}
		seen[v] = true
	}
	return nil
}
func (n Node) SupportsRelease(r Release) bool {
	if r.SchemaVersion != 3 {
		return r.SchemaVersion >= 0 && r.SchemaVersion <= 2 && len(r.Images) == 0 && len(r.Capabilities) == 0
	}
	if r.ValidateDockerImages() != nil {
		return false
	}
	current, ok := updateVersion("v" + strings.TrimPrefix(n.AgentVersion, "v"))
	if !ok {
		return false
	}
	minimum, ok := updateVersion("v" + r.MinimumDeployctlVersion)
	if !ok {
		return false
	}
	for _, capability := range r.Capabilities {
		if !slices.Contains(n.Capabilities, capability) {
			return false
		}
	}
	for i := range current {
		if current[i] != minimum[i] {
			return current[i] > minimum[i]
		}
	}
	return true
}

// DockerResolution is only returned through authenticated resolve/task endpoints.
func (r Release) DockerResolution(environment, packagePath string, rev Revision) map[string]any {
	release := map[string]any{"id": r.ID, "version": r.Version, "image": r.Image, "sha256": r.SHA256, "package_path": packagePath}
	cfg := map[string]any{"id": rev.ID, "revision": rev.Revision, "runtime_env": Values(rev.Configuration.RuntimeEnv), "install_params": Values(rev.Configuration.InstallParams), "deployment_defaults": rev.Configuration.DeploymentDefaults}
	out := map[string]any{"schema_version": 1, "minimum_client_version": "1.7.0", "project": r.Project, "environment": environment, "release": release, "configuration": cfg}
	if r.SchemaVersion == 3 {
		out["schema_version"] = 3
		out["minimum_client_version"] = r.MinimumDeployctlVersion
		out["capabilities"] = r.Capabilities
		delete(release, "image")
		release["images"] = r.Images
		config, secrets := map[string]string{}, map[string]string{}
		for key, value := range rev.Configuration.RuntimeEnv {
			if value.Secret {
				secrets[key] = value.Value
			} else {
				config[key] = value.Value
			}
		}
		delete(cfg, "runtime_env")
		cfg["config"] = config
		cfg["secrets"] = secrets
	}
	return out
}
