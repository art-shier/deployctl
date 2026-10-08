package httpapi

import (
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"net/http"
)

func (s *Server) groups(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	groups, err := s.store.ListGroups(r.Context())
	if err != nil {
		return err
	}
	reply(w, 200, groups)
	return nil
}
func (s *Server) createGroup(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var g domain.Group
	if err := decode(w, r, &g); err != nil {
		return err
	}
	result, err := s.store.CreateGroup(r.Context(), g, p.ID)
	if err != nil {
		return err
	}
	reply(w, 201, result)
	return nil
}
func (s *Server) updateGroup(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var g domain.Group
	if err := decode(w, r, &g); err != nil {
		return err
	}
	if g.Slug != r.PathValue("slug") {
		return domain.ErrInvalid
	}
	result, err := s.store.UpdateGroup(r.Context(), g, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, result)
	return nil
}
