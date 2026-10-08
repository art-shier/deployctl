CREATE TABLE IF NOT EXISTS ctl_projects (
 slug text PRIMARY KEY, data jsonb NOT NULL, stable_version text NOT NULL DEFAULT ''
);
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
CREATE TABLE IF NOT EXISTS ctl_releases (
 id text PRIMARY KEY, project text NOT NULL REFERENCES ctl_projects(slug), version text NOT NULL,
 status text NOT NULL, data jsonb NOT NULL, UNIQUE(project,version)
);
CREATE TABLE IF NOT EXISTS ctl_tokens (
 id text PRIMARY KEY, hash text NOT NULL UNIQUE, project text NOT NULL REFERENCES ctl_projects(slug),
 revoked boolean NOT NULL DEFAULT false, expires_at timestamptz NOT NULL, data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS ctl_sessions (hash text PRIMARY KEY, expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS ctl_receipts (id text PRIMARY KEY, project text NOT NULL REFERENCES ctl_projects(slug), data jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS ctl_audit (id text PRIMARY KEY, project text NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS ctl_audit_recent ON ctl_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS ctl_receipts_project ON ctl_receipts(project,created_at DESC);
CREATE INDEX IF NOT EXISTS ctl_sessions_expiry ON ctl_sessions(expires_at);
