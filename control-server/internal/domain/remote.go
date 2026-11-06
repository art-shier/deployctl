package domain

import (
	"math"
	"regexp"
	"slices"
	"strings"
	"time"
)

var remoteID = regexp.MustCompile(`^[a-f0-9]{32}$`)

type NodeInput struct {
	Name             string   `json:"name"`
	Enabled          bool     `json:"enabled"`
	Groups           []string `json:"groups"`
	Projects         []string `json:"projects"`
	ExcludedProjects []string `json:"excluded_projects"`
	Environments     []string `json:"environments"`
}

type ResourceCapacity struct {
	TotalBytes     int64  `json:"total_bytes"`
	AvailableBytes int64  `json:"available_bytes"`
	Path           string `json:"path,omitempty"`
}
type NodeMetrics struct {
	CPUCores   int               `json:"cpu_cores"`
	CPUPercent *float64          `json:"cpu_percent"`
	Memory     *ResourceCapacity `json:"memory"`
	Disk       *ResourceCapacity `json:"disk"`
}

func (m NodeMetrics) Validate() error {
	if m.CPUCores < 0 || m.CPUCores > 1048576 {
		return ErrInvalid
	}
	if m.CPUPercent != nil && (math.IsNaN(*m.CPUPercent) || math.IsInf(*m.CPUPercent, 0) || *m.CPUPercent < 0 || *m.CPUPercent > 100) {
		return ErrInvalid
	}
	for _, c := range []*ResourceCapacity{m.Memory, m.Disk} {
		if c != nil && (c.TotalBytes <= 0 || c.AvailableBytes < 0 || c.AvailableBytes > c.TotalBytes || len(c.Path) > 4096) {
			return ErrInvalid
		}
	}
	return nil
}

type Node struct {
	Metrics   *NodeMetrics `json:"metrics"`
	MetricsAt *time.Time   `json:"metrics_at"`

	NodeInput
	PendingAuthorization bool       `json:"pending_authorization"`
	ID                   string     `json:"id"`
	CreatedAt            time.Time  `json:"created_at"`
	LastSeen             *time.Time `json:"last_seen"`
	AgentVersion         string     `json:"agent_version"`
	Platform             string     `json:"platform"`
	DockerAvailable      bool       `json:"docker_available"`
	Online               bool       `json:"online"`
}

func (n NodeInput) Validate() error {
	if strings.TrimSpace(n.Name) == "" || len(n.Name) > 512 {
		return ErrInvalid
	}
	for _, values := range [][]string{n.Groups, n.Projects, n.ExcludedProjects, n.Environments} {
		if len(values) > 500 {
			return ErrInvalid
		}
		for _, value := range values {
			if ValidateName(value, 48) != nil {
				return ErrInvalid
			}
		}
	}
	return nil
}
func (n Node) Allows(p Project, env string) bool {
	if p.DeploymentType != "" && p.DeploymentType != "docker" {
		return false
	}
	return n.Enabled && !n.PendingAuthorization && !slices.Contains(n.ExcludedProjects, p.Slug) && (slices.Contains(n.Projects, p.Slug) || slices.Contains(n.Groups, p.Group)) && slices.Contains(n.Environments, env)
}

type Binding struct {
	HostPort    int    `json:"host_port"`
	BindAddress string `json:"bind_address"`
}
type Observation struct {
	ID                 string   `json:"id"`
	Project            string   `json:"project"`
	Environment        string   `json:"environment"`
	ActualVersion      string   `json:"actual_version"`
	Health             string   `json:"health"`
	Binding            *Binding `json:"binding"`
	PendingTransaction bool     `json:"pending_transaction"`
}
type Instance struct {
	Observation
	NodeID     string     `json:"node_id"`
	ObservedAt *time.Time `json:"observed_at"`
}

