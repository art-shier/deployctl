package httpapi

import (
	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"net/http"
	"strconv"
	"time"
)

func (s *Server) applicationLogRoutes() {
	base := "/api/v1/projects/{slug}/environments/{env}/logging"
	s.mux.HandleFunc("GET "+base+"/rules", s.wrap(func(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
		v, e := s.store.LogPolicy(r.Context(), r.PathValue("slug"), r.PathValue("env"), p.ID)
		if e == nil {
			reply(w, 200, v)
		}
		return e
	}))
	s.mux.HandleFunc("PUT "+base+"/rules", s.wrap(func(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
		var in domain.LogPolicy
		if e := decodeLimit(w, r, &in, 256*1024); e != nil {
			return e
		}
		v, e := s.store.SaveLogPolicy(r.Context(), r.PathValue("slug"), r.PathValue("env"), p.ID, in)
		if e == nil {
			reply(w, 200, v)
		}
		return e
	}))
	s.mux.HandleFunc("POST "+base+"/query-permission", s.wrap(func(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
		var in struct {
			Attributes []domain.LogAttribute `json:"attributes"`
		}
		if e := decodeLimit(w, r, &in, 4096); e != nil {
			return e
		}
		v, e := s.store.QueryLogPermission(r.Context(), r.PathValue("slug"), r.PathValue("env"), p.ID, in.Attributes)
		if e == nil {
			reply(w, 200, v)
		}
		return e
	}))
	s.mux.HandleFunc("POST "+base+"/events", s.wrap(func(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
		var in struct {
			Events []domain.ApplicationLog `json:"events"`
		}
		if e := decodeLimit(w, r, &in, 1024*1024); e != nil {
			return e
		}
		v, e := s.store.AppendApplicationLogs(r.Context(), r.PathValue("slug"), r.PathValue("env"), p.ID, in.Events)
		if e == nil {
			reply(w, 200, v)
		}
		return e
	}))
	s.mux.HandleFunc("GET "+base+"/events", s.wrap(s.applicationLogs))
}
func (s *Server) applicationLogs(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	v := r.URL.Query()
	q := store.ApplicationLogQuery{Limit: 100, UserID: v.Get("user_id"), DeviceID: v.Get("device_id"), Model: v.Get("model"), RequestID: v.Get("request_id"), TraceID: v.Get("trace_id"), Source: v.Get("source"), Level: v.Get("level"), Event: v.Get("event"), AppVersion: v.Get("app_version"), Search: v.Get("q")}
	var e error
	if v.Has("limit") {
		q.Limit, e = strconv.Atoi(v.Get("limit"))
		if e != nil {
			return domain.ErrInvalid
		}
	}
	if v.Has("before") {
		q.Before, e = strconv.ParseInt(v.Get("before"), 10, 64)
		if e != nil {
			return domain.ErrInvalid
		}
	}
	for key, target := range map[string]*time.Time{"since": &q.Since, "until": &q.Until} {
		if v.Has(key) {
			*target, e = time.Parse(time.RFC3339Nano, v.Get(key))
			if e != nil {
				return domain.ErrInvalid
			}
		}
	}
	out, e := s.store.QueryApplicationLogs(r.Context(), r.PathValue("slug"), r.PathValue("env"), p.ID, q)
	if e == nil {
		reply(w, 200, out)
	}
	return e
}
