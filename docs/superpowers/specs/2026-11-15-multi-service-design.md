# ctl multi-service deployment design

Status: service/data responsibility revised by the user on 2026-11-16. Data-recovery orchestration in the original text below is superseded by plans/2026-11-16-service-data-boundary.md. Revision implementation and re-verification are in progress; not released.

## Agreed scope
One application/environment/node owns multiple services with independent images. Keep existing publish, install, upgrade and online-upgrade entry points; preserve old single-service and static deployments. Lifelog will be redeployed with a new database volume restored from a final frozen-source backup, rather than adopting the existing companion container. First release is single-node Docker Compose; no cross-node scheduling or database HA.

## Alternatives
Bundling PostgreSQL into the application container couples lifecycle and is rejected. Project hooks are the current workaround but hide service state and repeat orchestration. Selected direction is a ctl-validated service manifest rendered into Compose, retaining platform ownership and integrity checks; arbitrary user Compose is not accepted.

## Baseline implementation evidence (before this change)
`deployctl/contract.py` accepts deployment schema 1 and release schemas 1/2. `release.py:render_compose` emits one app service. `runtime.py:DockerDriver` hardcodes app in pull/up/inspect/logs/operations and uses remove-orphans/down. `runtime_snapshot.py` creates one shared configuration mount. Go artifact validation independently checks release contracts. All must change together; a CLI-only implementation is insufficient.

## Contracts and packaging
Introduce deployment schema 2 and release schema 3, leaving old parsers/renderers and package bytes supported. A release contains named services, digest-pinned images, commands, explicit configuration/secret mappings, resource limits, health checks, endpoint bindings, volume declarations and dependency DAG. Jobs are separately named one-shot containers, never restartable services. Reject cycles, unknown references, duplicate ports, unsupported architectures, invalid paths and unsupported fields before mutation. Image references in published releases must be immutable; build inputs do not reach runtime. Preserve deterministic packaging, checksum verification and generated-Compose equality validation.

Each service declares lifecycle application or persistent. First version requires explicit health and startup timeout for dependent services; HTTP checks or container exec checks are supported. Only declared endpoints bind host ports; database defaults to internal application network. Secret files are mounted only into authorized services/jobs. Environment mappings are explicit, not full project-secret inheritance. Enforce bounded resource settings and approved container permissions; official database initialization may require a documented restricted permission profile rather than blindly inheriting app cap_drop ALL.

CI builds configured application images, resolves external images to platform-compatible digests, and optionally mirrors them into the existing project registry. All images and the release package must be verified before publishing the version as deployable. Reuse repository-scoped short-lived registry credentials. Do not put publisher credentials in node snapshots. Generic mirroring must support bounded uploads and layer/manifest integrity checks.

## State, configuration and compatibility
Keep application instance as the deployment unit. Add per-service desired/applied digest, configuration fingerprint, container identity, health and last action; add separately tracked persistent volume identities and job executions. Secret snapshots remain protected; public state contains identifiers/hashes, not secret values. Preserve old state and upgrade it additively; never infer a persistent volume from a container name alone.

A service configuration fingerprint includes only its effective settings, image and mount/network bindings. Unchanged persistent services must not receive new release/config labels that trigger recreation. Record application release membership separately from container creation release.

Old releases internally map to app but use the legacy behavior where required. Static deployment stays separate. Agent capability heartbeat and server task preflight require multi-service schema support. CLI/node locally reject unsupported schemas before hooks or changes; the server refuses dispatch to incapable nodes. Minimum supported versions are set at release time, not invented now. Upgrading ctl itself continues via its existing updater.

## Deployment plan and execution
The existing install/upgrade entry points compute a validated plan; optional --plan is read-only. Classify each service create/update/unchanged/maintenance-required and volumes create/reuse/missing/unexpected. Acquire the application environment lock across services and jobs. Recheck configuration revision and observed resource identity at apply time.

Pull all required images before disturbing running services. Ensure network and retained volumes, start missing dependencies, wait for health, execute authorized one-shot initialization jobs, then update application services in dependency order and verify actual digest/config and health. First version replaces services in place and may cause short downtime; no atomic multi-service or zero-downtime guarantee.

