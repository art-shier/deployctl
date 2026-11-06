package store

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"time"
	"unicode"

	"github.com/art-shier/deployctl/control-server/internal/auth"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

func normalizeNode(in domain.NodeInput) domain.NodeInput {
	if in.Groups == nil {
		in.Groups = []string{}
	}
	if in.Projects == nil {
		in.Projects = []string{}
	}
	if in.ExcludedProjects == nil {
		in.ExcludedProjects = []string{}
	}
	if in.Environments == nil {
		in.Environments = []string{}
	}
	return in
}
func scanNode(row pgx.Row) (domain.Node, error) {
	var n domain.Node
	var raw []byte
	var registered bool
	if err := row.Scan(&raw, &registered); err != nil {
		return n, mapped(err)
	}
	if err := json.Unmarshal(raw, &n); err != nil {
		return n, err
	}
	n.Online = registered && n.LastSeen != nil && time.Since(*n.LastSeen) < 90*time.Second
	return n, nil
}
func (s *Store) ListNodes(ctx context.Context) ([]domain.Node, error) {
	rows, err := s.pool.Query(ctx, "SELECT data,registered FROM ctl_nodes ORDER BY data->>'created_at',id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Node{}
	for rows.Next() {
		n, e := scanNode(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, n)
	}
	return out, rows.Err()
}

// CreateNodeEnrollment issues a one-use invitation without precreating a node.
func (s *Store) CreateNodeEnrollment(ctx context.Context, actor string) (string, time.Time, error) {
	raw, err := auth.NewToken()
	if err != nil {
		return "", time.Time{}, err
	}
	expires := time.Now().UTC().Add(15 * time.Minute)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", expires, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "DELETE FROM ctl_node_enrollments WHERE expires_at<=now()"); err != nil {
		return "", expires, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_node_enrollments(hash,expires_at) VALUES($1,$2)", auth.HashToken(raw), expires); err != nil {
		return "", expires, err
	}
	if err = audit(ctx, tx, actor, "node.enrollment.create", "", "", nil); err != nil {
		return "", expires, err
	}
	return raw, expires, tx.Commit(ctx)
}

