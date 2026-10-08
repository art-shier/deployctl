package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"slices"
)

type Principal struct {
	ID           string
	Role         string
	Project      string
	Environments []string
}

func (p Principal) Can(action, project, environment string) bool {
	if p.Role == "owner" {
		return true
	}
	if project == "" || p.Project != project {
		return false
	}
	switch p.Role {
	case "publisher":
		return slices.Contains([]string{"project.read", "release.read", "release.publish", "artifact.read", "registry.pull", "registry.push"}, action)
	case "deployer":
		if len(p.Environments) == 0 {
			return false
		}
		if slices.Contains([]string{"resolve", "configuration.read", "receipt.write"}, action) {
			return slices.Contains(p.Environments, environment)
		}
		return slices.Contains([]string{"project.read", "release.read", "artifact.read", "registry.pull"}, action)
	}
	return false
}
func NewToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return "ctl_" + base64.RawURLEncoding.EncodeToString(b), nil
}
func HashToken(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}
