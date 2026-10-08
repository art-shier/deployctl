package registry

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/v1/remote"
)

var tagPattern = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$`)

func ValidTag(tag string) bool { return tagPattern.MatchString(tag) }
func (v Verifier) managedSession(allowed string, actions ...string) (*session, error) {
	host, _, _ := strings.Cut(allowed, "/")
	if host != v.PublicHost || v.Signer == nil {
		return nil, ErrExternal
	}
	s, err := v.newSession(allowed, "")
	if err != nil {
		return nil, err
	}
	s.bearer, err = v.Signer.Sign(auth.Principal{ID: "control-server", Role: "owner"}, []auth.Access{{Type: "repository", Name: s.repository, Actions: actions}}, time.Now())
	return s, err
}

type manifest struct {
	SchemaVersion int          `json:"schemaVersion"`
	MediaType     string       `json:"mediaType"`
	Config        descriptor   `json:"config"`
	Layers        []descriptor `json:"layers"`
	Manifests     []descriptor `json:"manifests"`
}
type descriptor struct {
	MediaType string   `json:"mediaType"`
	Digest    string   `json:"digest"`
	Size      int64    `json:"size"`
	URLs      []string `json:"urls"`
}
type ManifestDetails struct {
	Digest    string   `json:"digest"`
	MediaType string   `json:"media_type"`
	SizeBytes int64    `json:"size_bytes"`
	Platforms []string `json:"platforms"`
}
type manifestGraph struct {
	session *session
	allowed string
	nodes   map[string]manifest
	bytes   map[string]int64
}

func (g *manifestGraph) read(ctx context.Context, digest string, depth int) error {
	if _, ok := g.nodes[digest]; ok {
		return nil
	}
	if depth > 8 || len(g.nodes) >= 4096 || len(g.bytes) >= 16384 || domain.ValidateImage(g.allowed+"@"+digest) != nil {
		return domain.ErrInvalid
	}
	res, err := g.session.request(ctx, "GET", "/v2/"+g.session.repository+"/manifests/"+digest)
	if err != nil {
		return err
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, 1024*1024+1))
	res.Body.Close()
	if res.StatusCode == 404 {
		return domain.ErrNotFound
	}
	hash := sha256.Sum256(raw)
	if err != nil || res.StatusCode != 200 || len(raw) > 1024*1024 || "sha256:"+hex.EncodeToString(hash[:]) != digest {
		return domain.ErrInvalid
	}
	var m manifest
	if json.Unmarshal(raw, &m) != nil || m.SchemaVersion != 2 || len(m.Layers) > 512 || len(m.Manifests) > 128 {
		return domain.ErrInvalid
	}
	if m.MediaType == "" {
		m.MediaType = strings.Split(res.Header.Get("Content-Type"), ";")[0]
	}
	switch m.MediaType {
	case "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json":
		if len(m.Manifests) == 0 {
			return domain.ErrInvalid
		}
	case "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json":
		if domain.ValidateImage(g.allowed+"@"+m.Config.Digest) != nil {
			return domain.ErrInvalid
		}
	default:
		return domain.ErrInvalid
	}
	g.nodes[digest] = m
	g.bytes[digest] = int64(len(raw))
	for _, d := range append(append([]descriptor{}, m.Layers...), m.Config) {
		if d.Digest == "" {
			continue
		}
		if len(g.bytes) >= 16384 || domain.ValidateImage(g.allowed+"@"+d.Digest) != nil || d.Size < 0 || d.Size > 1<<40 || len(d.URLs) != 0 {
			return domain.ErrInvalid
		}
		if size, ok := g.bytes[d.Digest]; ok && size != d.Size {
			return domain.ErrInvalid
		}
		g.bytes[d.Digest] = d.Size
	}
	for _, d := range m.Manifests {
		if len(d.URLs) != 0 {
			return domain.ErrInvalid
		}
		if err = g.read(ctx, d.Digest, depth+1); err != nil {
			return err
		}
	}
	return nil
}
func (v Verifier) InspectManifest(ctx context.Context, allowed, digest, proof string) (ManifestDetails, error) {
	out := ManifestDetails{Digest: digest, Platforms: []string{}}
	s, err := v.newSession(allowed, proof)
	if err != nil {
		return out, err
	}
	g := manifestGraph{s, allowed, map[string]manifest{}, map[string]int64{}}
	if err = g.read(ctx, digest, 0); err != nil {
		return out, err
	}
	out.MediaType = g.nodes[digest].MediaType
	for _, size := range g.bytes {
		out.SizeBytes += size
	}
	for _, m := range g.nodes {
		if m.Config.Digest == "" {
			continue
		}
		res, e := s.request(ctx, "GET", "/v2/"+s.repository+"/blobs/"+m.Config.Digest)
		if e != nil {
			return out, e
		}
		raw, e := io.ReadAll(io.LimitReader(res.Body, 1024*1024+1))
		res.Body.Close()
		sum := sha256.Sum256(raw)
		if e != nil || res.StatusCode != 200 || len(raw) > 1024*1024 || "sha256:"+hex.EncodeToString(sum[:]) != m.Config.Digest {
			return out, domain.ErrInvalid
		}
		var config struct {
			OS           string `json:"os"`
			Architecture string `json:"architecture"`
			Variant      string `json:"variant"`
		}
		if json.Unmarshal(raw, &config) != nil || config.OS == "" || config.Architecture == "" || len(config.OS)+len(config.Architecture)+len(config.Variant) > 128 {
			return out, domain.ErrInvalid
		}
		platform := config.OS + "/" + config.Architecture
		if config.Variant != "" {
			platform += "/" + config.Variant
		}
		if !slices.Contains(out.Platforms, platform) {
			out.Platforms = append(out.Platforms, platform)
		}
	}
	slices.Sort(out.Platforms)
	return out, nil
}
func (v Verifier) DeleteManifest(ctx context.Context, allowed, digest string, protected []string) error {
	s, err := v.managedSession(allowed, "pull", "delete")
	if err != nil {
		return err
	}
	if domain.ValidateImage(allowed+"@"+digest) != nil {
		return domain.ErrInvalid
	}
	for _, root := range protected {
		if root == allowed+"@"+digest {
			return ErrReferenced
		}
	}
	images, err := v.ListImages(ctx, allowed)
	if err != nil {
		return err
	}
	// Protect children of tagged indexes, including images not yet registered as releases.
	for _, image := range images {
		if image.Digest != digest && (strings.Contains(image.MediaType, "index") || strings.Contains(image.MediaType, "manifest.list")) {
			protected = append(protected, allowed+"@"+image.Digest)
		}
	}
	g := manifestGraph{s, allowed, map[string]manifest{}, map[string]int64{}}
	for _, root := range protected {
		repo, hash, ok := strings.Cut(root, "@")
		if !ok || repo != allowed {
			return domain.ErrInvalid
		}
		if err = g.read(ctx, hash, 0); err != nil {
			return err
		}
		if _, ok := g.nodes[digest]; ok {
			return ErrReferenced
		}
	}
	res, err := s.request(ctx, "DELETE", "/v2/"+s.repository+"/manifests/"+digest)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode == 404 {
		return domain.ErrNotFound
	}
	if res.StatusCode != 202 {
		return domain.ErrInvalid
	}
	return nil
}

// remote's registry client only receives this confined transport. Every request
// (including a redirect/challenge) must stay on the configured internal repository.
type pushTransport struct {
	session *session
	signer  *auth.RegistrySigner
}

func (p pushTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	origin, err := url.Parse(p.session.origin)
	if err != nil {
		return nil, domain.ErrInvalid
	}
	if request.URL.Host != origin.Host || (request.URL.Path != "/v2/" && !strings.HasPrefix(request.URL.Path, "/v2/"+p.session.repository+"/")) {
		return nil, domain.ErrInvalid
	}
	copy := request.Clone(request.Context())
	u := *request.URL
	u.Scheme = origin.Scheme
	copy.URL = &u
	token, err := p.signer.Sign(auth.Principal{ID: "control-server", Role: "owner"}, []auth.Access{{Type: "repository", Name: p.session.repository, Actions: []string{"pull", "push"}}}, time.Now())
	if err != nil {
		return nil, err
	}
	copy.Header.Set("Authorization", "Bearer "+token)
	rt := p.session.client.Transport
	if rt == nil {
		rt = http.DefaultTransport
	}
	return rt.RoundTrip(copy)
}
func (v Verifier) ImportDockerArchive(ctx context.Context, allowed, tag, file string) (Image, error) {
	out := Image{Tag: tag, Versions: []string{}}
	if !ValidTag(tag) {
		return out, domain.ErrInvalid
	}
	s, err := v.managedSession(allowed, "pull", "push")
	if err != nil {
		return out, err
	}
	image, err := readDockerArchive(ctx, file)
	if err != nil {
		return out, err
	}
	hash, err := image.Digest()
	if err != nil {
		return out, ErrArchive
	}
	out.Digest = hash.String()
	media, err := image.MediaType()
	if err != nil {
		return out, ErrArchive
	}
	out.MediaType = string(media)
	existing, err := s.request(ctx, "HEAD", "/v2/"+s.repository+"/manifests/"+tag)
	if err != nil {
		return out, err
	}
	existing.Body.Close()
	if existing.StatusCode == 200 {
		if existing.Header.Get("Docker-Content-Digest") == out.Digest {
			return out, nil
		}
		return out, ErrTagExists
	}
	if existing.StatusCode != 404 {
		return out, domain.ErrInvalid
	}
	origin, _ := url.Parse(s.origin)
	options := []name.Option{name.StrictValidation}
	if origin.Scheme == "http" {
		options = append(options, name.Insecure)
	}
	target, err := name.NewTag(origin.Host+"/"+s.repository+":"+tag, options...)
	if err != nil {
		return out, domain.ErrInvalid
	}
	err = remote.Write(target, image, remote.WithContext(ctx), remote.WithJobs(1), remote.WithTransport(pushTransport{s, v.Signer}), remote.WithRetryPredicate(func(error) bool { return false }))
	if err != nil {
		return out, domain.ErrInvalid
	}
	if err = v.CheckManifest(ctx, allowed+"@"+out.Digest, allowed); err != nil {
		return out, err
	}
	return out, nil
}
