package nodeinstall

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"io"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/crypto/ssh"
)

func sshFixture(t *testing.T) (net.Conn, string, *atomic.Int32) {
	t.Helper()
	_, key, _ := ed25519.GenerateKey(rand.Reader)
	signer, _ := ssh.NewSignerFromKey(key)
	var calls atomic.Int32
	cfg := &ssh.ServerConfig{PasswordCallback: func(_ ssh.ConnMetadata, p []byte) (*ssh.Permissions, error) {
		calls.Add(1)
		if string(p) != "secret" {
			return nil, errors.New("bad password")
		}
		return nil, nil
	}}
	cfg.AddHostKey(signer)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	go func() {
		c, e := listener.Accept()
		if e != nil {
			return
		}
		defer c.Close()
		s, channels, requests, e := ssh.NewServerConn(c, cfg)
		if e != nil {
			return
		}
		defer s.Close()
		go ssh.DiscardRequests(requests)
		for channel := range channels {
			channel.Reject(ssh.UnknownChannelType, "test")
		}
	}()
	conn, err := net.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn, ssh.FingerprintSHA256(signer.PublicKey()), &calls
}
func TestHostKeyBeforePassword(t *testing.T) {
	for _, mode := range []string{"probe", "wrong", "correct"} {
		t.Run(mode, func(t *testing.T) {
			conn, key, calls := sshFixture(t)
			expected := key
			if mode == "probe" {
				expected = ""
			}
			if mode == "wrong" {
				expected = "SHA256:wrong"
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second*3)
			defer cancel()
			client, found, err := handshake(ctx, conn, Target{"test", 22, "root"}, "secret", expected)
			if client != nil {
				client.Close()
			}
			if found != key {
				t.Fatal("missing fingerprint")
			}
			if mode == "wrong" && err == nil {
				t.Fatal("accepted changed key")
			}
			if mode != "wrong" && err != nil {
				t.Fatal(err)
			}
			if mode != "correct" && calls.Load() != 0 {
				t.Fatal("password sent before key confirmation")
			}
			if mode == "correct" && calls.Load() != 1 {
				t.Fatal("authentication missing")
			}
		})
	}
}
func TestBundleFixedPathsAndPrivatePermissions(t *testing.T) {
	data, err := bundle([]byte("cli"), "one-use-token")
	if err != nil {
		t.Fatal(err)
	}
	r := tar.NewReader(bytes.NewReader(data))
	count := 0
	for {
		h, e := r.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			t.Fatal(e)
		}
		if strings.Contains(h.Name, "/") || h.Mode != 0600 {
			t.Fatal(h)
		}
		count++
	}
	if count != 4 {
		t.Fatal(count)
	}
}
func TestRejectLocalTargets(t *testing.T) {
	for _, host := range []string{"127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1"} {
		_, err := Probe(context.Background(), Target{host, 22, "root"})
		if err == nil || err.Error() != "invalid_target" {
			t.Fatal(host, err)
		}
	}
}
func TestInvalidRequests(t *testing.T) {
	r := Request{Target: Target{"10.0.0.1", 22, "root"}, ID: strings.Repeat("a", 32), Fingerprint: "SHA256:key", Password: "secret"}
	if r.Validate() != nil {
		t.Fatal("valid rejected")
	}
	r.Password = "secret\ncommand"
	if r.Validate() == nil {
		t.Fatal("newline password accepted")
	}
}

func TestSSHInstallTransport(t *testing.T) {
	for _, user := range []string{"root", "deploy"} {
		t.Run(user, func(t *testing.T) {
			_, key, _ := ed25519.GenerateKey(rand.Reader)
			signer, _ := ssh.NewSignerFromKey(key)
			cfg := &ssh.ServerConfig{PasswordCallback: func(_ ssh.ConnMetadata, p []byte) (*ssh.Permissions, error) { return nil, nil }}
			cfg.AddHostKey(signer)
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			type received struct {
				command string
				stdin   []byte
			}
			capture := make(chan received, 1)
			go func() {
				conn, e := listener.Accept()
				if e != nil {
					return
				}
				defer conn.Close()
				server, channels, requests, e := ssh.NewServerConn(conn, cfg)
				if e != nil {
					return
				}
				defer server.Close()
				go ssh.DiscardRequests(requests)
				channel := <-channels
				ch, reqs, e := channel.Accept()
				if e != nil {
					return
				}
				defer ch.Close()
				request := <-reqs
				var command struct{ Value string }
				_ = ssh.Unmarshal(request.Payload, &command)
				request.Reply(true, nil)
				input, _ := io.ReadAll(ch)
				capture <- received{command.Value, input}
				ch.Write([]byte("raw-password-must-not-escape\nCTL_STAGE checking\nCTL_STAGE raw-password-must-not-escape\nCTL_STAGE heartbeat\n"))
				ch.Stderr().Write([]byte("another-secret"))
				ch.SendRequest("exit-status", false, ssh.Marshal(struct{ Status uint32 }{0}))
			}()
			conn, e := net.Dial("tcp", listener.Addr().String())
			if e != nil {
				t.Fatal(e)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			r := Request{Target: Target{"test", 22, user}, ID: strings.Repeat("a", 32), Password: "login-secret", SudoPassword: "sudo-secret", Fingerprint: ssh.FingerprintSHA256(signer.PublicKey())}
			client, _, e := handshake(ctx, conn, r.Target, r.Password, r.Fingerprint)
			if e != nil {
				t.Fatal(e)
			}
			defer client.Close()
			var progress []string
			e = runClient(ctx, client, r, "https://center.example", []byte("test-cli"), "enrollment-secret", func(stage string) { progress = append(progress, stage) })
			if e != nil {
				t.Fatal(e)
			}
			got := <-capture
			if strings.Contains(got.command, "secret") || !strings.Contains(got.command, "/run/ctl-node-install.") {
				t.Fatal("unsafe command", got.command)
			}
			prefix := "CTL_PAYLOAD_" + r.ID + "\n"
			if user != "root" {
				prefix = "sudo-secret\n" + prefix
			}
			if !bytes.HasPrefix(got.stdin, []byte(prefix)) {
				t.Fatal("missing stdin framing")
			}
			archive := tar.NewReader(bytes.NewReader(got.stdin[len(prefix):]))
			found := false
			for {
				h, e := archive.Next()
				if e == io.EOF {
					break
				}
				if e != nil {
					t.Fatal(e)
				}
				if h.Name == "enrollment.token" {
					content, _ := io.ReadAll(archive)
					found = string(content) == "enrollment-secret"
				}
			}
			if !found {
				t.Fatal("missing enrollment")
			}
			if strings.Join(progress, ",") != "checking,heartbeat" {
				t.Fatal("untrusted output escaped", progress)
			}
		})
	}
}
