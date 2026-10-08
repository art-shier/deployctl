package auth

import (
	"crypto/rand"
	"crypto/rsa"
	"strings"
	"testing"
	"time"
)

func TestRegistryGatewayVerifiesSignatureScopeAudienceAndExpiry(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	signer := RegistrySigner{Key: key, Issuer: "ctl", Service: "registry"}
	now := time.Now()
	token, _ := signer.Sign(Principal{Role: "publisher"}, []Access{{Type: "repository", Name: "notes", Actions: []string{"push"}}}, now)
	if !signer.Allows(token, "notes", "push", now) {
		t.Fatal("valid scoped push rejected")
	}
	if signer.Allows(token, "other", "push", now) || signer.Allows(token, "notes", "delete", now) || signer.Allows(token, "notes", "push", now.Add(6*time.Minute)) {
		t.Fatal("scope/expiry bypass")
	}
	parts := strings.Split(token, ".")
	parts[1] = "e30"
	if signer.Allows(strings.Join(parts, "."), "notes", "push", now) {
		t.Fatal("tampered token accepted")
	}
	signer.Service = "other"
	if signer.Allows(token, "notes", "push", now) {
		t.Fatal("audience mismatch")
	}
}
