package registry

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
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
	return v.CheckManifestWithToken(ctx, image, allowedRepository, "")
}
func (v Verifier) CheckManifestWithToken(ctx context.Context, image, allowedRepository, proof string) error {
	if domain.ValidateImage(image) != nil {
		return domain.ErrInvalid
	}
	pieces := strings.Split(image, "@")
	if pieces[0] != allowedRepository {
		return domain.ErrInvalid
	}
	s, err := v.newSession(allowedRepository, proof)
	if err != nil {
		return err
	}
	res, err := s.request(ctx, http.MethodHead, "/v2/"+s.repository+"/manifests/"+pieces[1])
	if err != nil {
		return domain.ErrInvalid
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK || res.Header.Get("Docker-Content-Digest") != pieces[1] {
		return domain.ErrInvalid
	}
	return nil
}

const mediaTypes = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"

var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9._~+/=-]+$`)

func ValidateVerificationToken(token string) error {
	if len(token) > 12288 || strings.HasPrefix(token, "ctl_") || (token != "" && !tokenPattern.MatchString(token)) {
		return domain.ErrInvalid
	}
	return nil
}

type session struct {
	origin, repository, bearer string
	internal, proof            bool
	client                     *http.Client
	mu                         sync.Mutex
}

func (v Verifier) newSession(allowed, proof string) (*session, error) {
	if domain.ValidateImage(allowed+"@sha256:"+strings.Repeat("0", 64)) != nil || ValidateVerificationToken(proof) != nil {
		return nil, domain.ErrInvalid
	}
	host, repository, ok := strings.Cut(allowed, "/")
	if !ok {
		return nil, domain.ErrInvalid
	}
	s := &session{origin: "https://" + host, repository: repository, internal: host == v.PublicHost, proof: proof != "", bearer: proof}
	if s.internal {
		s.origin = v.InternalURL
		if proof != "" {
			return nil, domain.ErrInvalid
		}
	}
	u, err := url.Parse(s.origin)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || (u.Scheme != "https" && !(s.internal && u.Scheme == "http")) {
		return nil, domain.ErrInvalid
	}
	if s.internal && v.Signer != nil {
		s.bearer, err = v.Signer.Sign(auth.Principal{ID: "control-server", Role: "owner"}, []auth.Access{{Type: "repository", Name: repository, Actions: []string{"pull"}}}, time.Now())
		if err != nil {
			return nil, err
		}
	}
	client := v.Client
	if client == nil {
		client = &http.Client{Timeout: 20 * time.Second}
	}
	copyClient := *client
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	s.client = &copyClient
	return s, nil
}
func (s *session) request(ctx context.Context, method, path string) (*http.Response, error) {
	do := func(token string) (*http.Response, error) {
		req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(s.origin, "/")+path, nil)
		if err != nil {
			return nil, domain.ErrInvalid
		}
		req.Header.Set("Accept", mediaTypes)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		return s.client.Do(req)
	}
	s.mu.Lock()
	token := s.bearer
	s.mu.Unlock()
	res, err := do(token)
	if err != nil {
		return nil, domain.ErrInvalid
	}
	if res.StatusCode != 401 || s.internal || s.proof {
		return res, nil
	}
	challenge := res.Header.Get("WWW-Authenticate")
	io.Copy(io.Discard, io.LimitReader(res.Body, 4096))
	res.Body.Close()
	token, err = s.anonymousToken(ctx, challenge)
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.bearer = token
	s.mu.Unlock()
	return do(token)
}
func (s *session) anonymousToken(ctx context.Context, challenge string) (string, error) {
	scheme, parameters, ok := strings.Cut(strings.TrimSpace(challenge), " ")
	if !ok || !strings.EqualFold(scheme, "Bearer") {
		return "", domain.ErrInvalid
	}
	var out strings.Builder
	quoted, escaped := false, false
	for _, c := range parameters {
		if c == ',' && !quoted {
			out.WriteRune(';')
			continue
		}
		out.WriteRune(c)
		if escaped {
			escaped = false
			continue
		}
		if c == '\\' && quoted {
			escaped = true
		} else if c == '"' {
			quoted = !quoted
		}
	}
	_, values, err := mime.ParseMediaType("bearer;" + out.String())
	if err != nil {
		return "", domain.ErrInvalid
	}
	realm, err := url.Parse(values["realm"])
	if err != nil || realm.Scheme != "https" || realm.Host == "" || realm.User != nil || realm.Fragment != "" {
		return "", domain.ErrInvalid
	}
	service := values["service"]
	if len(service) > 256 || strings.IndexFunc(service, func(r rune) bool { return r < 32 || r == 127 }) >= 0 {
		return "", domain.ErrInvalid
	}
	query := realm.Query()
	query.Set("service", service)
	query.Set("scope", "repository:"+s.repository+":pull")
	realm.RawQuery = query.Encode()
	req, err := http.NewRequestWithContext(ctx, "GET", realm.String(), nil)
	if err != nil {
		return "", domain.ErrInvalid
	}
	// Anonymous exchange never carries platform credentials or external verification proof.
	req.Header.Set("Accept", "application/json")
	res, err := s.client.Do(req)
	if err != nil {
		return "", domain.ErrInvalid
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(res.Body, 32769))
	if err != nil || len(raw) > 32768 || res.StatusCode != 200 {
		return "", domain.ErrInvalid
	}
	var body struct {
		Token       string `json:"token"`
		AccessToken string `json:"access_token"`
	}
	if json.Unmarshal(raw, &body) != nil {
		return "", domain.ErrInvalid
	}
	token := body.Token
	if token == "" {
		token = body.AccessToken
	}
	if token == "" || ValidateVerificationToken(token) != nil {
		return "", domain.ErrInvalid
	}
	return token, nil
}