// CreateNode is the legacy pre-provisioning API. New clients use discovery.
func (s *Store) CreateNode(ctx context.Context, in domain.NodeInput, actor string) (domain.Node, string, time.Time, error) {
	var n domain.Node
	if err := in.Validate(); err != nil {
		return n, "", time.Time{}, err
	}
	in = normalizeNode(in)
	raw, err := auth.NewToken()
	if err != nil {
		return n, "", time.Time{}, err
	}
	expires := time.Now().UTC().Add(15 * time.Minute)
	n = domain.Node{NodeInput: in, ID: domain.NewID(), CreatedAt: time.Now().UTC()}
	data, _ := json.Marshal(n)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return n, "", expires, err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_nodes(id,data,enrollment_hash,enrollment_expires) VALUES($1,$2,$3,$4)", n.ID, data, auth.HashToken(raw), expires); err != nil {
		return n, "", expires, mapped(err)
	}
	if err = audit(ctx, tx, actor, "node.create", "", "", nil); err != nil {
		return n, "", expires, err
	}
	return n, raw, expires, tx.Commit(ctx)
}
func (s *Store) UpdateNode(ctx context.Context, id string, in domain.NodeInput, actor string) (domain.Node, error) {
	var n domain.Node
	if err := in.Validate(); err != nil {
		return n, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return n, err
	}
	defer tx.Rollback(ctx)
	n, err = lockRemoteNode(ctx, tx, id, "UPDATE")
	if err != nil {
		return n, err
	}
	n.NodeInput = normalizeNode(in)
	if n.PendingAuthorization && n.Enabled && len(n.Groups)+len(n.Projects) > 0 && len(n.Environments) > 0 {
		n.PendingAuthorization = false
	}
	data, _ := json.Marshal(n)
	if _, err = tx.Exec(ctx, "UPDATE ctl_nodes SET data=$2 WHERE id=$1", id, data); err != nil {
		return n, err
	}
	if err = audit(ctx, tx, actor, "node.update", "", "", nil); err != nil {
		return n, err
	}
	return n, tx.Commit(ctx)
}
func (s *Store) RotateNodeEnrollment(ctx context.Context, id, actor string) (string, time.Time, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", time.Time{}, err
	}
	defer tx.Rollback(ctx)
	n, err := lockRemoteNode(ctx, tx, id, "UPDATE")
	if err != nil {
		return "", time.Time{}, err
	}
	if err = s.invalidateUnstartedTasks(ctx, tx, n); err != nil {
		return "", time.Time{}, err
	}
	var active bool
	if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_remote_tasks WHERE node_id=$1 AND status IN ('queued','leased','running'))", id).Scan(&active); err != nil {
		return "", time.Time{}, err
	}
	if active {
		return "", time.Time{}, domain.ErrConflict
	}
	raw, err := auth.NewToken()
	if err != nil {
		return "", time.Time{}, err
	}
	expires := time.Now().UTC().Add(15 * time.Minute)
	if _, err = tx.Exec(ctx, "UPDATE ctl_nodes SET enrollment_hash=$2,enrollment_expires=$3,token_hash=NULL,registered=false WHERE id=$1", id, auth.HashToken(raw), expires); err != nil {
		return "", expires, err
	}
	if err = audit(ctx, tx, actor, "node.enrollment.rotate", "", "", nil); err != nil {
		return "", expires, err
	}
	return raw, expires, tx.Commit(ctx)
}
func validNodeAgent(agent, platform string) bool {
	return len(agent) > 0 && len(agent) <= 128 && len(platform) > 0 && len(platform) <= 128 && !strings.ContainsAny(agent+platform, "\x00\r\n")
}
func (s *Store) EnrollNode(ctx context.Context, hash, agent, platform string) (string, string, error) {
	return s.EnrollNodeNamed(ctx, hash, agent, platform, "")
}
func (s *Store) EnrollNodeNamed(ctx context.Context, hash, agent, platform, name string) (string, string, error) {
	if !validNodeAgent(agent, platform) || len(name) > 512 || strings.IndexFunc(name, unicode.IsControl) >= 0 {
		return "", "", domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", "", err
	}
	defer tx.Rollback(ctx)
	// DELETE serializes concurrent redemption; node creation commits with consumption.
	var consumed string
	err = tx.QueryRow(ctx, "DELETE FROM ctl_node_enrollments WHERE hash=$1 AND expires_at>now() RETURNING hash", hash).Scan(&consumed)
	discovered := err == nil
	var n domain.Node
	if errors.Is(err, pgx.ErrNoRows) {
		n, err = scanNode(tx.QueryRow(ctx, "SELECT data,registered FROM ctl_nodes WHERE enrollment_hash=$1 AND enrollment_expires>now() FOR UPDATE", hash))
		if errors.Is(err, domain.ErrNotFound) {
			err = domain.ErrUnauthorized
		}
	}
	if err != nil {
		return "", "", err
	}
	if discovered {
		n = domain.Node{ID: domain.NewID(), CreatedAt: time.Now().UTC(), PendingAuthorization: true}
		name = strings.TrimSpace(name)
		if name == "" {
			name = "node-" + n.ID[:8]
		}
		n.NodeInput = normalizeNode(domain.NodeInput{Name: name, Enabled: true})
	}
	raw, err := auth.NewToken()
	if err != nil {
		return "", "", err
	}
	n.AgentVersion = agent
	n.Platform = platform
	n.Online = false
	n.LastSeen = nil
	n.Metrics = nil
	n.MetricsAt = nil
	data, _ := json.Marshal(n)
	if discovered {
		_, err = tx.Exec(ctx, "INSERT INTO ctl_nodes(id,data,token_hash,registered) VALUES($1,$2,$3,true)", n.ID, data, auth.HashToken(raw))
	} else {
		_, err = tx.Exec(ctx, "UPDATE ctl_nodes SET data=$2,token_hash=$3,enrollment_hash=NULL,enrollment_expires=NULL,registered=true WHERE id=$1", n.ID, data, auth.HashToken(raw))
	}
	if err != nil {
		return "", "", err
	}
	if err = audit(ctx, tx, n.ID, "node.enroll", "", "", nil); err != nil {
		return "", "", err
	}
	return n.ID, raw, tx.Commit(ctx)
}
func (s *Store) AuthenticateNode(ctx context.Context, hash string) (string, error) {
	var id string
	err := s.pool.QueryRow(ctx, "SELECT id FROM ctl_nodes WHERE token_hash=$1 AND registered", hash).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", domain.ErrUnauthorized
	}
	return id, err
}
func scanInstance(row pgx.Row) (domain.Instance, error) {
	var i domain.Instance
	var b []byte
	if err := row.Scan(&b); err != nil {
		return i, mapped(err)
	}
	return i, json.Unmarshal(b, &i)
}
func scanTask(row pgx.Row) (domain.Task, error) {
	var t domain.Task
	var b []byte
	if err := row.Scan(&b); err != nil {
		return t, mapped(err)
	}
	return t, json.Unmarshal(b, &t)
}
func writeTask(ctx context.Context, tx pgx.Tx, t *domain.Task) error {
	t.UpdatedAt = time.Now().UTC()
	b, _ := json.Marshal(t)
	_, err := tx.Exec(ctx, "UPDATE ctl_remote_tasks SET status=$2,data=$3 WHERE id=$1", t.ID, t.Status, b)
	return err
}
func (s *Store) ListInstances(ctx context.Context, project string) ([]domain.Instance, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_instances WHERE project=$1 ORDER BY node_id,environment", project)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Instance{}
	for rows.Next() {
		i, e := scanInstance(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, i)
	}
	return out, rows.Err()
}
func (s *Store) ListTasks(ctx context.Context, project string) ([]domain.Task, error) {
	rows, err := s.pool.Query(ctx, "SELECT data FROM ctl_remote_tasks WHERE project=$1 ORDER BY created_at DESC,id DESC LIMIT 200", project)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []domain.Task{}
	for rows.Next() {
		t, e := scanTask(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, t)
	}
	return out, rows.Err()
}
func observe(ctx context.Context, tx pgx.Tx, node string, o domain.Observation) error {
	if err := o.Validate(); err != nil {
		return err
	}
	i, err := scanInstance(tx.QueryRow(ctx, "SELECT data FROM ctl_instances WHERE id=$1 AND node_id=$2 FOR UPDATE", o.ID, node))
	if err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return domain.ErrForbidden
		}
		return err
	}
	if i.Project != o.Project || i.Environment != o.Environment {
		return domain.ErrForbidden
	}
	now := time.Now().UTC()
	if o.Health == "unknown" && o.ActualVersion == "" {
		o.ActualVersion = i.ActualVersion
		o.Binding = i.Binding
		o.PendingTransaction = o.PendingTransaction || i.PendingTransaction
	}
	i.Observation = o
	i.ObservedAt = &now
	b, _ := json.Marshal(i)
	_, err = tx.Exec(ctx, "UPDATE ctl_instances SET data=$2 WHERE id=$1", i.ID, b)
	return err
}
func (s *Store) HeartbeatNode(ctx context.Context, id, agent, platform string, docker bool, observations []domain.Observation, metrics ...*domain.NodeMetrics) (bool, []domain.Instance, error) {
	if len(metrics) > 1 {
		return false, nil, domain.ErrInvalid
	}
	if len(metrics) == 1 && metrics[0] != nil {
		if err := metrics[0].Validate(); err != nil {
			return false, nil, err
		}
	}
	if !validNodeAgent(agent, platform) || len(observations) > 1000 {
		return false, nil, domain.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return false, nil, err
	}
	defer tx.Rollback(ctx)
	n, err := lockRemoteNode(ctx, tx, id, "UPDATE")
	if err != nil {
		return false, nil, err
	}
	for _, o := range observations {
		if err = observe(ctx, tx, id, o); err != nil {
			return false, nil, err
		}
	}
	now := time.Now().UTC()
	n.LastSeen = &now
	if len(metrics) == 1 && metrics[0] != nil {
		n.Metrics = metrics[0]
		n.MetricsAt = &now
	}
	n.AgentVersion = agent
	n.Platform = platform
	n.DockerAvailable = docker
	b, _ := json.Marshal(n)
	if _, err = tx.Exec(ctx, "UPDATE ctl_nodes SET data=$2 WHERE id=$1", id, b); err != nil {
		return false, nil, err
	}
	rows, err := tx.Query(ctx, "SELECT data FROM ctl_instances WHERE node_id=$1 ORDER BY id", id)
	if err != nil {
		return false, nil, err
	}
	out := []domain.Instance{}
	for rows.Next() {
		i, e := scanInstance(rows)
		if e != nil {
			rows.Close()
			return false, nil, e
		}
		out = append(out, i)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return false, nil, err
	}
	return n.Enabled, out, tx.Commit(ctx)
}