func (o Observation) Validate() error {
	if !remoteID.MatchString(o.ID) || ValidateName(o.Project, 48) != nil || ValidateName(o.Environment, 32) != nil {
		return ErrInvalid
	}
	if o.ActualVersion != "" && ValidateVersion(o.ActualVersion) != nil {
		return ErrInvalid
	}
	if !slices.Contains([]string{"unknown", "healthy", "unhealthy", "stopped", "not_installed"}, o.Health) {
		return ErrInvalid
	}
	if o.Binding != nil {
		if o.Binding.HostPort < 1 || o.Binding.BindAddress == "" {
			return ErrInvalid
		}
		if ValidateDeploymentDefaults(DeploymentDefaults{HostPort: o.Binding.HostPort, BindAddress: o.Binding.BindAddress}) != nil {
			return ErrInvalid
		}
	}
	return nil
}

type TaskRequest struct {
	NodeID      string  `json:"node_id"`
	Environment string  `json:"environment"`
	Action      string  `json:"action"`
	Version     string  `json:"version,omitempty"`
	RequestID   string  `json:"request_id"`
	HostPort    *int    `json:"host_port,omitempty"`
	BindAddress *string `json:"bind_address,omitempty"`
}

func (r TaskRequest) Validate() error {
	if !remoteID.MatchString(r.NodeID) || !remoteID.MatchString(r.RequestID) || ValidateName(r.Environment, 32) != nil || !slices.Contains([]string{"install", "upgrade", "rollback"}, r.Action) {
		return ErrInvalid
	}
	if r.Action == "rollback" && (r.Version != "" || r.HostPort != nil || r.BindAddress != nil) {
		return ErrInvalid
	}
	if r.Version != "" && r.Version != "stable" && ValidateVersion(r.Version) != nil {
		return ErrInvalid
	}
	d := DeploymentDefaults{}
	if r.HostPort != nil {
		if *r.HostPort < 1 {
			return ErrInvalid
		}
		d.HostPort = *r.HostPort
	}
	if r.BindAddress != nil {
		if *r.BindAddress == "" {
			return ErrInvalid
		}
		d.BindAddress = *r.BindAddress
	}
	return ValidateDeploymentDefaults(d)
}

type TaskResult struct {
	Code        string       `json:"code"`
	Stage       string       `json:"stage"`
	Observation *Observation `json:"observation,omitempty"`
}
type Task struct {
	ID          string      `json:"id"`
	InstanceID  string      `json:"instance_id"`
	NodeID      string      `json:"node_id"`
	Project     string      `json:"project"`
	Environment string      `json:"environment"`
	Action      string      `json:"action"`
	Actor       string      `json:"actor"`
	RequestID   string      `json:"request_id"`
	Status      string      `json:"status"`
	Stage       string      `json:"stage"`
	Version     string      `json:"version"`
	CreatedAt   time.Time   `json:"created_at"`
	UpdatedAt   time.Time   `json:"updated_at"`
	Result      *TaskResult `json:"result"`
}
type AgentTask struct {
	Task
	Resolution  map[string]any `json:"resolution,omitempty"`
	HostPort    *int           `json:"host_port"`
	BindAddress *string        `json:"bind_address"`
}
type TaskReport struct {
	Status      string       `json:"status"`
	Stage       string       `json:"stage"`
	Observation *Observation `json:"observation,omitempty"`
}

func ValidTaskStage(stage string) bool {
	return slices.Contains([]string{"accepted", "preparing", "downloading", "pulling", "deploying", "verifying", "rollback", "completed", "execution_failed", "interrupted"}, stage)
}
func TaskTerminal(status string) bool {
	return slices.Contains([]string{"succeeded", "failed", "needs_attention", "cancelled", "expired"}, status)
}
func (r TaskReport) Validate() error {
	if !slices.Contains([]string{"running", "succeeded", "failed", "needs_attention"}, r.Status) || !ValidTaskStage(r.Stage) {
		return ErrInvalid
	}
	if r.Status == "succeeded" && r.Stage != "completed" {
		return ErrInvalid
	}
	if r.Status == "failed" && r.Stage != "execution_failed" {
		return ErrInvalid
	}
	if r.Status == "needs_attention" && r.Stage != "interrupted" && r.Stage != "execution_failed" {
		return ErrInvalid
	}
	if r.Status == "running" && slices.Contains([]string{"completed", "execution_failed", "interrupted"}, r.Stage) {
		return ErrInvalid
	}
	if r.Observation != nil {
		return r.Observation.Validate()
	}
	return nil
}
