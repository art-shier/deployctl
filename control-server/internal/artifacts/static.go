package artifacts

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

const MaxStaticPackage int64 = 256 * 1024 * 1024
const MaxStaticExpanded int64 = 1024 * 1024 * 1024
const MaxStaticFile int64 = 256 * 1024 * 1024
const MaxStaticEntries = 50000

type staticEntries struct {
	seen         map[string]bool
	parents      map[string]bool
	nodes        map[string]bool
	count, files int
	total        int64
}

func staticName(name string, directory bool) (string, error) {
	if !utf8.ValidString(name) || len(name) > 4096 || strings.Contains(name, "\\") || strings.HasPrefix(name, "/") || regexp.MustCompile(`^[A-Za-z]:`).MatchString(name) {
		return "", domain.ErrInvalid
	}
	for _, r := range name {
		if r < 32 || r >= 127 && r <= 159 {
			return "", domain.ErrInvalid
		}
	}
	if !directory && strings.HasSuffix(name, "/") {
		return "", domain.ErrInvalid
	}
	out := []string{}
	for _, p := range strings.Split(strings.TrimRight(name, "/"), "/") {
		if p == ".." {
			return "", domain.ErrInvalid
		}
		if p != "" && p != "." {
			out = append(out, p)
		}
	}
	if len(out) == 0 {
		return "", domain.ErrInvalid
	}
	return strings.Join(out, "/"), nil
}

func (e *staticEntries) consume(name string, directory bool, size int64, reader io.Reader) error {
	name, err := staticName(name, directory)
	if err != nil {
		return err
	}
	e.count++
	if e.count > MaxStaticEntries {
		return domain.ErrInvalid
	}
	if _, exists := e.seen[name]; exists {
		return domain.ErrInvalid
	}
	parts := strings.Split(name, "/")
	for i := 1; i < len(parts); i++ {
		parent := strings.Join(parts[:i], "/")
		if dir, exists := e.seen[parent]; exists && !dir {
			return domain.ErrInvalid
		}
		e.parents[parent] = true
		e.nodes[parent] = true
	}
	if !directory && e.parents[name] {
		return domain.ErrInvalid
	}
	e.seen[name] = directory
	e.nodes[name] = true
	if len(e.nodes) > MaxStaticEntries {
		return domain.ErrInvalid
	}
	if directory {
		if size != 0 {
			return domain.ErrInvalid
		}
		return nil
	}
	if size < 0 || size > MaxStaticFile || e.total+size > MaxStaticExpanded {
		return domain.ErrInvalid
	}
	actual, err := io.Copy(io.Discard, io.LimitReader(reader, size+1))
	if err != nil || actual != size {
		return domain.ErrInvalid
	}
	e.files++
	e.total += actual
	return nil
}

