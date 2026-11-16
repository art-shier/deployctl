package domain

import "slices"

type ServiceObservation struct {
	Version   string `json:"version"`
	Image     string `json:"image"`
	Lifecycle string `json:"lifecycle"`
	Health    string `json:"health"`
}
type StackObservation struct {
	Services         map[string]ServiceObservation `json:"services"`
	Jobs             map[string]string             `json:"jobs"`
	OperationID      string                        `json:"operation_id"`
	Phase            string                        `json:"phase"`
	RecoveryRequired bool                          `json:"recovery_required"`
}

func (s StackObservation) Validate() error {
	if len(s.Services) > 128 || len(s.Jobs) > 128 || (s.OperationID != "" && !remoteID.MatchString(s.OperationID)) {
		return ErrInvalid
	}
	phases := []string{"", "prepared", "pulling", "resources", "job-running", "job-succeeded", "applying", "checking", "checked", "quiescing", "quiesced", "succeeded", "rolling-back", "rolled-back", "recovery-required", "stop", "restart", "changing-layout", "retiring"}
	if !slices.Contains(phases, s.Phase) {
		return ErrInvalid
	}
	for name, item := range s.Services {
		if ValidateName(name, 48) != nil || ValidateVersion(item.Version) != nil || ValidateImage(item.Image) != nil || !slices.Contains([]string{"application", "persistent"}, item.Lifecycle) || !slices.Contains([]string{"healthy", "unhealthy", "stopped", "unknown"}, item.Health) {
			return ErrInvalid
		}
	}
	for name, status := range s.Jobs {
		if ValidateName(name, 48) != nil || !slices.Contains([]string{"starting", "running", "succeeded", "failed", "unknown"}, status) {
			return ErrInvalid
		}
	}
	return nil
}