Ordinary upgrade leaves persistent services untouched. Any persistent service image/config/mount change is maintenance-required and rejected before mutation, even when embedded in a normal release. Explicit maintenance runs under the same lock with a reviewed plan; no automatic database major-version migration. Changes to service names or deletion require an explicit removal plan; omit --remove-orphans and blanket compose down in multi-service paths. Application upgrades target exact services with dependency recreation disabled after readiness has been checked.

## Failure, recovery and jobs
Persist a transaction journal before each side effect with old/new per-service snapshot references and resource identities. Resume inspects actual state before acting. Record partial application and failed recovery, retain pending state, and block unrelated upgrades. Roll back only changed application services to their previous digests/configurations. Never automatically downgrade databases, reverse data migrations, delete retained volumes or stop pre-existing dependencies. New dependencies and volumes may remain after a failed install and are reported for deliberate cleanup.

Jobs declare dependencies, timeout, resource/secret scope and triggering policy. Execution identity binds application/environment, immutable job command/image/configuration and target resource identities. Journal successful bootstrap jobs so routine upgrades do not repeat them. Unknown outcomes are not automatically retried: reconcile the original container or require project-specific investigation. Generic jobs may be volumeless or access existing service resources. ctl does not require a backup identifier, infer an empty-target policy or determine logical data validity.

If an established volume disappears, prohibit silently starting an empty replacement and report the missing managed resource. Recovery of its data belongs to the project, using its own script and validation. ctl has no data restore command. `recover` reconciles an interrupted deployment transaction only.

## Commands and management API
Keep publish/install/upgrade/rollback entry points. Status returns application summary and service details. Add optional service selector to logs/restart/stop; no-selector legacy single-service behavior is unchanged. For multi-service applications, logs aggregate with service labels, status lists all, restart defaults to application-lifecycle services; stopping persistent services requires explicit service selection. No-selector semantics must be documented identically in CLI and management UI. Persistent-service configuration maintenance and explicit job selection are deployment operations; they carry no backup policy or checksum attestation.

Server stores versioned multi-service release metadata and service observations; task progress/logs include service/job and phase. UI supports release change preview, service health/logs and pending-recovery reporting. Existing online-upgrade action dispatches the same application deployment transaction. Before task acceptance validate node capability, image access, configuration, port conflicts and persistent maintenance requirements.

## Lifelog migration
Publish a new multi-service Lifelog release: api, postgres, isolated restore and verify jobs. Remove container orchestration from pre-install hooks only after multi-service release validation. Keep application-specific restore/verification implementation. Create a fresh database volume; preserve old database and volume.

Preflight exact runtime images, storage capacity, secrets, schema compatibility, data integrity checks and port handover. Freeze all writers on the old database, create a fresh final backup, verify uploaded archive and restore it to the new isolated database. Compare table counts and canonical data fingerprints excluding explicitly documented operational metadata. The Lifelog operator script stops the old application and invokes ordinary upgrade for the layout change. Keep the new API in maintenance mode and its schedulers disabled while checking readiness and data. Enable business writes and backups only after project validation. Before writes, rollback may reopen the frozen source; after new writes, do not redirect to stale data: use forward repair or reconciled migration. Record cutover checkpoint and rollback boundary durably.

Backup schedules must have a single active owner. Validate manual backup and a real isolated restore of the new deployment. Observe the next daily backup separately; do not claim it passed in advance. Cleanup of old volumes is a separate explicit action after retention, never part of deployment success.

## Acceptance
Old single-service/static packages and commands pass existing regressions. Python/Go contract fixtures agree. Real Docker exercises api+postgres, API-only upgrades leaving DB container ID unchanged, secret isolation, retained data across upgrade, health dependency failure, image pull failure before mutation, partial rollback, crash/restart reconciliation, generic job UNKNOWN state, missing-volume refusal, service removal protection and old-agent rejection. Control-plane online upgrade and CLI installation must exercise the same multi-service release. Lifelog frozen-source restore and post-cutover backup restore are verified with recorded evidence. No performance improvement is claimed from this deployment refactor.

## Delivery sequence
1. Versioned contracts, packaging and capability checks.
2. Per-service config/state, plans and real Docker lifecycle.
3. Journal/recovery/jobs and maintenance boundaries.
4. Server/UI/CLI, CI multi-image delivery and compatibility tests.
5. Release ctl, upgrade platform/node through existing updater, then separately deploy Lifelog and verify restoration.

