package auth

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base32"
	"encoding/base64"
	"encoding/json"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

type Access struct {
	Type    string   `json:"type"`
	Name    string   `json:"name"`
	Actions []string `json:"actions"`
}
type RegistrySigner struct {
	Key     *rsa.PrivateKey
	Issuer  string
	Service string
}

func ScopedAccess(p Principal, scope, project, repository string) []Access {
	out := []Access{}
	parts := strings.Split(scope, ":")
	if len(parts) != 3 || parts[0] != "repository" || parts[1] != repository {
		return out
	}
	actions := []string{}
	for _, action := range strings.Split(parts[2], ",") {
		if (action == "pull" || action == "push") && p.Can("registry."+action, project, "") {
			actions = append(actions, action)
		}
	}
	if len(actions) > 0 {
		out = append(out, Access{"repository", repository, actions})
	}
	return out
}
func (s RegistrySigner) Sign(p Principal, access []Access, now time.Time) (string, error) {
	if s.Key == nil || s.Key.N.BitLen() < 2048 {
		return "", domain.ErrInvalid
	}
	der, err := x509.MarshalPKIXPublicKey(&s.Key.PublicKey)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(der)
	// OCI Distribution/libtrust fingerprint: 240 bits, base32, groups of four.
	encoded := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(sum[:30])
	groups := []string{}
	for i := 0; i < len(encoded); i += 4 {
		groups = append(groups, encoded[i:i+4])
	}
	header, _ := json.Marshal(map[string]any{"typ": "JWT", "alg": "RS256", "kid": strings.Join(groups, ":")})
	body, _ := json.Marshal(map[string]any{"iss": s.Issuer, "sub": p.ID, "aud": s.Service, "exp": now.Add(5 * time.Minute).Unix(), "nbf": now.Add(-5 * time.Second).Unix(), "iat": now.Unix(), "jti": domain.NewID(), "access": access})
	unsigned := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(body)
	hash := sha256.Sum256([]byte(unsigned))
	sig, err := rsa.SignPKCS1v15(rand.Reader, s.Key, crypto.SHA256, hash[:])
	if err != nil {
		return "", err
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(sig), nil
}
