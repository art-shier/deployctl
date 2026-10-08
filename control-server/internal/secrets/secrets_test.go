package secrets

import "testing"

func TestCiphertextBoundToRevision(t *testing.T) {
	cipher, err := New(make([]byte, 32))
	if err != nil {
		t.Fatal(err)
	}
	sealed, err := cipher.Seal([]byte(" secret $ value "), "notes/prod/1")
	if err != nil {
		t.Fatal(err)
	}
	plain, err := cipher.Open(sealed, "notes/prod/1")
	if err != nil || string(plain) != " secret $ value " {
		t.Fatal("literal value lost", err)
	}
	for _, aad := range []string{"notes/test/1", "other/prod/1", "notes/prod/2"} {
		if _, err = cipher.Open(sealed, aad); err == nil {
			t.Fatal("cross context decryption")
		}
	}
	sealed[len(sealed)-1] ^= 1
	if _, err = cipher.Open(sealed, "notes/prod/1"); err == nil {
		t.Fatal("tampering accepted")
	}
	if _, err = New(make([]byte, 16)); err == nil {
		t.Fatal("non AES256 key accepted")
	}
}
