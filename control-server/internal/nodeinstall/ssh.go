// Package nodeinstall transports a bounded, node-owned bootstrap over pinned SSH.
package nodeinstall

import (
	"archive/tar"
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"errors"
	"fmt"
	"io"
	"net"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/ssh"
)

//go:embed bootstrap.sh
var Bootstrap []byte

type Target struct {
	Host string `json:"host"`
	Port int    `json:"port"`
	User string `json:"user"`
}
type Request struct {
	Target
	ID           string `json:"id"`
	Fingerprint  string `json:"fingerprint"`
	Password     string `json:"password"`
	SudoPassword string `json:"sudo_password"`
}

var userPattern = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}\$?$`)

func (t Target) Validate() error {
	if len(t.Host) == 0 || len(t.Host) > 253 || strings.ContainsAny(t.Host, " /\\\t\r\n\x00") || strings.HasPrefix(t.Host, "-") || t.Port < 1 || t.Port > 65535 || !userPattern.MatchString(t.User) {
		return errors.New("invalid_target")
	}
	return nil
}
func (r Request) Validate() error {
	if err := r.Target.Validate(); err != nil {
		return err
	}
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(r.ID) || !strings.HasPrefix(r.Fingerprint, "SHA256:") || len(r.Fingerprint) > 100 || len(r.Password) == 0 || len(r.Password) > 4096 || len(r.SudoPassword) > 4096 || strings.ContainsAny(r.Password+r.SudoPassword, "\r\n\x00") {
		return errors.New("invalid_request")
	}
	return nil
}
func dial(ctx context.Context, t Target) (net.Conn, error) {
	if err := t.Validate(); err != nil {
		return nil, err
	}
	ips, err := net.DefaultResolver.LookupIPAddr(ctx, t.Host)
	if err != nil {
		return nil, errors.New("ssh_unreachable")
	}
	if len(ips) == 0 {
		return nil, errors.New("ssh_unreachable")
	}
	for _, ip := range ips {
		if ip.IP.IsLoopback() || ip.IP.IsUnspecified() || ip.IP.IsMulticast() || ip.IP.IsLinkLocalUnicast() || ip.IP.IsLinkLocalMulticast() {
			return nil, errors.New("invalid_target")
		}
	}
	c, err := (&net.Dialer{Timeout: 10 * time.Second}).DialContext(ctx, "tcp", net.JoinHostPort(ips[0].IP.String(), strconv.Itoa(t.Port)))
	if err != nil {
		return nil, errors.New("ssh_unreachable")
	}
	return c, nil
}
func handshake(ctx context.Context, conn net.Conn, t Target, password, fingerprint string) (*ssh.Client, string, error) {
	defer conn.SetDeadline(time.Time{})
	conn.SetDeadline(time.Now().Add(15 * time.Second))
	stop := context.AfterFunc(ctx, func() { conn.Close() })
	defer stop()
	found := ""
	config := &ssh.ClientConfig{User: t.User, HostKeyCallback: func(_ string, _ net.Addr, key ssh.PublicKey) error {
		found = ssh.FingerprintSHA256(key)
		if fingerprint == "" {
			return errors.New("probe_complete")
		}
		if found != fingerprint {
			return errors.New("host_key_changed")
		}
		return nil
	}}
	if fingerprint != "" {
		config.Auth = []ssh.AuthMethod{ssh.Password(password)}
	}
	c, ch, req, err := ssh.NewClientConn(conn, net.JoinHostPort(t.Host, strconv.Itoa(t.Port)), config)
	if err != nil {
		conn.Close()
		if fingerprint == "" && found != "" {
			return nil, found, nil
		}
		if found != "" && found != fingerprint {
			return nil, found, errors.New("host_key_changed")
		}
		return nil, found, errors.New("ssh_authentication_failed")
	}
	return ssh.NewClient(c, ch, req), found, nil
}
func Probe(ctx context.Context, t Target) (string, error) {
	conn, err := dial(ctx, t)
	if err != nil {
		return "", err
	}
	defer conn.Close()
	_, fingerprint, err := handshake(ctx, conn, t, "", "")
	return fingerprint, err
}
func quote(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'" }
func bundle(cli []byte, token string) ([]byte, error) {
	var b bytes.Buffer
	w := tar.NewWriter(&b)
	for _, item := range []struct {
		name    string
		content []byte
	}{{"node-bootstrap.sh", Bootstrap}, {"deployctl.pyz", cli}, {"deployctl.pyz.sha256", []byte(fmt.Sprintf("%x  deployctl.pyz\n", sha256.Sum256(cli)))}, {"enrollment.token", []byte(token)}} {
		if err := w.WriteHeader(&tar.Header{Name: item.name, Mode: 0600, Size: int64(len(item.content))}); err != nil {
			return nil, err
		}
		if _, err := w.Write(item.content); err != nil {
			return nil, err
		}
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}

var stages = map[string]bool{"checking": true, "python": true, "docker": true, "agent": true, "heartbeat": true}
var codes = map[string]bool{"invalid_arguments": true, "root_required": true, "systemd_required": true, "agent_exists": true, "cli_exists": true, "unsupported_os": true, "package_conflict": true, "python_required": true, "repository_exists": true, "docker_unavailable": true, "docker_old": true, "compose_old": true, "installation_failed": true}

func Run(ctx context.Context, r Request, server string, cli []byte, token string, progress func(string)) error {
	conn, err := dial(ctx, r.Target)
	if err != nil {
		return err
	}
	client, _, err := handshake(ctx, conn, r.Target, r.Password, r.Fingerprint)
	if err != nil {
		return err
	}
	defer client.Close()
	return runClient(ctx, client, r, server, cli, token, progress)
}

func runClient(ctx context.Context, client *ssh.Client, r Request, server string, cli []byte, token string, progress func(string)) error {
	stop := context.AfterFunc(ctx, func() { client.Close() })
	defer stop()
	session, err := client.NewSession()
	if err != nil {
		return errors.New("ssh_session_failed")
	}
	defer session.Close()
	payload, err := bundle(cli, token)
	if err != nil {
		return errors.New("bundle_failed")
	}
	// sudo may consume its password or (NOPASSWD) leave it in stdin. The fixed
	// random-looking request marker lets the root wrapper handle both without echo.
	marker := "CTL_PAYLOAD_" + r.ID
	wrapper := `set -eu; umask 077; IFS= read -r line; if [ "$line" != ` + quote(marker) + ` ]; then IFS= read -r line; fi; [ "$line" = ` + quote(marker) + ` ]; command -v flock >/dev/null; exec 9>/run/ctl-node-install.lock; flock -n 9; tmp=$(mktemp -d /run/ctl-node-install.XXXXXXXX); trap 'rm -rf -- "$tmp"' EXIT; tar -xf - -C "$tmp"; timeout --kill-after=30s 1200 bash "$tmp/node-bootstrap.sh" ` + quote(server) + ` "$tmp/deployctl.pyz" "$tmp/enrollment.token"`
	command := "/bin/bash -c " + quote(wrapper)
	prefix := marker + "\n"
	if r.User != "root" {
		password := r.SudoPassword
		if password == "" {
			password = r.Password
		}
		prefix = password + "\n" + prefix
		command = "sudo -S -p '' -- " + command
	}
	session.Stdin = io.MultiReader(strings.NewReader(prefix), bytes.NewReader(payload))
	stdout, err := session.StdoutPipe()
	if err != nil {
		return errors.New("ssh_session_failed")
	}
	session.Stderr = io.Discard
	if err = session.Start(command); err != nil {
		return errors.New("ssh_session_failed")
	}
	code := ""
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 4096), 8192)
	for scanner.Scan() {
		line := scanner.Text()
		if v, ok := strings.CutPrefix(line, "CTL_STAGE "); ok && stages[v] {
			progress(v)
		}
		if v, ok := strings.CutPrefix(line, "CTL_ERROR "); ok && codes[v] {
			code = v
		}
	}
	// Oversized/untrusted output must not block SSH window flow and hang Wait.
	if scanner.Err() != nil {
		client.Close()
		return errors.New("invalid_remote_output")
	}
	err = session.Wait()
	if ctx.Err() != nil {
		return errors.New("installation_timeout")
	}
	if code != "" {
		return errors.New(code)
	}
	if err != nil {
		return errors.New("remote_install_failed")
	}
	return nil
}