## Builder-check refinements
- Once an installation has managed persistent services, a release-v1/v2 rollback must never enter legacy blanket down/remove-orphans code. For first release, reject cross-schema rollback before mutation and provide a forward-compatible single-application-service release retaining persistent declarations. Legacy-only installations retain original rollback behavior.
- Keep immutable per-service configuration bundles keyed by the service fingerprint, independent of application release IDs. Reuse unchanged mount source paths, secret versions and Compose-visible labels; do not merely compare hashes while rendering new paths. Garbage collection retains every active/rollback/journal reference.
- UNKNOWN jobs may still be running. Reconcile job container identity and exclusivity, terminate and verify termination when appropriate, then inspect target volume before any resume or project-directed replacement. Never create a second writer to the same volume.
- Preserve --port/--bind by mapping them to an explicitly designated primary endpoint; multi-endpoint deployments without one reject those flags as ambiguous. Existing --env-var/--unset-env operate on the project key store, with explicit per-service references determining recipients. --set remains invocation-local job/hook input, not an implicit secret broadcast.
- Orchestration safety is selected from instance resource history, not only target release schema. Even rejected cross-schema rollback and failed bootstrap cleanup must preserve persistent resources. Acceptance covers legacy-to-multi-service and attempted legacy rollback.
- Jobs join the dependency DAG with service healthy and job succeeded edges. Container names deterministically bind execution identity; preserve exit status until journal acknowledgement. Successful bootstrap is also recorded against target volume identity, so a new operation ID cannot silently repeat an already completed bootstrap task. Cancellation/timeout stops and verifies all owned job writers before unlocking.
- Data-changing jobs declare rollback compatibility with the prior application version. Default is incompatible. Successful or UNKNOWN incompatible jobs prohibit automatic application rollback and require recovery; the platform does not assume old binaries understand a new schema.
- State distinguishes requested release, last fully successful release, actual per-service versions and retained resources. Rollback stops newly created application services, restores changed old application services, and retains dependencies/volumes with explicit ownership. A rollback never erases the actual resource inventory.

## Source contract example and build compatibility
Illustrative normative field structure (digests shown as placeholders only):

```yaml
schema_version: 2
application: lifelog
platform: linux/amd64
primary_endpoint: api.http
services:
  api:
    lifecycle: application
    build:
      context: .
      dockerfile: apps/server/Dockerfile
      args: {}
    depends_on:
      postgres: healthy
    environment:
      DB_HOST: {literal: postgres}
      DB_PASSWORD: {secret: business_db_password}
    endpoints:
      http: {container_port: 8080, host_port: 8081, bind_address: 127.0.0.1}
    resources: {memory_limit: 512m, cpus: 1}
    health: {http: /health/ready, startup_timeout_seconds: 120}
  postgres:
    lifecycle: persistent
    image: docker.io/library/postgres@sha256:<official-platform-digest>
    mirror: project
    environment:
      POSTGRES_PASSWORD_FILE: {literal: /run/secrets/pg-password}
    secret_files:
      pg_admin_password: /run/secrets/pg-password
    volumes:
      data: /var/lib/postgresql
    resources: {memory_limit: 768m, cpus: 0.75}
    health: {exec: [pg_isready, -U, postgres], startup_timeout_seconds: 120}
volumes:
  data: {retain: true}
```

Exactly one of build or image per service; build supports context, dockerfile and explicit args using current validation rules. The initial platform is one platform per release, verified against node architecture. Packaging resolves every build/external image into a digest mapping and strips build inputs. Existing single-image package/build inputs remain for schema 1; schema 2 uses service-keyed image resolutions and rejects missing/extra keys. Publish continues accepting a standard package and uploading it; it validates all referenced project-registry manifests before marking the release deployable. Existing project-wide resource overrides map only to the primary service for schema 2; new overrides are service-keyed, with ambiguous collisions rejected rather than silently applied.

The bootstrap operation adds restore/verify job dependencies to the plan: restore depends on postgres healthy; verify depends on restore succeeded; api activation depends on verify succeeded. Ordinary upgrades use persisted bootstrap success for that same target volume, not a newly rerun job. Database initialization/migration tasks are separately versioned and cannot silently appear in an ordinary application release without an explicit operation plan.
