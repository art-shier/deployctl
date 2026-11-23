package store

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
	"strings"
	"time"
)

// All operations re-read credentials/project membership inside the transaction.
func (s *Store) logTx(ctx context.Context, project, env, actor, action string) (pgx.Tx, error) {
	if domain.ValidateName(project, 48) != nil || domain.ValidateName(env, 32) != nil {
		return nil, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	fail := func(e error) (pgx.Tx, error) { tx.Rollback(ctx); return nil, e }
	p, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", project))
	if err != nil {
		return fail(err)
	}
	principal, err := remotePrincipal(ctx, tx, actor)
	if err != nil {
		return fail(err)
	}
	if !principal.CanInGroup(action, project, p.Group, env) {
		return fail(domain.ErrForbidden)
	}
	var exists bool
	err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_environments WHERE project=$1 AND name=$2 UNION ALL SELECT 1 FROM ctl_group_environments WHERE group_slug=$3 AND name=$2)", project, env, p.Group).Scan(&exists)
	if err != nil {
		return fail(err)
	}
	if !exists {
		return fail(domain.ErrNotFound)
	}
	return tx, nil
}
func readLogPolicy(ctx context.Context, tx pgx.Tx, project, env string) (domain.LogPolicy, error) {
	p := domain.LogPolicy{Rules: []domain.LogRule{}}
	var b []byte
	err := tx.QueryRow(ctx, "SELECT version,data FROM ctl_log_policies WHERE project=$1 AND environment=$2", project, env).Scan(&p.Version, &b)
	if err == pgx.ErrNoRows {
		return p, nil
	}
	if err != nil {
		return p, err
	}
	err = json.Unmarshal(b, &p.Rules)
	if p.Rules == nil {
		p.Rules = []domain.LogRule{}
	}
	return p, err
}
func (s *Store) LogPolicy(ctx context.Context, project, env, actor string) (domain.LogPolicy, error) {
	tx, err := s.logTx(ctx, project, env, actor, "logs.read")
	if err != nil {
		return domain.LogPolicy{}, err
	}
	defer tx.Rollback(ctx)
	p, err := readLogPolicy(ctx, tx, project, env)
	if err != nil {
		return p, err
	}
	return p, tx.Commit(ctx)
}
func lockLogScope(ctx context.Context, tx pgx.Tx, project, env string) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "app-logs:"+project+":"+env)
	return err
}
func (s *Store) SaveLogPolicy(ctx context.Context, project, env, actor string, p domain.LogPolicy) (domain.LogPolicy, error) {
	if err := p.Validate(); err != nil {
		return p, err
	}
	tx, err := s.logTx(ctx, project, env, actor, "logs.rules.write")
	if err != nil {
		return p, err
	}
	defer tx.Rollback(ctx)
	if err = lockLogScope(ctx, tx, project, env); err != nil {
		return p, err
	}
	old, err := readLogPolicy(ctx, tx, project, env)
	if err != nil {
		return p, err
	}
	if p.Version != old.Version {
		return p, domain.ErrConflict
	}
	p.Version++
	b, err := json.Marshal(p.Rules)
	if err != nil {
		return p, err
	}
	_, err = tx.Exec(ctx, "INSERT INTO ctl_log_policies(project,environment,version,data) VALUES($1,$2,$3,$4) ON CONFLICT(project,environment) DO UPDATE SET version=excluded.version,data=excluded.data", project, env, p.Version, b)
	if err != nil {
		return p, err
	}
	if err = audit(ctx, tx, actor, "logs.rules.write", project, env, nil); err != nil {
		return p, err
	}
	return p, tx.Commit(ctx)
}
func (s *Store) QueryLogPermission(ctx context.Context, project, env, actor string, a []domain.LogAttribute) (domain.LogDecision, error) {
	if err := domain.ValidateLogAttributes(a); err != nil {
		return domain.LogDecision{}, err
	}
	tx, err := s.logTx(ctx, project, env, actor, "logs.write")
	if err != nil {
		return domain.LogDecision{}, err
	}
	defer tx.Rollback(ctx)
	p, err := readLogPolicy(ctx, tx, project, env)
	if err != nil {
		return domain.LogDecision{}, err
	}
	return p.Decide(project, env, a), tx.Commit(ctx)
}

type LogAppendResult struct {
	Accepted   int `json:"accepted"`
	Rejected   int `json:"rejected"`
	Duplicates int `json:"duplicates"`
}

