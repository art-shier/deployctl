package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"slices"
)

type Principal struct {
	ID               string
	Role             string
	Project          string
	Environments     []string
	Projects         []string
	Groups           []string
	ExcludedProjects []string
	ExplicitProjects []string
}

func (p Principal) Can(action, project, environment string) bool {
	if p.Role == "owner" {
		return true
	}
	if slices.Contains(p.ExcludedProjects, project) {
		return false
	}
	if project == "" || (p.Project != project && !slices.Contains(p.Projects, project)) {
		return false
	}
	switch p.Role {
	case "publisher":
		if slices.Contains([]string{"configuration.read", "configuration.write", "configuration.reveal"}, action) {
			return len(p.Environments) == 0 || slices.Contains(p.Environments, environment)
		}
		return slices.Contains([]string{"project.read", "project.update", "release.read", "release.publish", "artifact.read", "registry.pull", "registry.push"}, action)
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

func (p Principal) CanCreateProject(project, group, environment string) bool {
	return p.Role == "owner" || (p.Role == "publisher" && !slices.Contains(p.ExcludedProjects, project) && slices.Contains(p.Groups, group) && (len(p.Environments) == 0 || slices.Contains(p.Environments, environment)))
}

// CanInGroup authorizes the membership read by the resolution transaction,
// rather than projects expanded earlier during token authentication.
func (p Principal) CanInGroup(action, project, group, environment string) bool {
	resolved := p
	resolved.Projects = append([]string{}, p.ExplicitProjects...)
	if len(p.Groups) == 0 && p.ExplicitProjects == nil {
		resolved.Projects = append([]string{}, p.Projects...)
	}
	if slices.Contains(p.Groups, group) {
		resolved.Projects = append(resolved.Projects, project)
	}
	return resolved.Can(action, project, environment)
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
