package httpapi

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

var errLogBusy = errors.New("log query capacity reached")
var errLogUnavailable = errors.New("log node unavailable")
var errLogTimeout = errors.New("log query timed out")

type logTicket struct {
	query     domain.LogQuery
	actor     string
	result    chan domain.LogResult
	delivered bool
}
type logBroker struct {
	mu      sync.Mutex
	pending map[string]*logTicket
}

func newLogBroker() *logBroker { return &logBroker{pending: make(map[string]*logTicket)} }
func (b *logBroker) add(q domain.LogQuery, actor string) (*logTicket, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	count := 0
	for _, t := range b.pending {
		if t.query.NodeID == q.NodeID {
			count++
		}
	}
	if len(b.pending) >= 64 || count >= 2 {
		return nil, errLogBusy
	}
	q.ID = domain.NewID()
	ticket := &logTicket{query: q, actor: actor, result: make(chan domain.LogResult, 1)}
	b.pending[q.ID] = ticket
	return ticket, nil
}
func (b *logBroker) remove(id string) { b.mu.Lock(); defer b.mu.Unlock(); delete(b.pending, id) }
func (b *logBroker) take(node string) *logTicket {
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, t := range b.pending {
		if t.query.NodeID == node && !t.delivered {
			t.delivered = true
			return t
		}
	}
	return nil
}
func (b *logBroker) finish(node, id string, result domain.LogResult) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	t := b.pending[id]
	if t == nil || t.query.NodeID != node || !t.delivered {
		return false
	}
	select {
	case t.result <- result:
		return true
	default:
		return false
	}
}
func (b *logBroker) wait(ctx context.Context, t *logTicket) (domain.LogResult, error) {
	defer b.remove(t.query.ID)
	timer := time.NewTimer(25 * time.Second)
	defer timer.Stop()
	select {
	case r := <-t.result:
		return r, nil
	case <-ctx.Done():
		return domain.LogResult{}, ctx.Err()
	case <-timer.C:
		return domain.LogResult{}, errLogTimeout
	}
}
func (s *Server) logRoutes() {
	s.mux.HandleFunc("POST /api/v1/projects/{slug}/instances/{id}/logs", s.wrap(s.instanceLogs))
	s.mux.HandleFunc("POST /api/v1/node/logs/poll", s.nodeWrap(s.pollLogs))
	s.mux.HandleFunc("POST /api/v1/node/logs/{id}/result", s.nodeWrap(s.reportLogs))
}
func (s *Server) instanceLogs(w http.ResponseWriter, r *http.Request, p auth.Principal) error {
	q := domain.LogQuery{Source: "runtime", Tail: 200}
	if err := decode(w, r, &q); err != nil {
		return err
	}
	q.ID = ""
	q.NodeID = ""
	q.Environment = ""
	q.Project = r.PathValue("slug")
	q.InstanceID = r.PathValue("id")
	if err := q.Validate(); err != nil {
		return err
	}
	instance, node, err := s.store.AuthorizeLogQuery(r.Context(), p.ID, q)
	if err != nil {
		return err
	}
	if !node.Online {
		return errLogUnavailable
	}
	q.NodeID = node.ID
	q.Environment = instance.Environment
	ticket, err := s.logs.add(q, p.ID)
	if err != nil {
		return err
	}
	result, err := s.logs.wait(r.Context(), ticket)
	if err != nil {
		return err
	}
	// Token/session revocation while waiting must prevent disclosure.
	current, _, err := s.principal(r)
	if err != nil {
		return err
	}
	if current.ID != p.ID {
		return domain.ErrForbidden
	}
	if _, _, err = s.store.AuthorizeLogQuery(r.Context(), p.ID, q); err != nil {
		return err
	}
	reply(w, 200, result)
	return nil
}
func (s *Server) pollLogs(w http.ResponseWriter, r *http.Request, node string) error {
	var in struct{}
	if err := decode(w, r, &in); err != nil {
		return err
	}
	timer := time.NewTimer(20 * time.Second)
	defer timer.Stop()
	tick := time.NewTicker(200 * time.Millisecond)
	defer tick.Stop()
	for {
		if ticket := s.logs.take(node); ticket != nil {
			if _, _, err := s.store.AuthorizeLogQuery(r.Context(), ticket.actor, ticket.query); err != nil {
				s.logs.finish(node, ticket.query.ID, domain.LogResult{Error: "log_read_failed"})
				return err
			}
			reply(w, 200, map[string]any{"query": ticket.query})
			return nil
		}
		select {
		case <-r.Context().Done():
			return r.Context().Err()
		case <-timer.C:
			reply(w, 200, map[string]any{"query": nil})
			return nil
		case <-tick.C:
		}
	}
}
func (s *Server) reportLogs(w http.ResponseWriter, r *http.Request, node string) error {
	var result domain.LogResult
	if err := decodeLimit(w, r, &result, 2*1024*1024); err != nil {
		return err
	}
	if err := result.Validate(); err != nil {
		return err
	}
	if result.Sources == nil {
		result.Sources = []domain.LogSource{}
	}
	if !s.logs.finish(node, r.PathValue("id"), result) {
		return domain.ErrNotFound
	}
	reply(w, 200, map[string]bool{"accepted": true})
	return nil
}