func (s *Store) AppendApplicationLogs(ctx context.Context, project, env, actor string, events []domain.ApplicationLog) (LogAppendResult, error) {
	result := LogAppendResult{}
	if len(events) == 0 || len(events) > 100 {
		return result, domain.ErrInvalid
	}
	for _, e := range events {
		if err := e.Validate(); err != nil {
			return result, err
		}
	}
	tx, err := s.logTx(ctx, project, env, actor, "logs.write")
	if err != nil {
		return result, err
	}
	defer tx.Rollback(ctx)
	if err = lockLogScope(ctx, tx, project, env); err != nil {
		return result, err
	}
	p, err := readLogPolicy(ctx, tx, project, env)
	if err != nil {
		return result, err
	}
	for _, e := range events {
		if !p.Decide(project, env, e.Attributes()).Allowed {
			result.Rejected++
			continue
		}
		// Retain idempotency keys for 3 days independently of content capacity eviction.
		key, err := tx.Exec(ctx, "INSERT INTO ctl_application_log_ids(project,environment,event_id) VALUES($1,$2,$3) ON CONFLICT(project,environment,event_id) DO UPDATE SET received_at=excluded.received_at WHERE ctl_application_log_ids.received_at<now()-interval '3 days'", project, env, e.EventID)
		if err != nil {
			return result, err
		}
		if key.RowsAffected() == 0 {
			result.Duplicates++
			continue
		}
		e.ReceivedAt = time.Now().UTC()
		b, err := json.Marshal(e)
		if err != nil {
			return result, err
		}
		c, err := tx.Exec(ctx, "INSERT INTO ctl_application_logs(project,environment,event_id,received_at,data) VALUES($1,$2,$3,$4,$5) ON CONFLICT(project,environment,event_id) DO NOTHING", project, env, e.EventID, e.ReceivedAt, b)
		if err != nil {
			return result, err
		}
		if c.RowsAffected() == 0 {
			result.Duplicates++
		} else {
			result.Accepted++
		}
	}
	// Bound each active project/environment to 10,000 events and 3 days.
	_, err = tx.Exec(ctx, "DELETE FROM ctl_application_logs WHERE project=$1 AND environment=$2 AND (received_at<now()-interval '3 days' OR id IN (SELECT id FROM ctl_application_logs WHERE project=$1 AND environment=$2 ORDER BY id DESC OFFSET 10000))", project, env)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

type ApplicationLogQuery struct {
	Limit                                                                                 int
	Before                                                                                int64
	Since                                                                                 time.Time
	Until                                                                                 time.Time
	UserID, DeviceID, Model, RequestID, TraceID, Source, Level, Event, AppVersion, Search string
}
type ApplicationLogPage struct {
	Items      []domain.ApplicationLog `json:"items"`
	NextCursor int64                   `json:"next_cursor,omitempty"`
}

func (s *Store) QueryApplicationLogs(ctx context.Context, project, env, actor string, q ApplicationLogQuery) (ApplicationLogPage, error) {
	out := ApplicationLogPage{Items: []domain.ApplicationLog{}}
	if q.Limit == 0 {
		q.Limit = 100
	}
	if q.Limit < 1 || q.Limit > 200 || q.Before < 0 || (!q.Until.IsZero() && !q.Since.IsZero() && q.Until.Before(q.Since)) {
		return out, domain.ErrInvalid
	}
	tx, err := s.logTx(ctx, project, env, actor, "logs.read")
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	args := []any{project, env}
	where := "project=$1 AND environment=$2 AND received_at>=now()-interval '3 days'"
	add := func(expr string, v any) { args = append(args, v); where += " AND " + fmt.Sprintf(expr, len(args)) }
	for _, f := range []struct{ k, v string }{{"user_id", q.UserID}, {"device_id", q.DeviceID}, {"model", q.Model}, {"request_id", q.RequestID}, {"trace_id", q.TraceID}, {"source", q.Source}, {"level", q.Level}, {"event", q.Event}, {"app_version", q.AppVersion}} {
		if len(f.v) > 256 {
			return out, domain.ErrInvalid
		}
		if f.v != "" {
			add("data->>'"+f.k+"'=$%d", f.v)
		}
	}
	if q.Before > 0 {
		add("id<$%d", q.Before)
	}
	if !q.Since.IsZero() {
		add("(data->>'occurred_at')::timestamptz >= $%d", q.Since)
	}
	if !q.Until.IsZero() {
		add("(data->>'occurred_at')::timestamptz <= $%d", q.Until)
	}
	if len(q.Search) > 256 || strings.ContainsRune(q.Search, 0) {
		return out, domain.ErrInvalid
	}
	if q.Search != "" {
		add("strpos(lower(data::text),lower($%d))>0", q.Search)
	}
	args = append(args, q.Limit+1)
	rows, err := tx.Query(ctx, "SELECT id,data FROM ctl_application_logs WHERE "+where+fmt.Sprintf(" ORDER BY id DESC LIMIT $%d", len(args)), args...)
	if err != nil {
		return out, err
	}
	var last int64
	for rows.Next() {
		var id int64
		var b []byte
		if err = rows.Scan(&id, &b); err != nil {
			rows.Close()
			return out, err
		}
		if len(out.Items) == q.Limit {
			out.NextCursor = last
			break
		}
		var e domain.ApplicationLog
		if err = json.Unmarshal(b, &e); err != nil {
			rows.Close()
			return out, err
		}
		out.Items = append(out.Items, e)
		last = id
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

// Called by the server maintenance loop even if a scope no longer receives uploads.
func (s *Store) PruneApplicationLogs(ctx context.Context) error {
	_, err := s.pool.Exec(ctx, "DELETE FROM ctl_application_logs WHERE received_at<now()-interval '3 days'; DELETE FROM ctl_application_log_ids WHERE received_at<now()-interval '3 days'")
	return err
}
