package secrets

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"errors"
)

type Cipher struct{ aead cipher.AEAD }

func New(key []byte) (*Cipher, error) {
	if len(key) != 32 {
		return nil, errors.New("encryption key must have 32 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &Cipher{aead}, nil
}
func (c *Cipher) Seal(value []byte, context string) ([]byte, error) {
	nonce := make([]byte, c.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return c.aead.Seal(nonce, nonce, value, []byte(context)), nil
}
func (c *Cipher) Open(value []byte, context string) ([]byte, error) {
	n := c.aead.NonceSize()
	if len(value) < n {
		return nil, errors.New("invalid encrypted configuration")
	}
	plain, err := c.aead.Open(nil, value[:n], value[n:], []byte(context))
	if err != nil {
		return nil, errors.New("encrypted configuration integrity failure")
	}
	return plain, nil
}