// Re-read the persisted token under a share lock. A queued task never preserves
// a revoked token's former privileges or stale group expansion.
func remotePrincipal(ctx context.Context, tx pgx.Tx, actor string) (auth.Principal, error) {
	if actor == "owner" {
		return auth.Principal{ID: "owner", Role: "owner"}, nil
	}
	var b []byte
	if err := tx.QueryRow(ctx, "SELECT data FROM ctl_tokens WHERE id=$1 AND NOT revoked AND expires_at>now() FOR SHARE", actor).Scan(&b); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return auth.Principal{}, domain.ErrForbidden
		}
		return auth.Principal{}, err
	}
	var t domain.Token
	if err := json.Unmarshal(b, &t); err != nil {
		return auth.Principal{}, err
	}
	return auth.Principal{ID: t.ID, Role: t.Role, Project: t.Project, Projects: t.Projects, ExplicitProjects: t.Projects, Groups: t.Groups, ExcludedProjects: t.ExcludedProjects, Environments: t.Environments}, nil
}
func remoteAuthorization(ctx context.Context, tx pgx.Tx, n domain.Node, t domain.Task) error {
	p, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", t.Project))
	if err != nil {
		return err
	}
	principal, err := remotePrincipal(ctx, tx, t.Actor)
	if err != nil {
		return err
	}
	if t.InstanceID != "" && t.Action != "rollback" {
		instance, e := scanInstance(tx.QueryRow(ctx, "SELECT data FROM ctl_instances WHERE id=$1 AND node_id=$2", t.InstanceID, n.ID))
		if e != nil {
			return e
		}
		if instance.PendingTransaction {
			return domain.ErrForbidden
		}
	}
	if !n.Allows(p, t.Environment) || !principal.CanInGroup("resolve", p.Slug, p.Group, t.Environment) {
		return domain.ErrForbidden
	}
	return nil
}

