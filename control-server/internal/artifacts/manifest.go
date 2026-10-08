package artifacts

import (
	"crypto/sha256"
	"encoding/hex"
	"math"
	"net"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

func mapping(value any, allowed, required string) (map[string]any, bool) {
	m, ok := value.(map[string]any)
	if !ok {
		return nil, false
	}
	for k := range m {
		found := false
		for _, a := range strings.Fields(allowed) {
			if k == a {
				found = true
			}
		}
		if !found {
			return nil, false
		}
	}
	for _, r := range strings.Fields(required) {
		if _, ok := m[r]; !ok {
			return nil, false
		}
	}
	return m, true
}
func integer(value any, min, max int) bool { v, ok := value.(int); return ok && v >= min && v <= max }
func controls(s string) bool {
	for _, r := range s {
		if r < 32 || r >= 127 && r <= 159 || r == 0x2028 || r == 0x2029 {
			return true
		}
	}
	return false
}
func relative(value any) bool {
	s, ok := value.(string)
	if !ok || s == "" || strings.HasPrefix(s, "/") || strings.ContainsAny(s, "\\:\n") {
		return false
	}
	for _, part := range strings.Split(s, "/") {
		if part == ".." {
			return false
		}
	}
	return true
}
func fallback(m map[string]any, k string, d any) any {
	if v, ok := m[k]; ok {
		return v
	}
	return d
}
func normalizeDeployment(value any, project string) (map[string]any, error) {
	d, ok := mapping(value, "schema_version application build container health resources required_config", "schema_version application container health")
	if !ok || !integer(d["schema_version"], 1, 1) || d["application"] != project {
		return nil, domain.ErrInvalid
	}
	build, ok := mapping(fallback(d, "build", map[string]any{}), "dockerfile context args", "")
	if !ok {
		return nil, domain.ErrInvalid
	}
	dockerfile := fallback(build, "dockerfile", "Dockerfile")
	context := fallback(build, "context", ".")
	if !relative(dockerfile) || !relative(context) {
		return nil, domain.ErrInvalid
	}
	normalizedBuild := map[string]any{"dockerfile": dockerfile, "context": context}
	if raw, exists := build["args"]; exists {
		args, ok := raw.(map[string]any)
		if !ok || len(args) > 128 {
			return nil, domain.ErrInvalid
		}
		pattern := regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
		for k, v := range args {
			s, ok := v.(string)
			if !ok || !pattern.MatchString(k) || utf8.RuneCountInString(s) > 4096 || strings.TrimFunc(s, unicode.IsSpace) != s || strings.Trim(s, "\ufeff") != s || controls(s) {
				return nil, domain.ErrInvalid
			}
		}
		normalizedBuild["args"] = args
	}
	container, ok := mapping(d["container"], "port host_port bind_address", "port")
	if !ok || !integer(container["port"], 1, 65535) {
		return nil, domain.ErrInvalid
	}
	hostPort := fallback(container, "host_port", container["port"])
	bind := fallback(container, "bind_address", "127.0.0.1")
	address, ok := bind.(string)
	if !integer(hostPort, 1, 65535) || !ok || net.ParseIP(address) == nil || net.ParseIP(address).To4() == nil {
		return nil, domain.ErrInvalid
	}
	health, ok := mapping(d["health"], "readiness_path startup_timeout_seconds", "readiness_path")
	if !ok {
		return nil, domain.ErrInvalid
	}
	path, ok := health["readiness_path"].(string)
	timeout := fallback(health, "startup_timeout_seconds", 120)
	if !ok || !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") || strings.ContainsAny(path, "\r\n#?\\") || utf8.RuneCountInString(path) > 256 || !integer(timeout, 1, 600) {
		return nil, domain.ErrInvalid
	}
	resource, ok := mapping(fallback(d, "resources", map[string]any{}), "memory_limit cpus", "")
	if !ok {
		return nil, domain.ErrInvalid
	}
	memory, ok := fallback(resource, "memory_limit", "512m").(string)
	if !ok || !regexp.MustCompile(`^[1-9][0-9]*[kKmMgG]$`).MatchString(memory) {
		return nil, domain.ErrInvalid
	}
	rawCPU := fallback(resource, "cpus", float64(1))
	var cpu float64
	switch v := rawCPU.(type) {
	case int:
		cpu = float64(v)
	case float64:
		cpu = v
	default:
		return nil, domain.ErrInvalid
	}
	if math.IsNaN(cpu) || math.IsInf(cpu, 0) || cpu < 0.1 || cpu > 128 {
		return nil, domain.ErrInvalid
	}
	required, ok := fallback(d, "required_config", []any{}).([]any)
	if !ok || len(required) > 128 {
		return nil, domain.ErrInvalid
	}
	seen := map[string]bool{}
	keyPattern := regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)
	for _, v := range required {
		k, ok := v.(string)
		if !ok || !keyPattern.MatchString(k) || k == "APP_VERSION" || seen[k] {
			return nil, domain.ErrInvalid
		}
		seen[k] = true
	}
	return map[string]any{"schema_version": 1, "application": project, "build": normalizedBuild, "container": map[string]any{"port": container["port"], "host_port": hostPort, "bind_address": bind}, "health": map[string]any{"readiness_path": path, "startup_timeout_seconds": timeout}, "resources": map[string]any{"memory_limit": memory, "cpus": cpu}, "required_config": required}, nil
}
func validateManifest(value any, project string, content map[string][]byte) (map[string]any, error) {
	m, ok := mapping(value, "schema_version application version image commit minimum_deployctl_version deployment hooks", "schema_version application version image minimum_deployctl_version deployment")
	if !ok || !integer(m["schema_version"], 1, 2) || m["application"] != project || domain.ValidateName(project, 48) != nil {
		return nil, domain.ErrInvalid
	}
	version, vOK := m["version"].(string)
	image, iOK := m["image"].(string)
	if !vOK || !iOK || domain.ValidateVersion(version) != nil || domain.ValidateImage(image) != nil {
		return nil, domain.ErrInvalid
	}
	commit, ok := fallback(m, "commit", "").(string)
	if !ok || (commit != "" && !regexp.MustCompile(`^[a-f0-9]{40,64}$`).MatchString(commit)) {
		return nil, domain.ErrInvalid
	}
	normalized, err := normalizeDeployment(m["deployment"], project)
	if err != nil {
		return nil, err
	}
	m["deployment"] = normalized
	hooks := map[string]any{}
	minimum := "1.0.0"
	if m["schema_version"] == 1 {
		if _, exists := m["hooks"]; exists {
			return nil, domain.ErrInvalid
		}
	} else {
		hooks, ok = mapping(m["hooks"], "pre_install post_install", "")
		if !ok || len(hooks) == 0 {
			return nil, domain.ErrInvalid
		}
		minimum = "1.5.0"
		for phase, raw := range hooks {
			h, ok := mapping(raw, "path sha256 timeout_seconds refresh_config", "path sha256 timeout_seconds")
			if !ok || !integer(h["timeout_seconds"], 1, 3600) {
				return nil, domain.ErrInvalid
			}
			path := "hooks/pre-install.sh"
			if phase == "post_install" {
				path = "hooks/post-install.sh"
			}
			if h["path"] != path {
				return nil, domain.ErrInvalid
			}
			hash, ok := h["sha256"].(string)
			if !ok || !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(hash) {
				return nil, domain.ErrInvalid
			}
			if flag, exists := h["refresh_config"]; exists {
				if phase != "pre_install" {
					return nil, domain.ErrInvalid
				}
				if _, ok := flag.(bool); !ok {
					return nil, domain.ErrInvalid
				}
				minimum = "1.6.0"
			}
			body, exists := content[path]
			if !exists || len(body) > 256*1024 {
				return nil, domain.ErrInvalid
			}
			sum := sha256.Sum256(body)
			if hex.EncodeToString(sum[:]) != hash {
				return nil, domain.ErrInvalid
			}
		}
	}
	if m["minimum_deployctl_version"] != minimum || len(content) != 4+len(hooks) {
		return nil, domain.ErrInvalid
	}
	return m, nil
}
func renderCompose(m map[string]any) map[string]any {
	d := m["deployment"].(map[string]any)
	container := d["container"].(map[string]any)
	resource := d["resources"].(map[string]any)
	return map[string]any{"services": map[string]any{"app": map[string]any{
		"image": m["image"], "restart": "unless-stopped", "ports": []any{"${DEPLOY_BIND_ADDRESS}:${DEPLOY_PORT}:" + strconv.Itoa(container["port"].(int))},
		"env_file":    []any{map[string]any{"path": "${DEPLOY_CONFIG_FILE}", "format": "raw"}, map[string]any{"path": "${DEPLOY_SECRETS_FILE}", "format": "raw"}},
		"environment": map[string]any{"APP_VERSION": m["version"]}, "labels": map[string]any{"io.team-deploy.application": m["application"], "io.team-deploy.version": m["version"]},
		"mem_limit": resource["memory_limit"], "cpus": resource["cpus"], "pids_limit": 256, "stop_grace_period": "30s", "cap_drop": []any{"ALL"}, "security_opt": []any{"no-new-privileges:true"},
		"logging": map[string]any{"driver": "json-file", "options": map[string]any{"max-size": "10m", "max-file": "3"}},
	}}}
}
