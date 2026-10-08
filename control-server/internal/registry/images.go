package registry

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

type Image struct {
	Tag       string   `json:"tag"`
	Digest    string   `json:"digest"`
	MediaType string   `json:"media_type"`
	Versions  []string `json:"versions"`
}

func (v Verifier) ListImages(ctx context.Context, allowed string) ([]Image, error) {
	if domain.ValidateImage(allowed+"@sha256:"+strings.Repeat("0", 64)) != nil {
		return nil, domain.ErrInvalid
	}
	host, repository, ok := strings.Cut(allowed, "/")
	if !ok {
		return nil, domain.ErrInvalid
	}
	origin := "https://" + host
	internal := host == v.PublicHost
	if internal {
		origin = v.InternalURL
	}
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && !(internal && u.Scheme == "http")) {
		return nil, domain.ErrInvalid
	}
	bearer := ""
	if internal && v.Signer != nil {
		bearer, err = v.Signer.Sign(auth.Principal{ID: "control-server", Role: "owner"}, []auth.Access{{Type: "repository", Name: repository, Actions: []string{"pull"}}}, time.Now())
		if err != nil {
			return nil, err
		}
	}
	client := v.Client
	if client == nil {
		client = &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	request := func(method, path string) (*http.Response, error) {
		req, e := http.NewRequestWithContext(ctx, method, strings.TrimRight(origin, "/")+path, nil)
		if e != nil {
			return nil, e
		}
		req.Header.Set("Accept", "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json")
		if bearer != "" {
			req.Header.Set("Authorization", "Bearer "+bearer)
		}
		return client.Do(req)
	}
	res, err := request("GET", "/v2/"+repository+"/tags/list?n=100")
	if err != nil {
		return nil, domain.ErrInvalid
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, 1024*1024+1))
	res.Body.Close()
	if res.StatusCode == 404 {
		return []Image{}, nil
	}
	if err != nil || len(raw) > 1024*1024 || res.StatusCode != 200 {
		return nil, domain.ErrInvalid
	}
	var body struct {
		Name string   `json:"name"`
		Tags []string `json:"tags"`
	}
	if json.Unmarshal(raw, &body) != nil || body.Name != repository || len(body.Tags) > 100 {
		return nil, domain.ErrInvalid
	}
	result := make([]Image, len(body.Tags))
	tagPattern := regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$`)
	for _, tag := range body.Tags {
		if !tagPattern.MatchString(tag) {
			return nil, domain.ErrInvalid
		}
	}
	var group sync.WaitGroup
	var mu sync.Mutex
	failed := false
	slots := make(chan struct{}, 5)
	for i, tag := range body.Tags {
		group.Add(1)
		go func(i int, tag string) {
			defer group.Done()
			select {
			case slots <- struct{}{}:
			case <-ctx.Done():
				mu.Lock()
				failed = true
				mu.Unlock()
				return
			}
			defer func() { <-slots }()
			r, e := request("HEAD", "/v2/"+repository+"/manifests/"+tag)
			if e != nil {
				mu.Lock()
				failed = true
				mu.Unlock()
				return
			}
			r.Body.Close()
			digest := r.Header.Get("Docker-Content-Digest")
			if r.StatusCode != 200 || domain.ValidateImage(allowed+"@"+digest) != nil {
				mu.Lock()
				failed = true
				mu.Unlock()
				return
			}
			result[i] = Image{Tag: tag, Digest: digest, MediaType: r.Header.Get("Content-Type"), Versions: []string{}}
		}(i, tag)
	}
	group.Wait()
	if failed {
		return nil, domain.ErrInvalid
	}
	return result, nil
}
