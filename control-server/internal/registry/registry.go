package registry

import (
	"context"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

type Verifier struct {
	InternalURL string
	PublicHost  string
	Signer      *auth.RegistrySigner
	Client      *http.Client
}

func (v Verifier) CheckManifest(ctx context.Context, image, allowedRepository string) error {
	if domain.ValidateImage(image) != nil {
		return domain.ErrInvalid
	}
	pieces := strings.Split(image, "@")
	if pieces[0] != allowedRepository {
		return domain.ErrInvalid
	}
	host, repository, ok := strings.Cut(pieces[0], "/")
	if !ok {
		return domain.ErrInvalid
	}
	origin := "https://" + host
	internal := host == v.PublicHost
	if internal {
		origin = v.InternalURL
	}
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return domain.ErrInvalid
	}
	if parsed.Scheme != "https" && !(internal && parsed.Scheme == "http") {
		return domain.ErrInvalid
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, strings.TrimRight(origin, "/")+"/v2/"+repository+"/manifests/"+pieces[1], nil)
	if err != nil {
		return domain.ErrInvalid
	}
	req.Header.Set("Accept", "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json")
	if internal && v.Signer != nil {
		token, err := v.Signer.Sign(auth.Principal{ID: "control-server", Role: "owner"}, []auth.Access{{Type: "repository", Name: repository, Actions: []string{"pull"}}}, time.Now())
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}
	client := v.Client
	if client == nil {
		client = &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	res, err := client.Do(req)
	if err != nil {
		return domain.ErrInvalid
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK || res.Header.Get("Docker-Content-Digest") != pieces[1] {
		return domain.ErrInvalid
	}
	return nil
}
