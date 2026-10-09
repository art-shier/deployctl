// Package config loads private bootstrap material; it never logs values.
package config

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/httpapi"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/secrets"
)

type Config struct {
	DatabaseURL, Listen string
	Cipher              *secrets.Cipher
	API                 httpapi.Options
}

func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}
func normal(path string, private bool) error {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	for current := absolute; ; current = filepath.Dir(current) {
		info, e := os.Lstat(current)
		if e != nil {
			return e
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return errors.New("key paths cannot contain links")
		}
		if current == absolute && private && runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0 {
			return errors.New("private key paths require private permissions")
		}
		if filepath.Dir(current) == current {
			break
		}
	}
	return nil
}
func read(path string, private bool) ([]byte, error) {
	if err := normal(path, private); err != nil {
		return nil, err
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16384 {
		return nil, errors.New("key must be a bounded normal file")
	}
	return os.ReadFile(path)
}
func writeNew(path string, data []byte, mode os.FileMode) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if os.IsExist(err) {
		return normal(path, mode == 0600)
	}
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}
func signingKey(path string) (*rsa.PrivateKey, error) {
	raw, err := read(path, true)
	if err != nil {
		return nil, err
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return nil, errors.New("invalid signing key")
	}
	key, err := x509.ParsePKCS1PrivateKey(block.Bytes)
	if err != nil || key.N.BitLen() < 2048 {
		return nil, errors.New("invalid signing key")
	}
	return key, key.Validate()
}
func Initialize(dir string) error {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	if err := normal(dir, true); err != nil {
		return err
	}
	cipher := make([]byte, 32)
	if _, err := rand.Read(cipher); err != nil {
		return err
	}
	if err := writeNew(filepath.Join(dir, "encryption.key"), cipher, 0600); err != nil {
		return err
	}
	if _, err := os.Lstat(filepath.Join(dir, "signing.key")); os.IsNotExist(err) {
		key, e := rsa.GenerateKey(rand.Reader, 3072)
		if e != nil {
			return e
		}
		if e = writeNew(filepath.Join(dir, "signing.key"), pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)}), 0600); e != nil {
			return e
		}
	}
	key, err := signingKey(filepath.Join(dir, "signing.key"))
	if err != nil {
		return err
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(time.Now().UnixNano()), Subject: pkix.Name{CommonName: "ctl registry signing"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(10, 0, 0), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	cert, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return err
	}
	if err = writeNew(filepath.Join(dir, "signing.crt"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert}), 0644); err != nil {
		return err
	}
	token, err := auth.NewToken()
	if err != nil {
		return err
	}
	return writeNew(filepath.Join(dir, "owner.token"), []byte(token+"\n"), 0600)
}
func Load() (Config, error) {
	c := Config{DatabaseURL: os.Getenv("CTL_DATABASE_URL"), Listen: env("CTL_LISTEN", ":8080")}
	if file := os.Getenv("CTL_DATABASE_URL_FILE"); file != "" {
		if c.DatabaseURL != "" {
			return c, errors.New("use only one database configuration source")
		}
		raw, err := read(file, true)
		if err != nil {
			return c, errors.New("invalid database URL file")
		}
		c.DatabaseURL = strings.TrimSpace(string(raw))
	}
	if c.DatabaseURL == "" {
		return c, errors.New("CTL_DATABASE_URL required")
	}
	dir := env("CTL_KEYS_DIR", "/run/ctl-keys")
	if err := normal(dir, true); err != nil {
		return c, err
	}
	raw, err := read(filepath.Join(dir, "encryption.key"), true)
	if err != nil {
		return c, err
	}
	c.Cipher, err = secrets.New(raw)
	if err != nil {
		return c, err
	}
	key, err := signingKey(filepath.Join(dir, "signing.key"))
	if err != nil {
		return c, err
	}
	certRaw, err := read(filepath.Join(dir, "signing.crt"), false)
	if err != nil {
		return c, err
	}
	block, _ := pem.Decode(certRaw)
	if block == nil {
		return c, errors.New("invalid signing certificate")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return c, err
	}
	pub, ok := cert.PublicKey.(*rsa.PublicKey)
	if !ok || pub.N.Cmp(key.N) != 0 || pub.E != key.E || time.Now().After(cert.NotAfter) {
		return c, errors.New("signing certificate mismatch or expiry")
	}
	raw, err = read(filepath.Join(dir, "owner.token"), true)
	if err != nil {
		return c, err
	}
	token := strings.TrimSpace(string(raw))
	if !strings.HasPrefix(token, "ctl_") || len(token) != 47 {
		return c, errors.New("invalid owner token")
	}
	origin := env("CTL_PUBLIC_ORIGIN", "http://127.0.0.1:8080")
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost"))) {
		return c, errors.New("CTL_PUBLIC_ORIGIN requires HTTPS origin; loopback HTTP only for tests")
	}
	host := env("CTL_REGISTRY_HOST", "127.0.0.1:5000")
	parsed, err := url.Parse("https://" + host)
	if err != nil || parsed.Host != host || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return c, errors.New("invalid registry host")
	}
	if _, _, err = net.SplitHostPort(c.Listen); err != nil {
		return c, errors.New("invalid listen address")
	}
	signer := &auth.RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}
	c.API = httpapi.Options{OwnerHash: auth.HashToken(token), PublicOrigin: origin, CookieSecure: u.Scheme == "https", RegistryPublicHost: host, ArtifactsDir: env("CTL_ARTIFACTS_DIR", "/var/lib/ctl/artifacts"), WebDir: env("CTL_WEB_DIR", "/app/web"), AgentDir: env("CTL_AGENT_DIR", "/app/agent"), Signer: signer, Verifier: registry.Verifier{InternalURL: env("CTL_REGISTRY_INTERNAL_URL", "http://registry:5000"), PublicHost: host, Signer: signer}}
	return c, nil
}
