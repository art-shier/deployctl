package artifacts

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"unicode/utf8"

	"github.com/art-shier/deployctl/control-server/internal/domain"
	"gopkg.in/yaml.v3"
)

const MaxPackage = 10 * 1024 * 1024

type ValidatedPackage struct {
	Release  domain.Release
	Raw      []byte
	Manifest map[string]any
}

func readYAML(raw []byte) (any, error) {
	if len(raw) > 65536 || !utf8.Valid(raw) {
		return nil, domain.ErrInvalid
	}
	decoder := yaml.NewDecoder(bytes.NewReader(raw))
	var data any
	if err := decoder.Decode(&data); err != nil {
		return nil, domain.ErrInvalid
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, domain.ErrInvalid
	}
	return data, nil
}
func Validate(reader io.Reader, expectedSHA, project string) (ValidatedPackage, error) {
	out := ValidatedPackage{}
	raw, err := io.ReadAll(io.LimitReader(reader, MaxPackage+1))
	if err != nil || len(raw) > MaxPackage {
		return out, domain.ErrInvalid
	}
	sum := sha256.Sum256(raw)
	checksum := hex.EncodeToString(sum[:])
	if expectedSHA != "" && checksum != expectedSHA {
		return out, domain.ErrInvalid
	}
	gz, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return out, domain.ErrInvalid
	}
	expanded, err := io.ReadAll(io.LimitReader(gz, 5*1024*1024+1))
	gz.Close()
	if err != nil || len(expanded) > 5*1024*1024 {
		return out, domain.ErrInvalid
	}
	content := map[string][]byte{}
	allowed := map[string]bool{"release.yaml": true, "compose.yaml": true, ".env.example": true, "README.md": true, "hooks/pre-install.sh": true, "hooks/post-install.sh": true}
	archive := tar.NewReader(bytes.NewReader(expanded))
	for {
		h, e := archive.Next()
		if e == io.EOF {
			break
		}
		if e != nil || !allowed[h.Name] || content[h.Name] != nil || (h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA) || h.Size < 0 || h.Size > 1024*1024 {
			return out, domain.ErrInvalid
		}
		data, e := io.ReadAll(io.LimitReader(archive, 1024*1024+1))
		if e != nil {
			return out, domain.ErrInvalid
		}
		content[h.Name] = data
	}
	for _, name := range []string{"release.yaml", "compose.yaml", ".env.example", "README.md"} {
		if _, ok := content[name]; !ok {
			return out, domain.ErrInvalid
		}
	}
	parsed, err := readYAML(content["release.yaml"])
	if err != nil {
		return out, err
	}
	manifest, err := validateManifest(parsed, project, content)
	if err != nil {
		return out, err
	}
	compose, err := readYAML(content["compose.yaml"])
	if err != nil {
		return out, err
	}
	expected := renderCompose(manifest)
	a, e1 := json.Marshal(compose)
	b, e2 := json.Marshal(expected)
	if e1 != nil || e2 != nil || !bytes.Equal(a, b) {
		return out, domain.ErrInvalid
	}
	release := domain.Release{Project: project, Version: manifest["version"].(string), Image: manifest["image"].(string), SHA256: checksum, Size: int64(len(raw))}
	if c, ok := manifest["commit"].(string); ok {
		release.Commit = c
	}
	return ValidatedPackage{release, raw, manifest}, nil
}
func PublishFile(root string, p ValidatedPackage) error {
	if len(p.Release.SHA256) != 64 {
		return domain.ErrInvalid
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return err
	}
	destination := filepath.Join(root, p.Release.SHA256+".tar.gz")
	if info, err := os.Lstat(destination); err == nil {
		if !info.Mode().IsRegular() {
			return domain.ErrInvalid
		}
		existing, err := os.ReadFile(destination)
		if err != nil {
			return err
		}
		if !bytes.Equal(existing, p.Raw) {
			return domain.ErrConflict
		}
		return nil
	} else if !os.IsNotExist(err) {
		return err
	}
	temporary, err := os.CreateTemp(root, "upload-*")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if err = temporary.Chmod(0600); err == nil {
		_, err = temporary.Write(p.Raw)
	}
	if err == nil {
		err = temporary.Sync()
	}
	closeErr := temporary.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(name, destination)
}
