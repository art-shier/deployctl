# Managed Multi-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manage multiple independently versioned services while preserving ctl publish/install/upgrade and online upgrade entry points.
**Architecture:** Strict source/release manifests render managed Compose. Per-service immutable configuration and a durable operation journal control only selected resources; persistent services require explicit maintenance.
**Tech Stack:** Python, Go, TypeScript, Docker Compose, GitHub Actions.
**Spec:** ../specs/2026-11-15-multi-service-design.md

## Global Constraints
- Single-node Docker Compose; retain old single-service and static behavior.
- No automatic database downgrade, data rollback, orphan deletion or repeated restore.
- Keep existing publish/install/upgrade and online-upgrade entry points.
- Secret mappings are per service; immutable image digests are required.
- Production Lifelog cutover uses a final backup after freezing all writers.

## Review Focus
- Cross-schema rollback or failed install must preserve persistent resource inventory (Task 4).
- Unrelated secret or release-label change must not recreate a database (Task 3).
- Crash after restore side effects must not launch a second writer (Task 5).
- Old agents must reject new packages before side effects (Task 6).
- Dependency cycles, wildcard port collisions and unsafe mounts fail before execution (Task 1).

### Task 1: Strict service source contract
Files: create deployctl/multiservice_contract.py, tests/test_multiservice_contract.py; modify deployctl/contract.py.
Interface: validate_multiservice_deployment(data: dict) -> dict, dependency_order(services: dict, jobs: dict) -> list[str]. Services accept exactly one build/image source; normalize platform, configuration, resources, dependencies, ports and retained volumes.
- [ ] Write failing valid api/postgres normalization and malicious/ambiguous input rejection tests, including cycles, wildcard host bindings and overlapping mounts.
- [ ] Run python -m pytest tests/test_multiservice_contract.py -q and confirm intended failures.
- [ ] Implement independent strict module and version dispatch without modifying legacy normalization.
- [ ] Run new tests and existing contract tests; commit verified implementation.

### Task 2: Immutable multi-image release packaging
Files: deployctl/multiservice_release.py, deployctl/release.py, deployctl/build.py, schemas/deployment.schema.json, schemas/release.schema.json, tests/test_multiservice_release.py, .github/workflows/build-release.yml.
Interface: resolve_services(config: dict, images: dict) -> dict; render_multiservice_compose(release: dict) -> dict. Version 3 release stores service-keyed digests and generated Compose verified on extraction. Preserve legacy package invocation; use keyed resolutions only for new schema.
- [ ] Write failing deterministic package roundtrip, missing/extra resolution, tampered Compose, wrong architecture and legacy fixture tests.
- [ ] Run focused tests to observe failures; implement normalization, archive rendering and CI source/build matrix with existing scoped publisher authentication.
- [ ] Add generic bounded mirror helper with blob/manifest digest checks, external source ownership and credential redirect tests.
- [ ] Run focused packaging/build/schema tests; commit.

### Task 3: Service configuration, inventory and read-only plans
Files: deployctl/service_state.py, deployctl/service_plan.py, deployctl/runtime_snapshot.py, tests/test_service_plan.py.
Interface: plan_services(release: dict, observed: dict, configurations: dict) -> dict; service_fingerprint(spec: dict, values: dict) -> str. Plans classify create/update/unchanged/maintenance-required and volumes create/reuse/missing/unexpected.
- [ ] Write failing API-only image/secret changes keeping database paths/labels/fingerprint identical, old-state compatibility and persistent drift rejection tests.
- [ ] Implement protected immutable service bundles, retained reference inventory and plan/revision checks; no runtime mutation in planning.
- [ ] Run focused tests; commit.

### Task 4: Managed multi-service lifecycle and rollback
Files: deployctl/multiservice_runtime.py, deployctl/runtime.py, deployctl/deployment_state.py, tests/test_multiservice_runtime.py, tests/docker multi-service fixture.
Interface: MultiServiceManager.deploy(application, environment, release, configuration, operation) -> dict; reconcile(operation_id) -> dict. Reuse existing driver subprocess/log/redaction infrastructure.
- [ ] Write failing exact targeted pull/up, dependency health, persistent untouched, rollback-incompatible release and crash-phase tests.
- [ ] Implement app-wide lock, pre-pull, journal-before-action, actual resource identity checks and scoped rollback; never use blanket down/remove-orphans.
- [ ] Verify newly created application services stop on rollback while dependencies and retained volumes survive.
- [ ] Run real Docker api/postgres upgrade and failure injection, assert PostgreSQL container/volume identity and data unchanged; commit.

