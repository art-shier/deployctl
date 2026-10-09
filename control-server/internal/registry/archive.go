package registry

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path"
	"strings"

	v1 "github.com/google/go-containerregistry/pkg/v1"
	"github.com/google/go-containerregistry/pkg/v1/tarball"
	"github.com/klauspost/compress/zstd"
)

const MaxImageArchive int64 = 2 * 1024 * 1024 * 1024
const MaxExpandedArchive int64 = 8 * 1024 * 1024 * 1024

var ErrArchive = errors.New("invalid single image Docker archive")
var ErrExternal = errors.New("operation requires managed registry")
var ErrTagExists = errors.New("image tag already exists")
var ErrMixedLayers = errors.New("unsupported mixed layer encoding")

// Inspect the outer archive without extracting any member. The SDK reads layers
// from this same private file; configuration and manifest allocations are bounded.
func ReadDockerArchive(file string) (v1.Image, error) {
	return readDockerArchive(context.Background(), file)
}

type contextReader struct {
	context context.Context
	io.Reader
}

func (r contextReader) Read(buffer []byte) (int, error) {
	if err := r.context.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(buffer)
}

type contextFile struct {
	contextReader
	io.Closer
}

func readDockerArchive(ctx context.Context, file string) (v1.Image, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, ErrArchive
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > MaxImageArchive {
		return nil, ErrArchive
	}
	reader := tar.NewReader(contextReader{ctx, f})
	files := map[string]int64{}
	hashes := map[string]string{}
	compressed := map[string]int{}
	var manifest []struct {
		Config       string
		Layers       []string
		LayerSources map[string]descriptor
	}
	for count := 0; ; count++ {
		if count > 10000 {
			return nil, ErrArchive
		}
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, ErrArchive
		}
		name := strings.TrimSuffix(header.Name, "/")
		if name == "" || strings.HasPrefix(name, "/") || strings.Contains(name, "\\") || path.Clean(name) != name || name == ".." || strings.HasPrefix(name, "../") || len(name) > 512 {
			return nil, ErrArchive
		}
		if header.Typeflag == tar.TypeDir {
			continue
		}
		if header.Typeflag != tar.TypeReg || header.Size < 0 || header.Size > MaxImageArchive {
			return nil, ErrArchive
		}
		if _, exists := files[name]; exists {
			return nil, ErrArchive
		}
		files[name] = header.Size
		if name == "manifest.json" {
			if header.Size > 65536 {
				return nil, ErrArchive
			}
			raw, err := io.ReadAll(reader)
			if err != nil || json.Unmarshal(raw, &manifest) != nil {
				return nil, ErrArchive
			}
		} else {
			hash := sha256.New()
			var prefix [4]byte
			n, err := io.ReadFull(reader, prefix[:])
			if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
				return nil, ErrArchive
			}
			if n >= 2 && prefix[0] == 0x1f && prefix[1] == 0x8b {
				compressed[name] = 1
			}
			if n == 4 && prefix == [4]byte{0x28, 0xb5, 0x2f, 0xfd} {
				compressed[name] = 2
			}
			hash.Write(prefix[:n])
			if _, err = io.Copy(hash, reader); err != nil {
				return nil, ErrArchive
			}
			hashes[name] = "sha256:" + hex.EncodeToString(hash.Sum(nil))
		}
	}
	if len(manifest) != 1 || len(manifest[0].Layers) > 256 || len(manifest[0].LayerSources) > 256 {
		return nil, ErrArchive
	}
	entry := manifest[0]
	size, ok := files[entry.Config]
	if !ok || size > 1024*1024 || entry.Config == "manifest.json" {
		return nil, ErrArchive
	}
	seen := map[string]bool{}
	mode := -1
	for _, layer := range entry.Layers {
		if _, ok := files[layer]; !ok || layer == entry.Config || seen[layer] {
			return nil, ErrArchive
		}
		seen[layer] = true
		current := 0
		if compressed[layer] != 0 {
			current = 1
		}
		if mode >= 0 && current != mode {
			return nil, ErrMixedLayers
		}
		mode = current
	}
	image, err := tarball.Image(func() (io.ReadCloser, error) {
		input, err := os.Open(file)
		if err != nil {
			return nil, err
		}
		return contextFile{contextReader{ctx, input}, input}, nil
	}, nil)
	if err != nil {
		return nil, ErrArchive
	}
	config, err := image.ConfigFile()
	if err != nil || config.OS == "" || config.Architecture == "" || config.RootFS.Type != "layers" || len(config.RootFS.DiffIDs) != len(entry.Layers) {
		return nil, ErrArchive
	}
	// Modern Docker save may include compressed OCI blobs. Bound their expanded
	// content and verify actual DiffIDs without extracting files or buffering layers.
	for diffID, source := range entry.LayerSources {
		index := -1
		for i, hash := range config.RootFS.DiffIDs {
			if hash.String() == diffID {
				index = i
				break
			}
		}
		if index < 0 || len(source.URLs) != 0 || source.Digest != hashes[entry.Layers[index]] || source.Size != files[entry.Layers[index]] {
			return nil, ErrArchive
		}
		switch source.MediaType {
		case "application/vnd.docker.image.rootfs.diff.tar", "application/vnd.oci.image.layer.v1.tar", "application/vnd.docker.image.rootfs.diff.tar.gzip", "application/vnd.oci.image.layer.v1.tar+gzip", "application/vnd.oci.image.layer.v1.tar+zstd":
		default:
			return nil, ErrArchive
		}
		kind := compressed[entry.Layers[index]]
		if (kind == 0 && source.MediaType != "application/vnd.docker.image.rootfs.diff.tar" && source.MediaType != "application/vnd.oci.image.layer.v1.tar") || (kind == 1 && source.MediaType != "application/vnd.docker.image.rootfs.diff.tar.gzip" && source.MediaType != "application/vnd.oci.image.layer.v1.tar+gzip") || (kind == 2 && source.MediaType != "application/vnd.oci.image.layer.v1.tar+zstd") {
			return nil, ErrMixedLayers
		}
	}
	remaining := MaxExpandedArchive
	for i, layer := range entry.Layers {
		if compressed[layer] == 2 {
			source, ok := entry.LayerSources[config.RootFS.DiffIDs[i].String()]
			if !ok || source.MediaType != "application/vnd.oci.image.layer.v1.tar+zstd" {
				return nil, ErrMixedLayers
			}
		}
		if compressed[layer] != 0 {
			input, err := archiveMember(ctx, file, layer)
			if err != nil {
				return nil, ErrArchive
			}
			var reader io.Reader
			var closeDecoder func()
			if compressed[layer] == 1 {
				decoder, e := gzip.NewReader(input)
				if e != nil {
					input.Close()
					return nil, ErrArchive
				}
				reader = decoder
				closeDecoder = func() { decoder.Close() }
			} else {
				decoder, e := zstd.NewReader(input, zstd.WithDecoderMaxMemory(64*1024*1024), zstd.WithDecoderMaxWindow(16*1024*1024), zstd.WithDecoderConcurrency(1))
				if e != nil {
					input.Close()
					return nil, ErrArchive
				}
				reader = decoder
				closeDecoder = decoder.Close
			}
			hash := sha256.New()
			size, err := io.Copy(hash, io.LimitReader(contextReader{ctx, reader}, remaining+1))
			closeDecoder()
			input.Close()
			if err != nil || size > remaining {
				return nil, ErrArchive
			}
			remaining -= size
			if "sha256:"+hex.EncodeToString(hash.Sum(nil)) != config.RootFS.DiffIDs[i].String() {
				return nil, ErrArchive
			}
		} else if files[layer] > remaining || hashes[layer] != config.RootFS.DiffIDs[i].String() {
			return nil, ErrArchive
		} else {
			remaining -= files[layer]
		}
	}
	return image, nil
}

func archiveMember(ctx context.Context, file, member string) (io.ReadCloser, error) {
	input, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	reader := tar.NewReader(contextReader{ctx, input})
	for {
		header, e := reader.Next()
		if e != nil {
			input.Close()
			return nil, ErrArchive
		}
		if header.Name == member {
			return contextFile{contextReader{ctx, reader}, input}, nil
		}
	}
}
