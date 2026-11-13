CREATE TABLE IF NOT EXISTS ctl_groups (
 slug text PRIMARY KEY, data jsonb NOT NULL
);
INSERT INTO ctl_groups(slug,data) VALUES('default',jsonb_build_object('slug','default','name','default','description','默认项目组','created_at',now())) ON CONFLICT DO NOTHING;
ALTER TABLE ctl_groups ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE TABLE IF NOT EXISTS ctl_projects (
 slug text PRIMARY KEY, data jsonb NOT NULL, stable_version text NOT NULL DEFAULT ''
);
ALTER TABLE ctl_projects ADD COLUMN IF NOT EXISTS group_slug text NOT NULL DEFAULT 'default' REFERENCES ctl_groups(slug);
ALTER TABLE ctl_projects ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
UPDATE ctl_projects SET data=jsonb_set(data,'{group}',to_jsonb(group_slug),true) WHERE data->>'group' IS DISTINCT FROM group_slug;
CREATE INDEX IF NOT EXISTS ctl_projects_group ON ctl_projects(group_slug);
CREATE TABLE IF NOT EXISTS ctl_environments (
 project text NOT NULL REFERENCES ctl_projects(slug), name text NOT NULL,
 current_id text NOT NULL DEFAULT '', revision bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(project,name)
);
CREATE TABLE IF NOT EXISTS ctl_revisions (
 id text PRIMARY KEY, project text NOT NULL REFERENCES ctl_projects(slug), environment text NOT NULL,
 revision bigint NOT NULL, target_version text NOT NULL, ciphertext bytea NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(project,environment,revision)
);
CREATE TABLE IF NOT EXISTS ctl_group_environments (
 group_slug text NOT NULL REFERENCES ctl_groups(slug), name text NOT NULL,
 current_id text NOT NULL DEFAULT '', revision bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(group_slug,name)
);
CREATE TABLE IF NOT EXISTS ctl_group_revisions (
 id text PRIMARY KEY, group_slug text NOT NULL REFERENCES ctl_groups(slug), environment text NOT NULL,
 revision bigint NOT NULL, ciphertext bytea NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(group_slug,environment,revision)
);
CREATE TABLE IF NOT EXISTS ctl_releases (
 id text PRIMARY KEY, project text NOT NULL REFERENCES ctl_projects(slug), version text NOT NULL,
 status text NOT NULL, data jsonb NOT NULL, UNIQUE(project,version)
);
CREATE TABLE IF NOT EXISTS ctl_tokens (
 id text PRIMARY KEY, hash text NOT NULL UNIQUE, project text NOT NULL REFERENCES ctl_projects(slug),
 revoked boolean NOT NULL DEFAULT false, expires_at timestamptz NOT NULL, data jsonb NOT NULL
);
ALTER TABLE ctl_tokens ALTER COLUMN project DROP NOT NULL;
ALTER TABLE ctl_tokens ADD COLUMN IF NOT EXISTS ciphertext bytea;
CREATE TABLE IF NOT EXISTS ctl_sessions (hash text PRIMARY KEY, expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS ctl_receipts (id text PRIMARY KEY, project text NOT NULL REFERENCES ctl_projects(slug), data jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS ctl_audit (id text PRIMARY KEY, project text NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS ctl_audit_recent ON ctl_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS ctl_receipts_project ON ctl_receipts(project,created_at DESC);
CREATE INDEX IF NOT EXISTS ctl_sessions_expiry ON ctl_sessions(expires_at);

-- Remote identities deliberately never enter ctl_tokens or ctl_sessions.
CREATE TABLE IF NOT EXISTS ctl_node_enrollments (
 hash text PRIMARY KEY, expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS ctl_nodes (
 id text PRIMARY KEY, data jsonb NOT NULL, token_hash text UNIQUE,
 enrollment_hash text UNIQUE, enrollment_expires timestamptz,
 registered boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS ctl_instances (
 id text PRIMARY KEY, node_id text NOT NULL REFERENCES ctl_nodes(id),
 project text NOT NULL REFERENCES ctl_projects(slug), environment text NOT NULL,
 data jsonb NOT NULL, UNIQUE(node_id,project,environment)
);
CREATE TABLE IF NOT EXISTS ctl_remote_tasks (
 id text PRIMARY KEY, instance_id text NOT NULL REFERENCES ctl_instances(id),
 node_id text NOT NULL REFERENCES ctl_nodes(id), project text NOT NULL REFERENCES ctl_projects(slug),
 actor text NOT NULL, request_id text NOT NULL, request_hash text NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','leased','running','succeeded','failed','needs_attention','cancelled','expired')),
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 data jsonb NOT NULL, ciphertext bytea NOT NULL, UNIQUE(actor,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ctl_remote_instance_active ON ctl_remote_tasks(instance_id) WHERE status IN ('queued','leased','running');
CREATE UNIQUE INDEX IF NOT EXISTS ctl_remote_node_claimed ON ctl_remote_tasks(node_id) WHERE status IN ('leased','running');
CREATE INDEX IF NOT EXISTS ctl_remote_node_queue ON ctl_remote_tasks(node_id,created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS ctl_remote_project_recent ON ctl_remote_tasks(project,created_at DESC);

-- Updater identities are separate from platform and Agent credentials.
CREATE TABLE IF NOT EXISTS ctl_update_enrollments (
 hash text PRIMARY KEY, kind text NOT NULL, target_id text NOT NULL,
 expires_at timestamptz NOT NULL, UNIQUE(kind,target_id)
);
CREATE TABLE IF NOT EXISTS ctl_updaters (
 id text PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('node','center')),
 target_id text NOT NULL, token_hash text NOT NULL UNIQUE, data jsonb NOT NULL,
 UNIQUE(kind,target_id)
);
CREATE TABLE IF NOT EXISTS ctl_update_requests (
 request_id text PRIMARY KEY, request_hash text NOT NULL, batch_id text NOT NULL
);
CREATE TABLE IF NOT EXISTS ctl_update_jobs (
 id text PRIMARY KEY, request_id text NOT NULL REFERENCES ctl_update_requests(request_id),
 updater_id text NOT NULL REFERENCES ctl_updaters(id), kind text NOT NULL,
 target_id text NOT NULL, batch_id text NOT NULL, status text NOT NULL,
 created_at timestamptz NOT NULL, data jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ctl_update_updater_active ON ctl_update_jobs(updater_id) WHERE status IN ('queued','leased','running');
CREATE INDEX IF NOT EXISTS ctl_update_jobs_recent ON ctl_update_jobs(created_at DESC);

-- Release registration is independent of project publication and deployment credentials.
CREATE TABLE IF NOT EXISTS ctl_system_releases (
 version text PRIMARY KEY, data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS ctl_release_registration_credential (
 singleton boolean PRIMARY KEY CHECK(singleton), token_hash text NOT NULL, created_at timestamptz NOT NULL
);

-- SSH credentials are deliberately absent: workers retain them only in memory.
CREATE TABLE IF NOT EXISTS ctl_node_installations (
  id text PRIMARY KEY,
  host text NOT NULL,
  port integer NOT NULL,
  username text NOT NULL,
  fingerprint text NOT NULL,
  actor text NOT NULL,
  status text NOT NULL,
  stage text NOT NULL,
  error_code text NOT NULL DEFAULT '',
  node_id text NOT NULL DEFAULT '',
  enrollment_hash text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ctl_node_installations_active_host
  ON ctl_node_installations(host,port) WHERE status='running';