### Task 5: Restore jobs and maintenance operations
Files: deployctl/service_jobs.py, tests/test_service_jobs.py, Docker restore fixture.
Interface: run_job(operation: dict, job: dict, inventory: dict) -> dict; reconcile_job(execution: dict) -> dict. DAG supports service healthy and job succeeded. Bootstrap success bound to source backup, job digest and volume identity.
- [ ] Write failing job success, active-job reconciliation, crash UNKNOWN, timeout termination, fresh operation against populated target and rollback barrier tests.
- [ ] Implement deterministic job containers, durable exit acknowledgement, protected inputs and explicit recovery/maintenance operations.
- [ ] Run real PostgreSQL isolated restore/verify and missing-volume recovery tests; commit.

### Task 6: Platform validation, capabilities and operations
Files: control-server/internal/artifacts/manifest.go, domain/domain.go, store/node_tasks.go (locate current equivalent), httpapi node/project handlers, deployctl/node_agent.py, deployctl/cli.py; matching tests.
Interface: server validators accept source 2/release 3 only with multi_service_v1 capability; service selector and phase in task/log/heartbeat payloads. Existing install/upgrade dispatch route preserved.
- [ ] Add shared valid/invalid Python/Go fixture corpus and failing old-agent rejection tests.
- [ ] Add per-service desired/observed state, service-keyed configuration overrides and task progress; preserve old fields and routes.
- [ ] Extend CLI with --plan and --service; map --port/--bind to primary endpoint and env overrides to explicit bindings.
- [ ] Run Go artifact/store/httpapi tests and Python CLI/node tests; commit.

### Task 7: Management UI and end-to-end compatibility
Files: control-web/src deployment/instance components and clients (locate existing owners before editing), matching tests; docs and skills/team-deploy.
- [ ] Read relevant frontend skills; add tests for service detail/log selection, change preview and recovery-required state.
- [ ] Implement multi-service details without replacing existing online-upgrade entry point; clearly distinguish application rollback from database maintenance.
- [ ] Run web tests/build and platform online-upgrade E2E against a real Docker multi-service release; old single-service/static regression suites must pass.
- [ ] Update docs/skill contract and commit.

### Task 8: Review, ctl rollout and Lifelog cutover
Files: final review report; Lifelog deployment declaration, release workflow and restore/verify entry points.
- [ ] Independent whole-branch review; fix important findings with regression tests; record all executed and unavailable validation.
- [ ] Prepare reviewed ctl release, confirm actual minimum versions; publish/deploy within existing authorization and verify platform/node capability.
- [ ] Build Lifelog multi-service release, preflight ports/storage/backup and restore candidate in isolation before cutover.
- [ ] Freeze old writers, final backup, isolated restore and canonical data checks, controlled upstream handover; enable one backup owner.
- [ ] Verify application endpoints, new manual backup and isolated restore; retain old volumes and report write-cutover rollback boundary.

## Execution ledger
2026-11-15: Design approved by user '继续'. Existing isolated ctl worktree clean at 3f16662; implementation begins here. Ruling: native execution selected using developer autonomy instruction; no repeated execution-method approval. No production mutations until tested release and concrete cutover preparation.

2026-11-15 implementation checkpoint:
- Source contract validator complete locally: valid normalization, typed DAG and rejection tests (2d620d3).
- Package generation/extraction and JSON schemas implemented (5d2c506); CI/mirror and Go parity still outstanding, so Task 2 is not complete.
- Pure plans and protected stable service bundles implemented (66108c4); manager integration and observed identity reconciliation still outstanding.
- Targeted executor and Docker adapter under development; only fake-driver tests executed, no real Docker evidence yet.
- Focused contracts/package/config tests: 56 passed + 30 subtests. Local Python test env /private/tmp/ctl-multiservice-venv; use TMPDIR=/private/tmp to avoid macOS /var symlink rejection. Broad suite reached 82 passes before pre-existing process-group cleanup PermissionError on macOS; do not alter unrelated signal code to mask environment failure.
- Docker binary unavailable locally; Linux Docker validation required before shipping. No production changes or published version.
- Ruling: planned schema-3 minimum version is 1.20.0 with multi_service_v1 capability; this is a candidate floor, not a released-version claim. Confirm release numbering before publication.
