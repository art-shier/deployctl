package artifacts

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"testing"
)

func TestRealStandardPackagesAndChecksum(t *testing.T) {
	for _, mode := range []string{"v1", "v2", "refresh"} {
		raw, err := os.ReadFile("testdata/" + mode + ".tar.gz")
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		expected := hex.EncodeToString(sum[:])
		info, err := Validate(bytes.NewReader(raw), expected, "notes")
		if err != nil {
			t.Fatal(mode, err)
		}
		if info.Release.Version != "v1.0.0" || info.Release.SHA256 != expected {
			t.Fatal("package metadata mismatch")
		}
		if _, err = Validate(bytes.NewReader(raw), expected, "other"); err == nil {
			t.Fatal("cross project package accepted")
		}
		if _, err = Validate(bytes.NewReader(raw), "bad", "notes"); err == nil {
			t.Fatal("checksum ignored")
		}
	}
}
func rewrite(t *testing.T, mutate func(string, []byte) (string, []byte), extra *tar.Header) []byte {
	t.Helper()
	raw, _ := os.ReadFile("testdata/refresh.tar.gz")
	g, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	r := tar.NewReader(g)
	var buffer bytes.Buffer
	z := gzip.NewWriter(&buffer)
	w := tar.NewWriter(z)
	for {
		h, err := r.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		data, _ := io.ReadAll(r)
		name, data := mutate(h.Name, data)
		h.Name = name
		h.Size = int64(len(data))
		w.WriteHeader(h)
		w.Write(data)
	}
	if extra != nil {
		w.WriteHeader(extra)
	}
	w.Close()
	z.Close()
	return buffer.Bytes()
}
func TestUnsafeMembersHookAndComposeTampering(t *testing.T) {
	cases := [][]byte{
		rewrite(t, func(n string, b []byte) (string, []byte) {
			if n == "README.md" {
				n = "../outside"
			}
			return n, b
		}, nil),
		rewrite(t, func(n string, b []byte) (string, []byte) {
			if n == "hooks/pre-install.sh" {
				b = []byte("exit 7\n")
			}
			return n, b
		}, nil),
		rewrite(t, func(n string, b []byte) (string, []byte) {
			if n == "compose.yaml" {
				b = bytes.ReplaceAll(b, []byte("512m"), []byte("1g"))
			}
			return n, b
		}, nil),
		rewrite(t, func(n string, b []byte) (string, []byte) { return n, b }, &tar.Header{Name: "README.md", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"}),
	}
	for _, raw := range cases {
		if _, err := Validate(bytes.NewReader(raw), "", "notes"); err == nil {
			t.Fatal("unsafe package accepted")
		}
	}
}
