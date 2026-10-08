package registry

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"

	"github.com/art-shier/deployctl/control-server/internal/domain"
)

type Image struct {
	Tag       string   `json:"tag"`
	Digest    string   `json:"digest"`
	MediaType string   `json:"media_type"`
	Versions  []string `json:"versions"`
}

func (v Verifier) ListImages(ctx context.Context, allowed string) ([]Image, error) {
	return v.ListImagesWithToken(ctx, allowed, "")
}
func (v Verifier) ListImagesWithToken(ctx context.Context, allowed, proof string) ([]Image, error) {
	if domain.ValidateImage(allowed+"@sha256:"+strings.Repeat("0", 64)) != nil {
		return nil, domain.ErrInvalid
	}
	s, err := v.newSession(allowed, proof)
	if err != nil {
		return nil, err
	}
	repository := s.repository
	request := func(method, path string) (*http.Response, error) {
		return s.request(ctx, method, path)
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
