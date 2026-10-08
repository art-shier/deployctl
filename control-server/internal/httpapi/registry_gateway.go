package httpapi

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
)

type registryOrigin interface{ RegistryOrigin() string }

var repositoryPattern = regexp.MustCompile(`^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$`)

// Public OCI traffic uses the API gateway. Only manifest mutations take the
// repository lock; blob transfers stay streaming and Registry enforces auth.
func (s *Server) registryGateway(w http.ResponseWriter, r *http.Request) {
	provider, ok := s.options.Verifier.(registryOrigin)
	if !ok {
		failure(w, domain.ErrNotFound)
		return
	}
	origin, err := url.Parse(provider.RegistryOrigin())
	if err != nil || origin.Host == "" || origin.User != nil || origin.RawQuery != "" || origin.Fragment != "" || (origin.Path != "" && origin.Path != "/") || (origin.Scheme != "http" && origin.Scheme != "https") {
		failure(w, domain.ErrInvalid)
		return
	}
	if r.Method == "DELETE" {
		reply(w, 405, map[string]string{"code": "managed_delete_required", "message": "请通过管理台的受保护删除接口操作"})
		return
	}
	proxy := &httputil.ReverseProxy{
		Rewrite: func(p *httputil.ProxyRequest) {
			p.SetURL(origin)
			p.Out.URL.RawPath = ""
			p.Out.Header.Del("Cookie")
			p.Out.Header.Del("X-Registry-Verification-Token")
			p.SetXForwarded()
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			reply(w, 502, map[string]string{"code": "registry_unavailable", "message": "镜像仓库暂时不可用"})
		},
		ModifyResponse: func(response *http.Response) error {
			if value := response.Header.Get("Location"); value != "" {
				location, err := url.Parse(value)
				if err != nil || location.User != nil {
					return domain.ErrInvalid
				}
				if location.IsAbs() {
					if location.Host != origin.Host && location.Host != s.options.RegistryPublicHost {
						return domain.ErrInvalid
					}
					response.Header.Set("Location", location.RequestURI())
				}
			}
			return nil
		},
	}
	marker := strings.LastIndex(r.URL.Path, "/manifests/")
	if r.Method != "PUT" || marker < 0 {
		proxy.ServeHTTP(w, r)
		return
	}
	repository := strings.TrimPrefix(r.URL.Path[:marker], "/v2/")
	reference := r.URL.Path[marker+len("/manifests/"):]
	if !repositoryPattern.MatchString(repository) || (!registry.ValidTag(reference) && domain.ValidateImage("registry.test/"+repository+"@"+reference) != nil) {
		failure(w, domain.ErrInvalid)
		return
	}
	kind, token, found := strings.Cut(r.Header.Get("Authorization"), " ")
	// Never forward a manifest mutation that this gateway cannot authorize:
	// otherwise a Registry parser accepting a token we reject could bypass locking.
	if !found || !strings.EqualFold(kind, "Bearer") || s.options.Signer == nil || !s.options.Signer.Allows(token, repository, "push", time.Now()) {
		service := "ctl-registry"
		if s.options.Signer != nil {
			service = s.options.Signer.Service
		}
		w.Header().Set("WWW-Authenticate", "Bearer realm="+strconv.Quote(s.options.PublicOrigin+"/registry/token")+",service="+strconv.Quote(service)+",scope="+strconv.Quote("repository:"+repository+":pull,push"))
		failure(w, domain.ErrUnauthorized)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1024*1024)
	body, err := io.ReadAll(r.Body)
	r.Body.Close()
	if err != nil {
		failure(w, domain.ErrInvalid)
		return
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	forwarded := false
	err = s.store.WithRepositoryLock(r.Context(), s.options.RegistryPublicHost+"/"+repository, func() error { forwarded = true; proxy.ServeHTTP(w, r); return nil })
	// If the proxy has already replied, a lock-release/commit error cannot safely
	// append an API response. The transaction has no database writes.
	if err != nil && !forwarded {
		failure(w, err)
	}
}
