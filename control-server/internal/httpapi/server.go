package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/registry"
	"github.com/art-shier/deployctl/control-server/internal/store"
)

type ManifestVerifier interface {
	CheckManifest(context.Context, string, string) error
}
type Options struct {
	OwnerHash          string
	PublicOrigin       string
	CookieSecure       bool
	RegistryPublicHost string
	ArtifactsDir       string
	WebDir             string
	Verifier           ManifestVerifier
	Signer             *auth.RegistrySigner
}
type Server struct {
	store       *store.Store
	options     Options
	mux         *http.ServeMux
	imageUpload chan struct{}
}
type handler func(http.ResponseWriter, *http.Request, auth.Principal) error

func New(s *store.Store, o Options) *Server {
	server := &Server{store: s, options: o, mux: http.NewServeMux(), imageUpload: make(chan struct{}, 1)}
	m := server.mux
	m.HandleFunc("GET /api/v1/health/ready", func(w http.ResponseWriter, r *http.Request) {
		if err := s.Ping(r.Context()); err != nil {
			failure(w, err)
			return
		}
		reply(w, 200, map[string]string{"status": "ready"})
	})
	m.HandleFunc("POST /api/v1/session", server.login)
	m.HandleFunc("GET /registry/token", server.registryToken)
	m.HandleFunc("GET /api/v1/me", server.wrap(func(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
		reply(w, 200, map[string]any{"id": p.ID, "role": p.Role, "project": p.Project, "projects": p.Projects, "groups": p.Groups, "environments": p.Environments, "registry_host": o.RegistryPublicHost, "schema_version": 1, "minimum_client_version": "1.7.0"})
		return nil
	}))
	m.HandleFunc("DELETE /api/v1/session", server.wrap(server.logout))
	m.HandleFunc("GET /api/v1/groups", server.wrap(server.groups))
	m.HandleFunc("POST /api/v1/groups", server.wrap(server.createGroup))
	m.HandleFunc("PATCH /api/v1/groups/{slug}", server.wrap(server.updateGroup))
	m.HandleFunc("GET /api/v1/projects", server.wrap(server.projects))
	m.HandleFunc("POST /api/v1/projects", server.wrap(server.createProject))
	m.HandleFunc("GET /api/v1/projects/{slug}", server.wrap(server.getProject))
	m.HandleFunc("PATCH /api/v1/projects/{slug}", server.wrap(server.updateProject))
	m.HandleFunc("PATCH /api/v1/projects/{slug}/group", server.wrap(server.moveProjectGroup))
	m.HandleFunc("GET /api/v1/projects/{slug}/environments", server.wrap(server.environments))
	m.HandleFunc("GET /api/v1/projects/{slug}/environments/{env}", server.wrap(server.environment))
	m.HandleFunc("PUT /api/v1/projects/{slug}/environments/{env}", server.wrap(server.saveEnvironment))
	m.HandleFunc("GET /api/v1/projects/{slug}/releases", server.wrap(server.releases))
	m.HandleFunc("GET /api/v1/projects/{slug}/releases/{version}", server.wrap(server.getRelease))
	m.HandleFunc("GET /api/v1/projects/{slug}/images", server.wrap(server.images))
	m.HandleFunc("GET /api/v1/projects/{slug}/images/capabilities", server.wrap(server.imageCapabilities))
	m.HandleFunc("GET /api/v1/projects/{slug}/images/{digest}", server.wrap(server.imageDetails))
	m.HandleFunc("DELETE /api/v1/projects/{slug}/images/{digest}", server.wrap(server.deleteImage))
	m.HandleFunc("POST /api/v1/projects/{slug}/images/upload", server.wrap(server.uploadImage))
	m.HandleFunc("POST /api/v1/projects/{slug}/releases", server.wrap(server.publish))
	m.HandleFunc("POST /api/v1/projects/{slug}/releases/{version}/retire", server.wrap(server.retire))
	m.HandleFunc("POST /api/v1/projects/{slug}/resolve", server.wrap(server.resolve))
	m.HandleFunc("GET /api/v1/projects/{slug}/artifacts/{id}", server.wrap(server.artifact))
	m.HandleFunc("GET /api/v1/projects/{slug}/receipts", server.wrap(server.receipts))
	m.HandleFunc("POST /api/v1/projects/{slug}/receipts", server.wrap(server.saveReceipt))
	m.HandleFunc("GET /api/v1/tokens", server.wrap(server.tokens))
	m.HandleFunc("POST /api/v1/tokens", server.wrap(server.createToken))
	m.HandleFunc("DELETE /api/v1/tokens/{id}", server.wrap(server.revokeToken))
	m.HandleFunc("GET /api/v1/audit", server.wrap(server.audit))
	m.HandleFunc("/", server.static)
	return server
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
	if strings.HasPrefix(r.URL.Path, "/api/") || r.URL.Path == "/registry/token" {
		w.Header().Set("Cache-Control", "no-store")
	}
	timeout := 60 * time.Second
	if r.Method == "POST" && strings.HasSuffix(r.URL.Path, "/images/upload") {
		timeout = 15 * time.Minute
	}
	native := strings.HasPrefix(r.URL.Path, "/v2/") || r.URL.Path == "/v2"
	if native && !strings.Contains(r.URL.Path, "/manifests/") {
		timeout = 60 * time.Minute
	}
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	if native {
		s.registryGateway(w, r.WithContext(ctx))
		return
	}
	s.mux.ServeHTTP(w, r.WithContext(ctx))
}
func (s *Server) principal(r *http.Request) (auth.Principal, bool, error) {
	raw := r.Header.Get("Authorization")
	if raw != "" {
		value, ok := strings.CutPrefix(raw, "Bearer ")
		if !ok || value == "" {
			return auth.Principal{}, false, domain.ErrUnauthorized
		}
		p, err := s.tokenPrincipal(r.Context(), value)
		return p, false, err
	}
	cookie, err := r.Cookie("ctl_session")
	if err != nil || !s.store.SessionValid(r.Context(), auth.HashToken(cookie.Value)) {
		return auth.Principal{}, true, domain.ErrUnauthorized
	}
	return auth.Principal{ID: "owner", Role: "owner"}, true, nil
}
func (s *Server) tokenPrincipal(ctx context.Context, token string) (auth.Principal, error) {
	hash := auth.HashToken(token)
	if len(s.options.OwnerHash) == 64 && subtle.ConstantTimeCompare([]byte(hash), []byte(s.options.OwnerHash)) == 1 {
		return auth.Principal{ID: "owner", Role: "owner"}, nil
	}
	return s.store.Authenticate(ctx, hash)
}
func (s *Server) wrap(h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		p, cookie, err := s.principal(r)
		if err != nil {
			failure(w, err)
			return
		}
		if cookie && r.Method != "GET" && r.Method != "HEAD" && r.Header.Get("Origin") != s.options.PublicOrigin {
			failure(w, errForbidden)
			return
		}
		if err = h(w, r, p); err != nil {
			failure(w, err)
		}
	}
}