type frozenTask struct {
	Resolved Resolved           `json:"resolved"`
	Request  domain.TaskRequest `json:"request"`
}

func (s *Store) CreateTask(ctx context.Context, project string, request domain.TaskRequest, principal auth.Principal) (domain.Task, error) {
	var task domain.Task
	if domain.ValidateName(project, 48) != nil || request.Validate() != nil {
		return task, domain.ErrInvalid
	}
	requestData, _ := json.Marshal(struct {
		Project string
		Request domain.TaskRequest
	}{project, request})
	requestHash := auth.HashToken(string(requestData))
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return task, err
	}
	defer tx.Rollback(ctx)
	// Actor/request lock also serializes retries addressed to different nodes.
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "remote-request:"+principal.ID+":"+request.RequestID); err != nil {
		return task, err
	}
	var existingHash string
	var data []byte
	err = tx.QueryRow(ctx, "SELECT data,request_hash FROM ctl_remote_tasks WHERE actor=$1 AND request_id=$2", principal.ID, request.RequestID).Scan(&data, &existingHash)
	if err == nil {
		if existingHash != requestHash {
			return task, domain.ErrConflict
		}
		if err = json.Unmarshal(data, &task); err != nil {
			return task, err
		}
		project, e := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", task.Project))
		if e != nil {
			return task, e
		}
		current, e := remotePrincipal(ctx, tx, principal.ID)
		if e != nil {
			return task, e
		}
		if !current.CanInGroup("resolve", project.Slug, project.Group, task.Environment) {
			return task, domain.ErrForbidden
		}
		return task, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return task, err
	}
	n, err := scanNode(tx.QueryRow(ctx, "SELECT data,registered FROM ctl_nodes WHERE id=$1 FOR UPDATE", request.NodeID))
	if err != nil {
		return task, err
	}
	task = domain.Task{ID: domain.NewID(), NodeID: n.ID, Project: project, Environment: request.Environment, Action: request.Action, Actor: principal.ID, RequestID: request.RequestID, Status: "queued", Stage: "accepted", CreatedAt: time.Now().UTC()}
	task.UpdatedAt = task.CreatedAt
	if err = remoteAuthorization(ctx, tx, n, task); err != nil {
		return task, err
	}
	p, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL", project))
	if err != nil {
		return task, err
	}
	instance, err := scanInstance(tx.QueryRow(ctx, "SELECT data FROM ctl_instances WHERE node_id=$1 AND project=$2 AND environment=$3 FOR UPDATE", n.ID, project, request.Environment))
	if errors.Is(err, domain.ErrNotFound) {
		instance = domain.Instance{Observation: domain.Observation{ID: domain.NewID(), Project: project, Environment: request.Environment, Health: "not_installed"}, NodeID: n.ID}
		b, _ := json.Marshal(instance)
		_, err = tx.Exec(ctx, "INSERT INTO ctl_instances(id,node_id,project,environment,data) VALUES($1,$2,$3,$4,$5)", instance.ID, n.ID, project, request.Environment, b)
	}
	if err != nil {
		return task, mapped(err)
	}
	task.InstanceID = instance.ID
	if instance.PendingTransaction && request.Action != "rollback" {
		return task, domain.ErrConflict
	}
	frozen := frozenTask{Request: request}
	if request.Action != "rollback" {
		rev, e := s.projectRevision(ctx, tx, p, request.Environment)
		if e != nil {
			return task, e
		}
		rev = effectiveRevision(p, rev)
		if e = domain.ValidateConfiguration(rev.Configuration); e != nil {
			return task, e
		}
		version := request.Version
		if version == "" {
			version = rev.TargetVersion
		}
		if version == "stable" {
			if e = tx.QueryRow(ctx, "SELECT stable_version FROM ctl_projects WHERE slug=$1", project).Scan(&version); e != nil {
				return task, e
			}
		}
		release, e := scanRelease(tx.QueryRow(ctx, "SELECT data,status FROM ctl_releases WHERE project=$1 AND version=$2 AND status='published'", project, version))
		if e != nil {
			return task, e
		}
		frozen.Resolved = Resolved{Project: p, Revision: rev, Release: release}
		task.Version = release.Version
	}
	data, _ = json.Marshal(frozen)
	encrypted, err := s.cipher.Seal(data, "ctl-remote-task/"+task.ID)
	if err != nil {
		return task, err
	}
	data, _ = json.Marshal(task)
	if _, err = tx.Exec(ctx, "INSERT INTO ctl_remote_tasks(id,instance_id,node_id,project,actor,request_id,request_hash,status,created_at,expires_at,data,ciphertext) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)", task.ID, task.InstanceID, task.NodeID, project, task.Actor, task.RequestID, requestHash, task.Status, task.CreatedAt, task.CreatedAt.Add(24*time.Hour), data, encrypted); err != nil {
		return task, mapped(err)
	}
	if err = audit(ctx, tx, principal.ID, "remote.task.create:"+task.Action, project, task.Environment, nil); err != nil {
		return task, err
	}
	return task, tx.Commit(ctx)
}
func (s *Store) agentTask(ctx context.Context, tx pgx.Tx, t domain.Task) (domain.AgentTask, error) {
	out := domain.AgentTask{Task: t}
	var encrypted []byte
	if err := tx.QueryRow(ctx, "SELECT ciphertext FROM ctl_remote_tasks WHERE id=$1", t.ID).Scan(&encrypted); err != nil {
		return out, err
	}
	b, err := s.cipher.Open(encrypted, "ctl-remote-task/"+t.ID)
	if err != nil {
		return out, err
	}
	var f frozenTask
	if err = json.Unmarshal(b, &f); err != nil {
		return out, err
	}
	out.HostPort = f.Request.HostPort
	out.BindAddress = f.Request.BindAddress
	if t.Action != "rollback" {
		r, v := f.Resolved.Release, f.Resolved.Revision
		out.Resolution = map[string]any{"schema_version": 1, "minimum_client_version": "1.7.0", "project": t.Project, "environment": t.Environment, "release": map[string]any{"id": r.ID, "version": r.Version, "image": r.Image, "sha256": r.SHA256, "package_path": "/api/v1/node/tasks/" + t.ID + "/package"}, "configuration": map[string]any{"id": v.ID, "revision": v.Revision, "runtime_env": domain.Values(v.Configuration.RuntimeEnv), "install_params": domain.Values(v.Configuration.InstallParams), "deployment_defaults": v.Configuration.DeploymentDefaults}}
	}
	return out, nil
}
func finishTask(t *domain.Task, status string) {
	t.Status = status
	t.Result = &domain.TaskResult{Code: status, Stage: t.Stage}
}
func (s *Store) PollNode(ctx context.Context, node string) (*domain.AgentTask, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = lockUpdateScheduler(ctx, tx); err != nil {
		return nil, err
	}
	n, err := lockRemoteNode(ctx, tx, node, "UPDATE")
	if err != nil {
		return nil, err
	}
	t, err := scanTask(tx.QueryRow(ctx, "SELECT data FROM ctl_remote_tasks WHERE node_id=$1 AND status IN ('leased','running')", node))
	if err == nil {
		// Running tasks stay visible after disablement, so a restarted Agent can
		// reconcile its journal. A lease still requires current permission.
		if t.Status == "leased" {
			if !n.Enabled {
				return nil, tx.Commit(ctx)
			}
			if time.Since(t.CreatedAt) >= 24*time.Hour {
				finishTask(&t, "expired")
			} else if e := remoteAuthorization(ctx, tx, n, t); e != nil {
				if !remoteDenied(e) {
					return nil, e
				}
				finishTask(&t, "cancelled")
			}
			if domain.TaskTerminal(t.Status) {
				if err = writeTask(ctx, tx, &t); err != nil {
					return nil, err
				}
				return nil, tx.Commit(ctx)
			}
		}
		out, e := s.agentTask(ctx, tx, t)
		if e != nil {
			return nil, e
		}
		return &out, tx.Commit(ctx)
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, err
	}
	if !n.Enabled {
		return nil, tx.Commit(ctx)
	}
	blocked, err := updateBlocksNode(ctx, tx, node)
	if err != nil {
		return nil, err
	}
	if blocked {
		return nil, tx.Commit(ctx)
	}
	rows, err := tx.Query(ctx, "SELECT data FROM ctl_remote_tasks WHERE node_id=$1 AND status='queued' ORDER BY created_at,id FOR UPDATE", node)
	if err != nil {
		return nil, err
	}
	queue := []domain.Task{}
	for rows.Next() {
		t, e := scanTask(rows)
		if e != nil {
			rows.Close()
			return nil, e
		}
		queue = append(queue, t)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for _, t = range queue {
		if time.Since(t.CreatedAt) >= 24*time.Hour {
			finishTask(&t, "expired")
		} else if e := remoteAuthorization(ctx, tx, n, t); e != nil {
			if !remoteDenied(e) {
				return nil, e
			}
			finishTask(&t, "cancelled")
		} else {
			t.Status = "leased"
		}
		if err = writeTask(ctx, tx, &t); err != nil {
			return nil, err
		}
		if t.Status == "leased" {
			out, e := s.agentTask(ctx, tx, t)
			if e != nil {
				return nil, e
			}
			return &out, tx.Commit(ctx)
		}
	}
	return nil, tx.Commit(ctx)
}
func remoteDenied(err error) bool {
	return errors.Is(err, domain.ErrForbidden) || errors.Is(err, domain.ErrUnauthorized) || errors.Is(err, domain.ErrNotFound)
}
func (s *Store) StartNodeTask(ctx context.Context, node, id string) (domain.AgentTask, error) {
	var out domain.AgentTask
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	n, err := lockRemoteNode(ctx, tx, node, "UPDATE")
	if err != nil {
		return out, err
	}
	t, err := scanTask(tx.QueryRow(ctx, "SELECT data FROM ctl_remote_tasks WHERE id=$1 AND node_id=$2 FOR UPDATE", id, node))
	if err != nil {
		return out, err
	}
	if !n.Enabled {
		return out, domain.ErrForbidden
	}
	if t.Status != "leased" && t.Status != "running" {
		return out, domain.ErrConflict
	}
	if t.Status == "leased" {
		if time.Since(t.CreatedAt) >= 24*time.Hour {
			finishTask(&t, "expired")
			if err = writeTask(ctx, tx, &t); err != nil {
				return out, err
			}
			if err = tx.Commit(ctx); err != nil {
				return out, err
			}
			return out, domain.ErrConflict
		}
		if err = remoteAuthorization(ctx, tx, n, t); err != nil {
			if !remoteDenied(err) {
				return out, err
			}
			finishTask(&t, "cancelled")
			if e := writeTask(ctx, tx, &t); e != nil {
				return out, e
			}
			if e := tx.Commit(ctx); e != nil {
				return out, e
			}
			return out, domain.ErrForbidden
		}
		t.Status = "running"
		if err = writeTask(ctx, tx, &t); err != nil {
			return out, err
		}
	}
	out, err = s.agentTask(ctx, tx, t)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
func (s *Store) CancelTask(ctx context.Context, project, id string, p auth.Principal) (domain.Task, error) {
	var t domain.Task
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return t, err
	}
	defer tx.Rollback(ctx)
	// Follow the same node -> task lock order as start/report.
	var node string
	if err = tx.QueryRow(ctx, "SELECT node_id FROM ctl_remote_tasks WHERE id=$1 AND project=$2", id, project).Scan(&node); err != nil {
		return t, mapped(err)
	}
	n, err := lockRemoteNode(ctx, tx, node, "UPDATE")
	if err != nil {
		return t, err
	}
	_ = n
	t, err = scanTask(tx.QueryRow(ctx, "SELECT data FROM ctl_remote_tasks WHERE id=$1 FOR UPDATE", id))
	if err != nil {
		return t, err
	}
	current, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", project))
	if err != nil {
		return t, err
	}
	if !p.CanInGroup("resolve", project, current.Group, t.Environment) {
		return t, domain.ErrForbidden
	}
	if t.Status == "cancelled" {
		return t, tx.Commit(ctx)
	}
	if t.Status != "queued" && t.Status != "leased" {
		return t, domain.ErrConflict
	}
	finishTask(&t, "cancelled")
	if err = writeTask(ctx, tx, &t); err != nil {
		return t, err
	}
	if err = audit(ctx, tx, p.ID, "remote.task.cancel", project, t.Environment, nil); err != nil {
		return t, err
	}
	return t, tx.Commit(ctx)
}
func (s *Store) ReportNodeTask(ctx context.Context, node, id string, r domain.TaskReport) (domain.Task, error) {
	var t domain.Task
	if err := r.Validate(); err != nil {
		return t, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return t, err
	}
	defer tx.Rollback(ctx)
	if _, err = lockRemoteNode(ctx, tx, node, "UPDATE"); err != nil {
		return t, err
	}
	t, err = scanTask(tx.QueryRow(ctx, "SELECT data FROM ctl_remote_tasks WHERE id=$1 AND node_id=$2 FOR UPDATE", id, node))
	if err != nil {
		return t, err
	}
	result := &domain.TaskResult{Code: r.Status, Stage: r.Stage, Observation: r.Observation}
	if domain.TaskTerminal(t.Status) {
		a, _ := json.Marshal(t.Result)
		b, _ := json.Marshal(result)
		if t.Status != r.Status || string(a) != string(b) {
			return t, domain.ErrConflict
		}
		return t, tx.Commit(ctx)
	}
	// Recovery of a durable starting journal can report uncertainty after the
	// Agent crashed before receiving start. It never reports success from a lease.
	if t.Status != "running" && (t.Status != "leased" || r.Status != "needs_attention") {
		return t, domain.ErrConflict
	}
	if r.Observation != nil {
		if r.Observation.ID != t.InstanceID || r.Observation.Project != t.Project || r.Observation.Environment != t.Environment {
			return t, domain.ErrForbidden
		}
		if err = observe(ctx, tx, node, *r.Observation); err != nil {
			return t, err
		}
	}
	t.Status = r.Status
	t.Stage = r.Stage
	if t.Status != "running" {
		t.Result = result
	}
	if err = writeTask(ctx, tx, &t); err != nil {
		return t, err
	}
	return t, tx.Commit(ctx)
}

// Material access is restricted to one node's active frozen release. Node
// grants and enabled state are checked again even after a task has started.
func (s *Store) NodeTaskMaterial(ctx context.Context, node, id string) (domain.Release, error) {
	var release domain.Release
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return release, err
	}
	defer tx.Rollback(ctx)
	n, err := lockRemoteNode(ctx, tx, node, "SHARE")
	if err != nil {
		return release, err
	}
	t, err := scanTask(tx.QueryRow(ctx, "SELECT data FROM ctl_remote_tasks WHERE id=$1 AND node_id=$2 FOR SHARE", id, node))
	if err != nil {
		return release, err
	}
	if !n.Enabled || !slices.Contains([]string{"leased", "running"}, t.Status) || t.Action == "rollback" {
		return release, domain.ErrForbidden
	}
	if t.Status == "leased" {
		if time.Since(t.CreatedAt) >= 24*time.Hour {
			return release, domain.ErrConflict
		}
		if err = remoteAuthorization(ctx, tx, n, t); err != nil {
			return release, err
		}
	}
	var encrypted []byte
	if err = tx.QueryRow(ctx, "SELECT ciphertext FROM ctl_remote_tasks WHERE id=$1", id).Scan(&encrypted); err != nil {
		return release, err
	}
	b, err := s.cipher.Open(encrypted, "ctl-remote-task/"+id)
	if err != nil {
		return release, err
	}
	var f frozenTask
	if err = json.Unmarshal(b, &f); err != nil {
		return release, err
	}
	return f.Resolved.Release, tx.Commit(ctx)
}

