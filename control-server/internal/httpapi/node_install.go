package httpapi

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/nodeinstall"
)

func (s *Server) nodeInstallRoutes() {
	s.mux.HandleFunc("GET /api/v1/node-installations", s.wrap(s.nodeInstallations))
	s.mux.HandleFunc("POST /api/v1/node-installations", s.wrap(s.createNodeInstallation))
	s.mux.HandleFunc("POST /api/v1/node-installations/probe", s.wrap(s.probeNodeInstallation))
}
func (s *Server) nodeInstallations(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	jobs, err := s.store.ListNodeInstallations(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, jobs)
	return nil
}
func (s *Server) probeNodeInstallation(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var target nodeinstall.Target
	if err := decode(w, r, &target); err != nil {
		return err
	}
	if target.Validate() != nil {
		return domain.ErrInvalid
	}
	// Bounded probes cannot be used to exhaust the center's SSH connections.
	select {
	case s.sshProbes <- struct{}{}:
		defer func() { <-s.sshProbes }()
	default:
		return domain.ErrConflict
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	fingerprint, err := nodeinstall.Probe(ctx, target)
	if err != nil {
		reply(w, 422, map[string]string{"message": err.Error()})
		return nil
	}
	reply(w, 200, map[string]string{"fingerprint": fingerprint})
	return nil
}
func (s *Server) createNodeInstallation(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var in nodeinstall.Request
	if err := decode(w, r, &in); err != nil {
		return err
	}
	in.Host = strings.ToLower(strings.TrimSuffix(in.Host, "."))
	if in.Validate() != nil {
		return domain.ErrInvalid
	}
	origin, err := url.Parse(s.options.PublicOrigin)
	if err != nil || origin.Scheme != "https" || origin.Host == "" || origin.User != nil {
		return domain.ErrInvalid
	}
	cli, _, err := s.readAgentFile("assets/deployctl.pyz", maxAgentFile)
	if err != nil {
		return err
	}
	checksum, _, err := s.readAgentFile("assets/deployctl.pyz.sha256", 1024)
	if err != nil {
		return err
	}
	fields := strings.Fields(string(checksum))
	if len(fields) != 2 || fields[0] != fmt.Sprintf("%x", sha256.Sum256(cli)) {
		return domain.ErrInvalid
	}
	job, token, err := s.store.CreateNodeInstallation(r.Context(), in, p.ID)
	if err != nil {
		return err
	}
	if token != "" {
		go s.runNodeInstallation(in, origin.String(), cli, token)
	}
	reply(w, 202, job)
	return nil
}
func (s *Server) runNodeInstallation(in nodeinstall.Request, origin string, cli []byte, token string) {
	ctx, cancel := context.WithTimeout(context.Background(), 23*time.Minute)
	defer cancel()
	stage := "connecting"
	update := func(status, code string) {
		c, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		_ = s.store.UpdateNodeInstallation(c, in.ID, status, stage, code)
	}
	err := nodeinstall.Run(ctx, in, origin, cli, token, func(value string) { stage = value; update("running", "") })
	// Drop credential references as soon as the SSH phase ends.
	in.Password = ""
	in.SudoPassword = ""
	token = ""
	if err != nil {
		update("failed", err.Error())
		return
	}
	stage = "heartbeat"
	update("running", "")
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	deadline := time.NewTimer(90 * time.Second)
	defer deadline.Stop()
	for {
		select {
		case <-ticker.C:
			ready, e := s.store.InstallationHeartbeat(ctx, in.ID)
			if e == nil && ready {
				stage = "complete"
				update("succeeded", "")
				return
			}
		case <-deadline.C:
			update("failed", "heartbeat_timeout")
			return
		case <-ctx.Done():
			update("failed", "installation_timeout")
			return
		}
	}
}
