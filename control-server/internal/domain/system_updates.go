package domain

import (
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

var updateSemver = regexp.MustCompile(`^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func updateVersion(value string) ([3]uint64, bool) {
	var out [3]uint64
	parts := updateSemver.FindStringSubmatch(value)
	if parts == nil {
		return out, false
	}
	for i := range out {
		n, err := strconv.ParseUint(parts[i+1], 10, 32)
		if err != nil {
			return out, false
		}
		out[i] = n
	}
	return out, true
}
func StableUpdateVersion(value string) bool { _, ok := updateVersion(value); return ok }
func CompatibleUpdateVersion(value string) bool {
	v, ok := updateVersion(value)
	return ok && v[0] == 1 && v[1] >= 15
}
func UpdateVersionNewer(target, current string) bool {
	a, ok := updateVersion(target)
	if !ok {
		return false
	}
	b, ok := updateVersion(current)
	if !ok {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return false
}

type SystemRelease struct {
	Version      string    `json:"version"`
	PublishedAt  time.Time `json:"published_at"`
	CLISHA256    string    `json:"cli_sha256"`
	ServerSHA256 string    `json:"server_sha256"`
}

func (r SystemRelease) Validate() error {
	if !CompatibleUpdateVersion(r.Version) || !updateDigest.MatchString(r.CLISHA256) || !updateDigest.MatchString(r.ServerSHA256) || r.PublishedAt.IsZero() {
		return ErrInvalid
	}
	return nil
}

var updateDigest = regexp.MustCompile(`^[a-f0-9]{64}$`)

type Updater struct {
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	TargetID string `json:"target_id"`
	UpdaterHeartbeat
	LastSeen *time.Time `json:"last_seen"`
	Online   bool       `json:"online"`
}
type UpdaterHeartbeat struct {
	CurrentVersion string `json:"current_version"`
	HelperVersion  string `json:"helper_version"`
	Protocol       int    `json:"protocol"`
	Platform       string `json:"platform"`
	Ready          bool   `json:"ready"`
	BlockedReason  string `json:"blocked_reason"`
}

func (h UpdaterHeartbeat) Validate() error {
	if h.Protocol != 1 || !CompatibleUpdateVersion(h.HelperVersion) || h.Platform != "linux" || (h.CurrentVersion != "" && !StableUpdateVersion(h.CurrentVersion)) || (h.Ready && (h.CurrentVersion == "" || h.BlockedReason != "")) || !slices.Contains([]string{"", "setup_required", "unsupported_platform", "unsupported_protocol", "invalid_version", "agent_unavailable", "pending_operation", "service_unavailable", "disabled", "pending_authorization", "backup_unavailable"}, h.BlockedReason) {
		return ErrInvalid
	}
	return nil
}

type UpdateEnrollment struct {
	Kind     string `json:"kind"`
	TargetID string `json:"target_id"`
}

func (e UpdateEnrollment) Validate() error {
	if (e.Kind == "center" && e.TargetID == "center") || (e.Kind == "node" && remoteID.MatchString(e.TargetID)) {
		return nil
	}
	return ErrInvalid
}

type UpdateRequest struct {
	RequestID       string   `json:"request_id"`
	UpdaterIDs      []string `json:"updater_ids"`
	Version         string   `json:"version"`
	Parallelism     int      `json:"parallelism"`
	BackupConfirmed bool     `json:"backup_confirmed"`
}

func (r UpdateRequest) Validate() error {
	if !remoteID.MatchString(r.RequestID) || len(r.UpdaterIDs) < 1 || len(r.UpdaterIDs) > 50 || r.Parallelism < 1 || r.Parallelism > 5 || !CompatibleUpdateVersion(r.Version) {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, id := range r.UpdaterIDs {
		if !remoteID.MatchString(id) || seen[id] {
			return ErrInvalid
		}
		seen[id] = true
	}
	return nil
}

type UpdateJob struct {
	ID             string     `json:"id"`
	RequestID      string     `json:"request_id"`
	BatchID        string     `json:"batch_id"`
	UpdaterID      string     `json:"updater_id"`
	Kind           string     `json:"kind"`
	TargetID       string     `json:"target_id"`
	Version        string     `json:"version"`
	FromVersion    string     `json:"from_version"`
	CLISHA256      string     `json:"cli_sha256"`
	ServerSHA256   string     `json:"server_sha256"`
	Status         string     `json:"status"`
	Stage          string     `json:"stage"`
	ResultCode     string     `json:"result_code"`
	CurrentVersion string     `json:"current_version"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
	Parallelism    int        `json:"parallelism"`
	ResolvedAt     *time.Time `json:"resolved_at"`
}
type UpdateReport struct {
	Status         string `json:"status"`
	Stage          string `json:"stage"`
	ResultCode     string `json:"result_code"`
	CurrentVersion string `json:"current_version"`
}

func UpdateTerminal(status string) bool {
	return slices.Contains([]string{"succeeded", "rolled_back", "failed", "needs_attention", "cancelled"}, status)
}
func UpdateBlocks(status string) bool {
	return slices.Contains([]string{"queued", "leased", "running", "needs_attention"}, status)
}
func (r UpdateReport) Validate() error {
	if !slices.Contains([]string{"running", "succeeded", "rolled_back", "failed", "needs_attention"}, r.Status) || !slices.Contains(strings.Split("queued waiting downloading backing_up stopping installing restarting verifying restoring completed failed interrupted", " "), r.Stage) || !slices.Contains([]string{"", "ok", "download_failed", "backup_failed", "install_failed", "health_failed", "rolled_back", "interrupted", "manual_recovery_required"}, r.ResultCode) || (r.CurrentVersion != "" && !StableUpdateVersion(r.CurrentVersion)) || (r.CurrentVersion == "" && (r.Status == "succeeded" || r.Status == "rolled_back")) {
		return ErrInvalid
	}
	if r.Status != "running" && r.ResultCode == "" {
		return ErrInvalid
	}
	return nil
}
