package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

var errAgentUnavailable = errors.New("agent resources unavailable")

const maxAgentFile = 16 * 1024 * 1024

var agentReferenceName = regexp.MustCompile(`^references/[a-zA-Z0-9][a-zA-Z0-9_-]*\.md$`)
var agentVersion = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`)
var agentDigest = regexp.MustCompile(`^[a-f0-9]{64}$`)

// Only packaged public resources are addressable, even if AgentDir is misconfigured.
func agentPublicName(name string) bool {
	if agentReferenceName.MatchString(name) {
		return true
	}
	switch name {
	case "SKILL.md", "team-deploy-skill.zip", "team-deploy-skill.zip.sha256",
		"assets/deployctl.pyz", "assets/deployctl.pyz.sha256", "assets/install.sh",
		"assets/LICENSE", "assets/THIRD_PARTY_NOTICES.txt", "assets/templates/release.yml",
		"assets/templates/deploy.yml", "assets/templates/deployment.yaml":
		return true
	}
	return false
}

func (s *Server) readAgentFile(name string, limit int64) ([]byte, os.FileInfo, error) {
	if s.options.AgentDir == "" {
		return nil, nil, errAgentUnavailable
	}
	path, err := filepath.Abs(filepath.Join(s.options.AgentDir, filepath.FromSlash(name)))
	if err != nil {
		return nil, nil, errAgentUnavailable
	}
	var info os.FileInfo
	for current := path; ; current = filepath.Dir(current) {
		stat, err := os.Lstat(current)
		if err != nil || stat.Mode()&os.ModeSymlink != 0 {
			return nil, nil, errAgentUnavailable
		}
		if current == path {
			info = stat
		}
		if filepath.Dir(current) == current {
			break
		}
	}
	if !info.Mode().IsRegular() || info.Size() > limit {
		return nil, nil, errAgentUnavailable
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, nil, errAgentUnavailable
	}
	defer file.Close()
	opened, err := file.Stat()
	if err != nil || !os.SameFile(info, opened) || !opened.Mode().IsRegular() {
		return nil, nil, errAgentUnavailable
	}
	raw, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, nil, errAgentUnavailable
	}
	return raw, info, nil
}

func (s *Server) agentResource(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/agent/")
	if name == "deployctl.pyz" || name == "deployctl.pyz.sha256" {
		name = "assets/" + name
	}
	if (r.Method != "GET" && r.Method != "HEAD") || !agentPublicName(name) {
		failure(w, domain.ErrNotFound)
		return
	}
	raw, info, err := s.readAgentFile(name, maxAgentFile)
	if err != nil {
		failure(w, domain.ErrNotFound)
		return
	}
	switch filepath.Ext(name) {
	case ".zip":
		w.Header().Set("Content-Type", "application/zip")
	case ".pyz":
		w.Header().Set("Content-Type", "application/octet-stream")
	case ".md":
		w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	default:
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	}
	if strings.HasSuffix(name, ".zip") || strings.HasSuffix(name, ".pyz") {
		w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filepath.Base(name)))
	}
	digest := sha256.Sum256(raw)
	w.Header().Set("ETag", `"`+hex.EncodeToString(digest[:])+`"`)
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeContent(w, r, filepath.Base(name), info.ModTime(), bytes.NewReader(raw))
}

func (s *Server) agentAccess(w http.ResponseWriter, r *http.Request, _ auth.Principal) error {
	raw, _, err := s.readAgentFile("metadata.json", 16*1024)
	if err != nil {
		return errAgentUnavailable
	}
	var metadata struct {
		Version     string `json:"version"`
		SkillSHA256 string `json:"skill_sha256"`
		CLISHA256   string `json:"cli_sha256"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&metadata) != nil || !agentVersion.MatchString(metadata.Version) || !agentDigest.MatchString(metadata.SkillSHA256) || !agentDigest.MatchString(metadata.CLISHA256) {
		return errAgentUnavailable
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return errAgentUnavailable
	}
	if _, _, err = s.readAgentFile("SKILL.md", maxAgentFile); err != nil {
		return errAgentUnavailable
	}
	for name, expected := range map[string]string{"team-deploy-skill.zip": metadata.SkillSHA256, "assets/deployctl.pyz": metadata.CLISHA256} {
		contents, _, err := s.readAgentFile(name, maxAgentFile)
		if err != nil {
			return errAgentUnavailable
		}
		digest := sha256.Sum256(contents)
		if hex.EncodeToString(digest[:]) != expected {
			return errAgentUnavailable
		}
		checksum, _, err := s.readAgentFile(name+".sha256", 256)
		if err != nil || string(checksum) != expected+"  "+filepath.Base(name)+"\n" {
			return errAgentUnavailable
		}
	}
	reply(w, 200, map[string]string{"version": metadata.Version, "server_url": s.options.PublicOrigin,
		"skill_name": "team-deploy", "skill_url": "/agent/team-deploy-skill.zip", "skill_sha256": metadata.SkillSHA256,
		"cli_url": "/agent/deployctl.pyz", "cli_sha256": metadata.CLISHA256, "entry_url": "/agent/SKILL.md",
		"release_url": "https://github.com/art-shier/deployctl/releases/tag/v" + metadata.Version})
	return nil
}
