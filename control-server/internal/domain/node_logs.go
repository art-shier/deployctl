package domain

import (
	"slices"
	"time"
	"unicode/utf8"
)

type LogQuery struct {
	ID          string `json:"id,omitempty"`
	NodeID      string `json:"node_id,omitempty"`
	InstanceID  string `json:"instance_id,omitempty"`
	Project     string `json:"project,omitempty"`
	Environment string `json:"environment,omitempty"`
	Source      string `json:"source"`
	TaskID      string `json:"task_id,omitempty"`
	ArchiveID   string `json:"archive_id,omitempty"`
	Tail        int    `json:"tail"`
	Since       string `json:"since,omitempty"`
	Until       string `json:"until,omitempty"`
	Search      string `json:"search,omitempty"`
}

func (q LogQuery) Validate() error {
	if !slices.Contains([]string{"runtime", "deployment", "archives", "archive", "failure"}, q.Source) || q.Tail < 1 || q.Tail > 2000 || len(q.Search) > 256 || !utf8.ValidString(q.Search) {
		return ErrInvalid
	}
	if (q.Source == "deployment" && !remoteID.MatchString(q.TaskID)) || (q.Source == "archive" && !remoteID.MatchString(q.ArchiveID)) {
		return ErrInvalid
	}
	if (q.Source != "deployment" && q.TaskID != "") || (q.Source != "archive" && q.ArchiveID != "") {
		return ErrInvalid
	}
	var since, until time.Time
	var err error
	if q.Since != "" {
		since, err = time.Parse(time.RFC3339Nano, q.Since)
		if err != nil {
			return ErrInvalid
		}
	}
	if q.Until != "" {
		until, err = time.Parse(time.RFC3339Nano, q.Until)
		if err != nil {
			return ErrInvalid
		}
	}
	if !since.IsZero() && !until.IsZero() && since.After(until) {
		return ErrInvalid
	}
	return nil
}

type LogSource struct {
	ID          string `json:"id"`
	Version     string `json:"version"`
	ContainerID string `json:"container_id"`
	CapturedAt  string `json:"captured_at"`
	Truncated   bool   `json:"truncated"`
}
type LogResult struct {
	Text      string      `json:"text"`
	Truncated bool        `json:"truncated"`
	Sources   []LogSource `json:"sources"`
	Error     string      `json:"error"`
}

func (r LogResult) Validate() error {
	if len(r.Text) > 256*1024 || !utf8.ValidString(r.Text) || len(r.Sources) > 30 || !slices.Contains([]string{"", "log_unavailable", "container_unavailable", "log_read_failed"}, r.Error) {
		return ErrInvalid
	}
	for _, s := range r.Sources {
		if !remoteID.MatchString(s.ID) || len(s.Version) > 128 || len(s.ContainerID) > 64 || len(s.CapturedAt) > 40 {
			return ErrInvalid
		}
	}
	return nil
}