var errForbidden = errors.New("forbidden")

func owner(p auth.Principal) error {
	if p.Role != "owner" {
		return errForbidden
	}
	return nil
}
func permitted(p auth.Principal, action, project, env string) error {
	if !p.Can(action, project, env) {
		return errForbidden
	}
	return nil
}
func decode(w http.ResponseWriter, r *http.Request, target any) error {
	content, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || content != "application/json" {
		return domain.ErrInvalid
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 512*1024))
	if err != nil || !utf8.Valid(raw) || !strings.HasPrefix(strings.TrimSpace(string(raw)), "{") {
		return domain.ErrInvalid
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if decoder.Decode(target) != nil {
		return domain.ErrInvalid
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return domain.ErrInvalid
	}
	return nil
}
func reply(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(value)
}
func failure(w http.ResponseWriter, err error) {
	status, code, message := 500, "internal_error", "服务暂时不可用"
	switch {
	case errors.Is(err, registry.ErrMixedLayers):
		status, code, message = 400, "unsupported_layer_encoding", "归档包含不兼容的镜像层压缩格式，请使用 docker push 上传此镜像"
	case errors.Is(err, registry.ErrReferenced):
		status, code, message = 409, "image_referenced", "该镜像被发布版本、回滚版本或多架构镜像引用，不能删除"
	case errors.Is(err, registry.ErrExternal):
		status, code, message = 400, "external_registry", "上传和删除仅支持托管镜像仓库；外部仓库请在其管理端操作"
	case errors.Is(err, registry.ErrTagExists):
		status, code, message = 409, "tag_exists", "该标签已有不同镜像，请使用新标签"
	case errors.Is(err, registry.ErrArchive):
		status, code, message = 400, "invalid_image_archive", "镜像文件无效；请上传 docker save 导出的单镜像 tar 文件"
	case errors.Is(err, errImageTooLarge):
		status, code, message = 413, "image_too_large", "镜像文件不能超过 2 GiB"
	case errors.Is(err, errImageBusy):
		status, code, message = 409, "image_upload_busy", "服务端已有镜像正在上传，请稍后重试"
	case errors.Is(err, domain.ErrInvalid):
		status, code, message = 400, "invalid_input", "参数或制品校验失败"
	case errors.Is(err, domain.ErrConflict):
		status, code, message = 409, "conflict", "配置已更新或版本内容冲突，请重新加载"
	case errors.Is(err, domain.ErrNotFound):
		status, code, message = 404, "not_found", "项目、环境或可安装版本不存在"
	case errors.Is(err, domain.ErrUnauthorized):
		status, code, message = 401, "unauthorized", "凭据无效或已失效"
	case errors.Is(err, errForbidden):
		status, code, message = 403, "forbidden", "没有此操作的权限"
	}
	reply(w, status, map[string]string{"code": code, "message": message})
}
func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Origin") != s.options.PublicOrigin {
		failure(w, errForbidden)
		return
	}
	var body struct {
		Token string `json:"token"`
	}
	if err := decode(w, r, &body); err != nil {
		failure(w, err)
		return
	}
	p, err := s.tokenPrincipal(r.Context(), body.Token)
	if err != nil || p.Role != "owner" {
		failure(w, domain.ErrUnauthorized)
		return
	}
	raw, err := auth.NewToken()
	if err != nil {
		failure(w, err)
		return
	}
	if err = s.store.CreateSession(r.Context(), auth.HashToken(raw)); err != nil {
		failure(w, err)
		return
	}
	http.SetCookie(w, &http.Cookie{Name: "ctl_session", Value: raw, Path: "/", HttpOnly: true, Secure: s.options.CookieSecure, SameSite: http.SameSiteStrictMode, MaxAge: 8 * 3600})
	reply(w, 200, map[string]string{"role": "owner"})
}
func (s *Server) logout(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if c, err := r.Cookie("ctl_session"); err == nil {
		if err = s.store.DeleteSession(r.Context(), auth.HashToken(c.Value)); err != nil {
			return err
		}
	}
	http.SetCookie(w, &http.Cookie{Name: "ctl_session", Value: "", Path: "/", HttpOnly: true, Secure: s.options.CookieSecure, SameSite: http.SameSiteStrictMode, MaxAge: -1})
	reply(w, 200, map[string]bool{"ok": true})
	return nil
}
func (s *Server) static(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" && r.Method != "HEAD" || strings.HasPrefix(r.URL.Path, "/api/") {
		failure(w, domain.ErrNotFound)
		return
	}
	if s.options.WebDir == "" {
		failure(w, domain.ErrNotFound)
		return
	}
	name := "index.html"
	if strings.HasPrefix(r.URL.Path, "/assets/") || r.URL.Path == "/favicon.svg" {
		name = strings.TrimPrefix(r.URL.Path, "/")
	}
	path := filepath.Join(s.options.WebDir, filepath.Clean("/" + name)[1:])
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		failure(w, domain.ErrNotFound)
		return
	}
	http.ServeFile(w, r, path)
}
