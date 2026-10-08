package httpapi

import (
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"net/http"
	"time"
)

func (s *Server) tokens(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.store.ListTokens(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, items)
	return nil
}
func (s *Server) createToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var token domain.Token
	if err := decode(w, r, &token); err != nil {
		return err
	}
	if len(token.Groups)+len(token.Projects) == 0 || token.Project != "" {
		return domain.ErrInvalid
	}
	if token.ExpiresAt.IsZero() {
		token.ExpiresAt = time.Now().Add(90 * 24 * time.Hour)
	}
	raw, err := auth.NewToken()
	if err != nil {
		return err
	}
	created, err := s.store.CreateReadableToken(r.Context(), token, raw, p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"credential": created, "token": raw})
	return nil
}
func (s *Server) updateToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var body struct {
		Name             string    `json:"name"`
		Role             string    `json:"role"`
		Groups           []string  `json:"groups"`
		Projects         []string  `json:"projects"`
		ExcludedProjects []string  `json:"excluded_projects"`
		Environments     []string  `json:"environments"`
		ExpiresAt        time.Time `json:"expires_at"`
	}
	if err := decode(w, r, &body); err != nil {
		return err
	}
	next, err := s.store.UpdateToken(r.Context(), r.PathValue("id"), domain.Token{Name: body.Name, Role: body.Role, Groups: body.Groups, Projects: body.Projects, ExcludedProjects: body.ExcludedProjects, Environments: body.Environments, ExpiresAt: body.ExpiresAt}, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, next)
	return nil
}
func (s *Server) tokenSecret(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	raw, err := s.store.TokenSecret(r.Context(), r.PathValue("id"), p.ID)
	if errors.Is(err, store.ErrTokenUnavailable) {
		reply(w, 409, map[string]string{"code": "token_unavailable", "message": "旧凭据未保存可恢复的Token，请重新生成Token后更新使用方。"})
		return nil
	}
	if err != nil {
		return err
	}
	reply(w, 200, map[string]string{"token": raw})
	return nil
}
func (s *Server) rotateToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	raw, err := auth.NewToken()
	if err != nil {
		return err
	}
	next, err := s.store.RotateToken(r.Context(), r.PathValue("id"), raw, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, map[string]any{"credential": next, "token": raw})
	return nil
}
func (s *Server) revokeToken(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	if err := s.store.RevokeToken(r.Context(), r.PathValue("id"), p.ID); err != nil {
		return err
	}
	reply(w, 200, map[string]bool{"ok": true})
	return nil
}
func (s *Server) receipts(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.store.ListReceipts(r.Context(), r.PathValue("slug"))
	if err != nil {
		return err
	}
	reply(w, 200, items)
	return nil
}
func (s *Server) saveReceipt(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	var receipt domain.Receipt
	if err := decode(w, r, &receipt); err != nil {
		return err
	}
	if receipt.Project != r.PathValue("slug") {
		return domain.ErrInvalid
	}
	if err := permitted(p, "receipt.write", receipt.Project, receipt.Environment); err != nil {
		return err
	}
	if err := s.store.SaveReceipt(r.Context(), receipt); err != nil {
		return err
	}
	reply(w, 201, map[string]bool{"ok": true})
	return nil
}
func (s *Server) audit(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	items, err := s.store.ListAudit(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, items)
	return nil
}
