package artifacts

import (
	"math"
	"net"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

var (
	multiConfigPattern      = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
	multiEnvironmentPattern = regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)
	multiMemoryPattern      = regexp.MustCompile(`^[1-9][0-9]{0,8}[kKmMgG]$`)
	multiCommitPattern      = regexp.MustCompile(`^[a-f0-9]{40,64}$`)
)

func multiText(v any, max int) (string, bool) {
	s, ok := v.(string)
	if !ok || utf8.RuneCountInString(s) > max {
		return "", false
	}
	for _, r := range s {
		if r < 32 || r >= 127 && r <= 159 {
			return "", false
		}
	}
	return s, true
}
func multiNames(v any, max int) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	if !ok || len(m) > max {
		return nil, false
	}
	for k := range m {
		if domain.ValidateName(k, 48) != nil {
			return nil, false
		}
	}
	return m, true
}
func multiKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func multiArgv(v any) bool {
	a, ok := v.([]any)
	if !ok || len(a) < 1 || len(a) > 128 {
		return false
	}
	for i, v := range a {
		s, ok := multiText(v, 4096)
		if !ok || i == 0 && s == "" {
			return false
		}
	}
	return true
}
func multiConfigKey(v any) bool {
	s, ok := v.(string)
	return ok && multiConfigPattern.MatchString(s)
}
func multiMount(v any) (string, bool) {
	s, ok := multiText(v, 256)
	if !ok || !strings.HasPrefix(s, "/") || path.Clean(s) != s || s == "/" || strings.ContainsAny(s, `\$:`) {
		return "", false
	}
	for _, blocked := range []string{"/proc", "/sys", "/dev", "/run/deployctl"} {
		if s == blocked || strings.HasPrefix(s, blocked+"/") {
			return "", false
		}
	}
	return s, true
}
func multiResources(v any) (map[string]any, error) {
	m, ok := mapping(v, "memory_limit cpus", "")
	if !ok {
		return nil, domain.ErrInvalid
	}
	memory, ok := fallback(m, "memory_limit", "512m").(string)
	if !ok || !multiMemoryPattern.MatchString(memory) {
		return nil, domain.ErrInvalid
	}
	var cpu float64
	switch v := fallback(m, "cpus", float64(1)).(type) {
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
	return map[string]any{"memory_limit": memory, "cpus": cpu}, nil
}
func multiHealth(v any) (map[string]any, error) {
	m, ok := mapping(v, "http exec startup_timeout_seconds", "")
	if !ok {
		return nil, domain.ErrInvalid
	}
	_, http := m["http"]
	_, exec := m["exec"]
	if http == exec {
		return nil, domain.ErrInvalid
	}
	timeout := fallback(m, "startup_timeout_seconds", 120)
	if !integer(timeout, 1, 600) {
		return nil, domain.ErrInvalid
	}
	out := map[string]any{"startup_timeout_seconds": timeout}
	if http {
		s, ok := multiText(m["http"], 256)
		if !ok || !strings.HasPrefix(s, "/") || strings.HasPrefix(s, "//") || strings.ContainsAny(s, `#?\$`) {
			return nil, domain.ErrInvalid
		}
		out["http"] = s
	} else {
		if !multiArgv(m["exec"]) {
			return nil, domain.ErrInvalid
		}
		out["exec"] = m["exec"]
	}
	return out, nil
}
func multiNodeContract(v any, volumes map[string]any, job bool) (map[string]any, error) {
	allowed := "image command environment secret_files volumes resources depends_on permission_profile "
	required := "image "
	if job {
		allowed += "trigger timeout_seconds rollback_compatible"
		required += "trigger command"
	} else {
		allowed += "lifecycle endpoints health"
		required += "lifecycle health"
	}
	m, ok := mapping(v, allowed, required)
	if !ok {
		return nil, domain.ErrInvalid
	}
	image, ok := m["image"].(string)
	if !ok || domain.ValidateImage(image) != nil {
		return nil, domain.ErrInvalid
	}
	out := map[string]any{"image": image}
	if cmd, exists := m["command"]; exists {
		if !multiArgv(cmd) {
			return nil, domain.ErrInvalid
		}
		out["command"] = cmd
	}
	profile := fallback(m, "permission_profile", "restricted")
	if profile != "restricted" && profile != "database-init" {
		return nil, domain.ErrInvalid
	}
	out["permission_profile"] = profile
	if job {
		if m["trigger"] != "bootstrap" && m["trigger"] != "explicit" {
			return nil, domain.ErrInvalid
		}
		timeout := fallback(m, "timeout_seconds", 900)
		if !integer(timeout, 1, 7200) {
			return nil, domain.ErrInvalid
		}
		compatible, ok := fallback(m, "rollback_compatible", false).(bool)
		if !ok {
			return nil, domain.ErrInvalid
		}
		out["trigger"] = m["trigger"]
		out["timeout_seconds"] = timeout
		out["rollback_compatible"] = compatible
	} else {
		lifecycle := m["lifecycle"]
		if lifecycle != "application" && lifecycle != "persistent" || profile == "database-init" && lifecycle != "persistent" {
			return nil, domain.ErrInvalid
		}
		health, err := multiHealth(m["health"])
		if err != nil {
			return nil, err
		}
		endpoints, ok := multiNames(fallback(m, "endpoints", map[string]any{}), 16)
		if !ok {
			return nil, domain.ErrInvalid
		}
		normalized := map[string]any{}
		for name, v := range endpoints {
			e, ok := mapping(v, "container_port host_port bind_address", "container_port host_port")
			if !ok || !integer(e["container_port"], 1, 65535) || !integer(e["host_port"], 1, 65535) {
				return nil, domain.ErrInvalid
			}
			bind, ok := fallback(e, "bind_address", "127.0.0.1").(string)
			if !ok || strings.Contains(bind, ":") || net.ParseIP(bind) == nil || net.ParseIP(bind).To4() == nil {
				return nil, domain.ErrInvalid
			}
			normalized[name] = map[string]any{"container_port": e["container_port"], "host_port": e["host_port"], "bind_address": bind}
		}
		if _, http := health["http"]; http && len(normalized) != 1 {
			return nil, domain.ErrInvalid
		}
		out["lifecycle"] = lifecycle
		out["health"] = health
		out["endpoints"] = normalized
	}
	env, ok := fallback(m, "environment", map[string]any{}).(map[string]any)
	if !ok || len(env) > 128 {
		return nil, domain.ErrInvalid
	}
	normalizedEnv := map[string]any{}
	for key, v := range env {
		if !multiEnvironmentPattern.MatchString(key) || strings.HasPrefix(key, "DEPLOYCTL_") {
			return nil, domain.ErrInvalid
		}
		b, ok := mapping(v, "literal config secret", "")
		if !ok || len(b) != 1 {
			return nil, domain.ErrInvalid
		}
		for kind, item := range b {
			if kind == "literal" {
				if _, ok := multiText(item, 4096); !ok {
					return nil, domain.ErrInvalid
				}
			} else if !multiConfigKey(item) {
				return nil, domain.ErrInvalid
			}
		}
		normalizedEnv[key] = b
	}
	files, ok := fallback(m, "secret_files", map[string]any{}).(map[string]any)
	if !ok || len(files) > 128 {
		return nil, domain.ErrInvalid
	}
	mounts, ok := multiNames(fallback(m, "volumes", map[string]any{}), 32)
	if !ok {
		return nil, domain.ErrInvalid
	}
	paths := []string{}
	for key, v := range files {
		s, ok := multiMount(v)
		if !ok || !multiConfigKey(key) {
			return nil, domain.ErrInvalid
		}
		paths = append(paths, s)
	}
	for key, v := range mounts {
		s, ok := multiMount(v)
		if _, exists := volumes[key]; !exists || !ok {
			return nil, domain.ErrInvalid
		}
		paths = append(paths, s)
	}
	for i, p := range paths {
		for _, q := range paths[i+1:] {
			if p == q || strings.HasPrefix(p, q+"/") || strings.HasPrefix(q, p+"/") {
				return nil, domain.ErrInvalid
			}
		}
	}
	resources, err := multiResources(fallback(m, "resources", map[string]any{}))
	if err != nil {
		return nil, err
	}
	deps, ok := multiNames(fallback(m, "depends_on", map[string]any{}), 64)
	if !ok {
		return nil, domain.ErrInvalid
	}
	out["environment"] = normalizedEnv
	out["secret_files"] = files
	out["volumes"] = mounts
	out["resources"] = resources
	out["depends_on"] = deps
	return out, nil
}
func normalizeMultiDeployment(v any, project string) (map[string]any, error) {
	m, ok := mapping(v, "schema_version application platform primary_endpoint services volumes jobs", "schema_version application platform services")
	if !ok || !integer(m["schema_version"], 2, 2) || m["application"] != project || m["platform"] != "linux/amd64" && m["platform"] != "linux/arm64" {
		return nil, domain.ErrInvalid
	}
	volumes, ok := multiNames(fallback(m, "volumes", map[string]any{}), 32)
	if !ok {
		return nil, domain.ErrInvalid
	}
	for _, v := range volumes {
		d, ok := mapping(v, "retain", "retain")
		if !ok || d["retain"] != true {
			return nil, domain.ErrInvalid
		}
	}
	services, ok := multiNames(m["services"], 32)
	if !ok || len(services) == 0 {
		return nil, domain.ErrInvalid
	}
	jobs, ok := multiNames(fallback(m, "jobs", map[string]any{}), 32)
	if !ok {
		return nil, domain.ErrInvalid
	}
	all := map[string]any{}
	normalizedServices := map[string]any{}
	normalizedJobs := map[string]any{}
	for name, v := range services {
		n, err := multiNodeContract(v, volumes, false)
		if err != nil {
			return nil, err
		}
		all[name] = n
		normalizedServices[name] = n
	}
	for name, v := range jobs {
		if _, exists := all[name]; exists {
			return nil, domain.ErrInvalid
		}
		n, err := multiNodeContract(v, volumes, true)
		if err != nil {
			return nil, err
		}
		all[name] = n
		normalizedJobs[name] = n
	}
	visiting, visited := map[string]bool{}, map[string]bool{}
	var visit func(string) bool
	visit = func(name string) bool {
		if visiting[name] {
			return false
		}
		if visited[name] {
			return true
		}
		visiting[name] = true
		for dep, condition := range all[name].(map[string]any)["depends_on"].(map[string]any) {
			if _, exists := all[dep]; !exists {
				return false
			}
			expected := "succeeded"
			if _, exists := services[dep]; exists {
				expected = "healthy"
			}
			if condition != expected || !visit(dep) {
				return false
			}
		}
		visiting[name] = false
		visited[name] = true
		return true
	}
	for name := range all {
		if !visit(name) {
			return nil, domain.ErrInvalid
		}
	}
	endpoints := map[string]any{}
	used := []map[string]any{}
	for name, v := range normalizedServices {
		for key, e := range v.(map[string]any)["endpoints"].(map[string]any) {
			endpoint := e.(map[string]any)
			for _, prior := range used {
				if endpoint["host_port"] == prior["host_port"] && (endpoint["bind_address"] == prior["bind_address"] || endpoint["bind_address"] == "0.0.0.0" || prior["bind_address"] == "0.0.0.0") {
					return nil, domain.ErrInvalid
				}
			}
			used = append(used, endpoint)
			endpoints[name+"."+key] = e
		}
	}
	out := map[string]any{"schema_version": 2, "application": project, "platform": m["platform"], "services": normalizedServices, "jobs": normalizedJobs, "volumes": volumes}
	if raw, exists := m["primary_endpoint"]; exists {
		p, ok := raw.(string)
		if !ok {
			return nil, domain.ErrInvalid
		}
		if _, exists := endpoints[p]; !exists {
			return nil, domain.ErrInvalid
		}
		out["primary_endpoint"] = p
	}
	return out, nil
}
func validateMultiManifest(v any, project string, content map[string][]byte) (map[string]any, error) {
	m, ok := mapping(v, "schema_version application version commit minimum_deployctl_version capabilities deployment", "schema_version application version minimum_deployctl_version capabilities deployment")
	if !ok || !integer(m["schema_version"], 3, 3) || m["application"] != project || domain.ValidateName(project, 48) != nil || len(content) != 4 {
		return nil, domain.ErrInvalid
	}
	version, ok := m["version"].(string)
	if !ok || domain.ValidateVersion(version) != nil {
		return nil, domain.ErrInvalid
	}
	commit, ok := fallback(m, "commit", "").(string)
	if !ok || commit != "" && !multiCommitPattern.MatchString(commit) {
		return nil, domain.ErrInvalid
	}
	capabilities, ok := m["capabilities"].([]any)
	if !ok || len(capabilities) != 1 || capabilities[0] != "multi_service_v1" || m["minimum_deployctl_version"] != "1.20.0" {
		return nil, domain.ErrInvalid
	}
	d, err := normalizeMultiDeployment(m["deployment"], project)
	if err != nil {
		return nil, err
	}
	out := map[string]any{}
	for k, v := range m {
		out[k] = v
	}
	out["deployment"] = d
	out["commit"] = commit
	return out, nil
}
func multiVariable(kind, name string) string {
	return "${DEPLOYCTL_" + kind + "_" + strings.ToUpper(strings.ReplaceAll(name, "-", "_")) + "}"
}
func multiCommand(v any) []any {
	out := []any{}
	for _, arg := range v.([]any) {
		out = append(out, strings.ReplaceAll(arg.(string), "$", "$$"))
	}
	return out
}
func renderMultiCompose(m map[string]any) map[string]any {
	d := m["deployment"].(map[string]any)
	services := map[string]any{}
	volumes := map[string]any{}
	for name := range d["volumes"].(map[string]any) {
		volumes[name] = map[string]any{"external": true, "name": multiVariable("VOLUME", name)}
	}
	for _, kind := range []string{"services", "jobs"} {
		for name, v := range d[kind].(map[string]any) {
			n := v.(map[string]any)
			job := kind == "jobs"
			restart := "unless-stopped"
			lifecycle := n["lifecycle"]
			if job {
				restart = "no"
				lifecycle = "job"
			}
			resources := n["resources"].(map[string]any)
			s := map[string]any{"image": n["image"], "platform": d["platform"], "restart": restart,
				"networks":  map[string]any{"app": map[string]any{"aliases": []any{name}}},
				"labels":    map[string]any{"io.team-deploy.application": m["application"], "io.team-deploy.service": name, "io.team-deploy.lifecycle": lifecycle},
				"env_file":  []any{map[string]any{"path": multiVariable("ENV", name), "format": "raw"}},
				"mem_limit": resources["memory_limit"], "cpus": resources["cpus"], "pids_limit": 256, "stop_grace_period": "30s",
				"cap_drop": []any{"ALL"}, "security_opt": []any{"no-new-privileges:true"},
				"logging": map[string]any{"driver": "json-file", "options": map[string]any{"max-size": "10m", "max-file": "3"}},
			}
			if n["permission_profile"] == "database-init" {
				s["cap_add"] = []any{"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
			}
			if cmd, exists := n["command"]; exists {
				s["command"] = multiCommand(cmd)
			}
			mounts := []any{}
			vm := n["volumes"].(map[string]any)
			for _, key := range multiKeys(vm) {
				mounts = append(mounts, map[string]any{"type": "volume", "source": key, "target": vm[key]})
			}
			files := n["secret_files"].(map[string]any)
			for _, key := range multiKeys(files) {
				mounts = append(mounts, map[string]any{"type": "bind", "source": multiVariable("SECRET", name) + "/" + key, "target": files[key], "read_only": true, "bind": map[string]any{"create_host_path": false}})
			}
			if len(mounts) > 0 {
				s["volumes"] = mounts
			}
			if job {
				s["profiles"] = []any{"jobs"}
			} else {
				endpoints := n["endpoints"].(map[string]any)
				ports := []any{}
				for _, key := range multiKeys(endpoints) {
					e := endpoints[key].(map[string]any)
					ports = append(ports, e["bind_address"].(string)+":"+strconv.Itoa(e["host_port"].(int))+":"+strconv.Itoa(e["container_port"].(int)))
				}
				if len(ports) > 0 {
					s["ports"] = ports
				}
				health := n["health"].(map[string]any)
				if cmd, exists := health["exec"]; exists {
					s["healthcheck"] = map[string]any{"test": append([]any{"CMD"}, multiCommand(cmd)...), "interval": "5s", "timeout": "5s", "retries": 12}
				}
			}
			services[name] = s
		}
	}
	return map[string]any{"services": services, "volumes": volumes, "networks": map[string]any{"app": map[string]any{"external": true, "name": "${DEPLOYCTL_NETWORK}"}}}
}
