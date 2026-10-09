package httpapi

import (
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"net/http"
	"slices"
)

func (s *Server) groups(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if p.Role != "owner" && p.Role != "publisher" {
		return errForbidden
	}
	groups, err := s.store.ListGroups(r.Context())
	if err != nil {
		return err
	}
	if p.Role != "owner" {
		groups = slices.DeleteFunc(groups, func(g domain.Group) bool { return !slices.Contains(p.Groups, g.Slug) })
	}
	reply(w, 200, groups)
	return nil
}

func (s *Server) deleteGroup(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	if err := s.store.DeleteGroup(r.Context(), r.PathValue("slug"), p.ID); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
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

func (s *Server) moveProjectGroup(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	if err := owner(p); err != nil {
		return err
	}
	var body struct {
		Group         string `json:"group"`
		ExpectedGroup string `json:"expected_group"`
	}
	if err := decode(w, r, &body); err != nil {
		return err
	}
	project, err := s.store.MoveProjectGroup(r.Context(), r.PathValue("slug"), body.ExpectedGroup, body.Group, p.ID)
	if err != nil {
		return err
	}
	reply(w, 200, project)
	return nil
}