// WithNodeIdentity binds authentication to the row lock used by a request.
// A request waiting during re-enrollment cannot act using a revoked identity.
type nodeIdentityKey struct{}

func WithNodeIdentity(ctx context.Context, hash string) context.Context {
	return context.WithValue(ctx, nodeIdentityKey{}, hash)
}
func lockRemoteNode(ctx context.Context, tx pgx.Tx, id, mode string) (domain.Node, error) {
	hash, _ := ctx.Value(nodeIdentityKey{}).(string)
	query := "SELECT data,registered FROM ctl_nodes WHERE id=$1 AND ($2='' OR (token_hash=$2 AND registered)) FOR " + mode
	n, err := scanNode(tx.QueryRow(ctx, query, id, hash))
	if hash != "" && errors.Is(err, domain.ErrNotFound) {
		err = domain.ErrUnauthorized
	}
	return n, err
}

// Enrollment maintenance also releases invalid queues on offline/disabled
// nodes. Otherwise an archived project could strand the node permanently.
func (s *Store) invalidateUnstartedTasks(ctx context.Context, tx pgx.Tx, n domain.Node) error {
	// Disablement pauses valid work and is not itself a reason to discard it.
	n.Enabled = true
	rows, err := tx.Query(ctx, "SELECT data FROM ctl_remote_tasks WHERE node_id=$1 AND status IN ('queued','leased') FOR UPDATE", n.ID)
	if err != nil {
		return err
	}
	tasks := []domain.Task{}
	for rows.Next() {
		t, e := scanTask(rows)
		if e != nil {
			rows.Close()
			return e
		}
		tasks = append(tasks, t)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, t := range tasks {
		if time.Since(t.CreatedAt) >= 24*time.Hour {
			finishTask(&t, "expired")
		} else if e := remoteAuthorization(ctx, tx, n, t); e != nil {
			if !remoteDenied(e) {
				return e
			}
			finishTask(&t, "cancelled")
		} else {
			continue
		}
		if err = writeTask(ctx, tx, &t); err != nil {
			return err
		}
	}
	return nil
}
