package auth

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestRegistrySignatureAndScopedAccess(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	signer := RegistrySigner{Key: key, Issuer: "ctl", Service: "ctl-registry"}
	principal := Principal{ID: "fixture", Role: "deployer", Project: "notes", Environments: []string{"prod"}}
	access := ScopedAccess(principal, "repository:notes:pull,push", "notes", "notes")
	if len(access) != 1 || len(access[0].Actions) != 1 || access[0].Actions[0] != "pull" {
		t.Fatal("scope escaped")
	}
	if len(ScopedAccess(principal, "repository:other:pull", "notes", "notes")) != 0 {
		t.Fatal("cross repository")
	}
	token, err := signer.Sign(principal, access, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatal("invalid JWT")
	}
	signature, _ := base64.RawURLEncoding.DecodeString(parts[2])
	sum := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(&key.PublicKey, crypto.SHA256, sum[:], signature) != nil {
		t.Fatal("invalid signature")
	}
	body, _ := base64.RawURLEncoding.DecodeString(parts[1])
	var claims map[string]any
	json.Unmarshal(body, &claims)
	if claims["aud"] != "ctl-registry" || claims["iss"] != "ctl" || int64(claims["exp"].(float64)) <= time.Now().Unix() {
		t.Fatal("invalid claims")
	}
}