func ValidateStaticFile(filename, expectedSHA string) (domain.Release, error) {
	out := domain.Release{DeploymentType: "static"}
	f, err := os.Open(filename)
	if err != nil {
		return out, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() <= 0 || st.Size() > MaxStaticPackage {
		return out, domain.ErrInvalid
	}
	out.Size = st.Size()
	digest := sha256.New()
	if _, err = io.Copy(digest, io.LimitReader(f, MaxStaticPackage+1)); err != nil {
		return out, err
	}
	out.SHA256 = hex.EncodeToString(digest.Sum(nil))
	if expectedSHA != "" && out.SHA256 != expectedSHA {
		return out, domain.ErrInvalid
	}
	f.Seek(0, io.SeekStart)
	magic := make([]byte, 4)
	if _, err = io.ReadFull(f, magic); err != nil {
		return out, domain.ErrInvalid
	}
	f.Seek(0, io.SeekStart)
	entries := staticEntries{seen: map[string]bool{}, parents: map[string]bool{}, nodes: map[string]bool{}}
	switch {
	case string(magic[:2]) == "PK":
		out.ArchiveFormat = "zip"
		archive, err := zip.NewReader(f, out.Size)
		if err != nil || len(archive.File) > MaxStaticEntries {
			return out, domain.ErrInvalid
		}
		for _, item := range archive.File {
			if item.Flags&0x800 == 0 && strings.IndexFunc(item.Name, func(r rune) bool { return r > 127 }) >= 0 {
				return out, domain.ErrInvalid
			}
			mode := item.Mode()
			directory := mode.IsDir()
			if item.Flags&1 != 0 || !(mode.IsRegular() || directory) || item.UncompressedSize64 > uint64(MaxStaticFile) {
				return out, domain.ErrInvalid
			}
			if directory {
				err = entries.consume(item.Name, true, int64(item.UncompressedSize64), nil)
			} else {
				reader, e := item.Open()
				if e != nil {
					return out, domain.ErrInvalid
				}
				err = entries.consume(item.Name, false, int64(item.UncompressedSize64), reader)
				reader.Close()
			}
			if err != nil {
				return out, err
			}
		}
	case magic[0] == 0x1f && magic[1] == 0x8b:
		out.ArchiveFormat = "tar.gz"
		gz, err := gzip.NewReader(f)
		if err != nil {
			return out, domain.ErrInvalid
		}
		defer gz.Close()
		bounded := &io.LimitedReader{R: gz, N: MaxStaticExpanded + MaxStaticEntries*8192 + 1024*1024 + 1}
		archive := tar.NewReader(bounded)
		for {
			item, e := archive.Next()
			if e == io.EOF {
				break
			}
			if e != nil {
				return out, domain.ErrInvalid
			}
			directory := item.Typeflag == tar.TypeDir
			if !directory && item.Typeflag != tar.TypeReg && item.Typeflag != tar.TypeRegA {
				return out, domain.ErrInvalid
			}
			for key := range item.PAXRecords {
				if strings.HasPrefix(key, "GNU.sparse") {
					return out, domain.ErrInvalid
				}
			}
			if entries.consume(item.Name, directory, item.Size, archive) != nil {
				return out, domain.ErrInvalid
			}
		}
		buf := make([]byte, 65536)
		for {
			n, e := bounded.Read(buf)
			for _, b := range buf[:n] {
				if b != 0 {
					return out, domain.ErrInvalid
				}
			}
			if e == io.EOF {
				break
			}
			if e != nil {
				return out, domain.ErrInvalid
			}
		}
		if bounded.N <= 0 {
			return out, domain.ErrInvalid
		}
	default:
		return out, domain.ErrInvalid
	}
	if entries.files == 0 {
		return out, domain.ErrInvalid
	}
	out.ExpandedSize = entries.total
	out.EntryCount = entries.count
	return out, nil
}

func ArtifactPath(root string, release domain.Release) (string, error) {
	if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(release.SHA256) {
		return "", domain.ErrInvalid
	}
	format := release.ArchiveFormat
	if format == "" {
		format = "tar.gz"
	}
	if format != "zip" && format != "tar.gz" {
		return "", domain.ErrInvalid
	}
	return filepath.Join(root, release.SHA256+"."+format), nil
}

func verifyArtifact(filename, checksum string) error {
	st, err := os.Lstat(filename)
	if err != nil || !st.Mode().IsRegular() {
		return domain.ErrConflict
	}
	f, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer f.Close()
	digest := sha256.New()
	if _, err = io.Copy(digest, io.LimitReader(f, MaxStaticPackage+1)); err != nil {
		return err
	}
	if hex.EncodeToString(digest.Sum(nil)) != checksum {
		return domain.ErrConflict
	}
	return nil
}

// PublishTempFile does not consume its input. Exclusive hard-link publication
// avoids replacing a concurrent artifact or an unexpected existing symlink.
func PublishTempFile(root, filename string, release domain.Release) error {
	target, err := ArtifactPath(root, release)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(root, 0700); err != nil {
		return err
	}
	if _, err = os.Lstat(target); err == nil {
		return verifyArtifact(target, release.SHA256)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	source, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer source.Close()
	temp, err := os.CreateTemp(root, ".static-")
	if err != nil {
		return err
	}
	name := temp.Name()
	defer os.Remove(name)
	defer temp.Close()
	digest := sha256.New()
	n, err := io.Copy(io.MultiWriter(temp, digest), io.LimitReader(source, MaxStaticPackage+1))
	if err != nil {
		return err
	}
	if n != release.Size || hex.EncodeToString(digest.Sum(nil)) != release.SHA256 {
		return domain.ErrInvalid
	}
	if err = temp.Sync(); err != nil {
		return err
	}
	if err = temp.Close(); err != nil {
		return err
	}
	if err = os.Link(name, target); err != nil {
		if errors.Is(err, os.ErrExist) {
			return verifyArtifact(target, release.SHA256)
		}
		return err
	}
	dir, err := os.Open(root)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
