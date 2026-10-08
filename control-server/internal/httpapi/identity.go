package httpapi

import (
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
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
	if token.ExpiresAt.IsZero() {
		token.ExpiresAt = time.Now().Add(90 * 24 * time.Hour)
	}
	if _, err := s.store.GetProject(r.Context(), token.Project); err != nil {
		return err
	}
	raw, err := auth.NewToken()
	if err != nil {
		return err
	}
	created, err := s.store.CreateToken(r.Context(), token, auth.HashToken(raw), p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, map[string]any{"credential": created, "token": raw})
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
