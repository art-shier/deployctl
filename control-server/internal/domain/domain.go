package domain

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"net"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

var (
	ErrInvalid      = errors.New("invalid input")
	ErrConflict     = errors.New("revision or version conflict")
	ErrNotFound     = errors.New("not found")
	ErrUnauthorized = errors.New("unauthorized")
	names           = regexp.MustCompile(`^[a-z][a-z0-9]*(-[a-z0-9]+)*$`)
	keys            = regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)
	versions        = regexp.MustCompile(`^v?[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9][A-Za-z0-9.-]*)?$`)
	images          = regexp.MustCompile(`^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$`)
	memories        = regexp.MustCompile(`^[1-9][0-9]*[kmg]$`)
)

type Variable struct {
	Value  string `json:"value"`
	Secret bool   `json:"secret"`
}
type Change struct {
	Key       string  `json:"key"`
	Operation string  `json:"operation"`
	Secret    *bool   `json:"secret,omitempty"`
	Value     *string `json:"value,omitempty"`
}
type DeploymentDefaults struct {
	HostPort    int     `json:"host_port,omitempty"`
	BindAddress string  `json:"bind_address,omitempty"`
	MemoryLimit string  `json:"memory_limit,omitempty"`
	CPUs        float64 `json:"cpus,omitempty"`
}
type Configuration struct {
	RuntimeEnv         map[string]Variable `json:"runtime_env"`
	InstallParams      map[string]Variable `json:"install_params"`
	DeploymentDefaults DeploymentDefaults  `json:"deployment_defaults"`
}
type Project struct {
	Slug               string    `json:"slug"`
	Name               string    `json:"name"`
	Description        string    `json:"description"`
	Repository         string    `json:"repository"`
	ImageRepository    string    `json:"image_repository"`
	DefaultEnvironment string    `json:"default_environment"`
	CreatedAt          time.Time `json:"created_at"`
}
type Revision struct {
	ID            string        `json:"id"`
	Project       string        `json:"project"`
	Environment   string        `json:"environment"`
	Revision      int64         `json:"revision"`
	TargetVersion string        `json:"target_version"`
	Configuration Configuration `json:"configuration"`
	CreatedAt     time.Time     `json:"created_at"`
}
type Release struct {
	ID        string    `json:"id"`
	Project   string    `json:"project"`
	Version   string    `json:"version"`
	Commit    string    `json:"commit"`
	Image     string    `json:"image"`
	SHA256    string    `json:"sha256"`
	Size      int64     `json:"size"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
}
type Receipt struct {
	ID          string    `json:"id"`
	HostID      string    `json:"host_id"`
	Project     string    `json:"project"`
	Environment string    `json:"environment"`
	ReleaseID   string    `json:"release_id"`
	RevisionID  string    `json:"configuration_revision"`
	Success     bool      `json:"success"`
	CLIVersion  string    `json:"cli_version"`
	CreatedAt   time.Time `json:"created_at"`
}
type AuditEvent struct {
	ID          string    `json:"id"`
	Actor       string    `json:"actor"`
	Action      string    `json:"action"`
	Project     string    `json:"project"`
	Environment string    `json:"environment"`
	Keys        []string  `json:"keys,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
}
type Token struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Role         string    `json:"role"`
	Project      string    `json:"project"`
	Environments []string  `json:"environments"`
	ExpiresAt    time.Time `json:"expires_at"`
	Revoked      bool      `json:"revoked"`
	CreatedAt    time.Time `json:"created_at"`
}

func NewID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func ValidateName(s string, max int) error {
	if len(s) > max || !names.MatchString(s) {
		return ErrInvalid
	}
	return nil
}
func ValidateVersion(s string) error {
	if len(s) > 96 || !versions.MatchString(s) {
		return ErrInvalid
	}
	return nil
}
func ValidateImage(s string) error {
	if !images.MatchString(s) {
		return ErrInvalid
	}
	return nil
}
func ValidateVariables(vars map[string]Variable, runtime bool) error {
	if len(vars) > 128 {
		return ErrInvalid
	}
	size := 0
	for k, v := range vars {
		if len(k) > 128 || !keys.MatchString(k) || !utf8.ValidString(v.Value) || utf8.RuneCountInString(v.Value) > 4096 {
			return ErrInvalid
		}
		if runtime && (k == "APP_VERSION" || strings.HasPrefix(k, "DEPLOYCTL_") || k == "BASH_ENV" || k == "ENV" || k == "SHELLOPTS" || k == "BASHOPTS") {
			return ErrInvalid
		}
		for _, r := range v.Value {
			if r < 32 || r >= 127 && r <= 159 || r == 0x2028 || r == 0x2029 {
				return ErrInvalid
			}
		}
		size += len(k) + len(v.Value) + 2
	}
	if size > 64*1024 {
		return ErrInvalid
	}
	return nil
}
func ApplyChanges(old map[string]Variable, changes []Change, runtime bool) (map[string]Variable, error) {
	out := map[string]Variable{}
	for k, v := range old {
		out[k] = v
	}
	seen := map[string]bool{}
	if len(changes) > 128 {
		return nil, ErrInvalid
	}
	for _, c := range changes {
		if seen[c.Key] || ValidateVariables(map[string]Variable{c.Key: {}}, runtime) != nil {
			return nil, ErrInvalid
		}
		seen[c.Key] = true
		v, exists := out[c.Key]
		switch c.Operation {
		case "keep":
			if !exists || c.Value != nil {
				return nil, ErrInvalid
			}
			if c.Secret != nil {
				v.Secret = *c.Secret
			}
			out[c.Key] = v
		case "set":
			if c.Value == nil {
				return nil, ErrInvalid
			}
			v.Value = *c.Value
			if c.Secret != nil {
				v.Secret = *c.Secret
			}
			out[c.Key] = v
		case "remove":
			if c.Value != nil || c.Secret != nil {
				return nil, ErrInvalid
			}
			delete(out, c.Key)
		default:
			return nil, ErrInvalid
		}
	}
	if err := ValidateVariables(out, runtime); err != nil {
		return nil, err
	}
	return out, nil
}
func ValidateDeploymentDefaults(d DeploymentDefaults) error {
	if d.HostPort < 0 || d.HostPort > 65535 {
		return ErrInvalid
	}
	if d.BindAddress != "" && (net.ParseIP(d.BindAddress) == nil || net.ParseIP(d.BindAddress).To4() == nil) {
		return ErrInvalid
	}
	if d.MemoryLimit != "" && !memories.MatchString(d.MemoryLimit) {
		return ErrInvalid
	}
	if d.CPUs != 0 && (d.CPUs < 0.1 || d.CPUs > 64) {
		return ErrInvalid
	}
	return nil
}
func ValidateConfiguration(c Configuration) error {
	if err := ValidateVariables(c.RuntimeEnv, true); err != nil {
		return err
	}
	if err := ValidateVariables(c.InstallParams, false); err != nil {
		return err
	}
	return ValidateDeploymentDefaults(c.DeploymentDefaults)
}
func Values(vars map[string]Variable) map[string]string {
	out := map[string]string{}
	for k, v := range vars {
		out[k] = v.Value
	}
	return out
}
