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

2026-11-15 continuation checkpoint:
- Connected package CLI to schema-2 multi-service image resolutions using repeated --image NAME=DIGEST; legacy single-image syntax preserved. Duplicate or incomplete mappings fail before archive creation. CLI RED: 2 failed/4 passed; GREEN: 6 passed.
- Fixed restore reconciliation resetting timeout after agent restart. Deadline is now persisted before starting the writer; active jobs with no valid deadline are stopped and require recovery. Completed exit status still reconciles without repeating work. Deadline RED: 2 failed/5 passed; GREEN included below.
- Combined focused verification: 81 passed + 30 subtests (CLI, runtime CLI, contracts, release/schema, service plans/config, executor, Docker adapter and job reconciliation); git diff --check clean.
- Still NOT ready to publish: Go platform schema-3 validation, Manager/Agent integration, build/mirror CI and real Docker tests remain. Docker executable unavailable on this host. No production changes.

2026-11-15 platform/protocol checkpoint:
- Added Go release-schema-3 validation and managed Compose rendering, verified against two deterministic Python-produced package fixtures (API/PostgreSQL and API/PostgreSQL/bootstrap job). Rejects untrusted fields, mutable images, unsafe/overlapping mounts, typed-DAG errors, port collisions, capability mismatch, and Compose tampering.
- Added versioned image inventories to release metadata. Publication verifies every unique service/job digest under the existing project repository scope; immutable-version retries compare the whole inventory. Static and legacy release behavior retained.
- Added explicit node capability heartbeat persistence. New task creation, queue/lease dispatch and start recheck both version and capability; tests cover downgrade while queued and after leasing. Existing Agent does NOT advertise multi_service_v1 yet: runtime Manager integration is still outstanding.
- Resolution schema 3 preserves public config/secret classification and frozen revisions. Python validates all service/job digests on download and supports immutable artifact recovery without production configuration access. Agent registry auth now handles multi-service image inventories.
- Real Go HTTP + disposable PostgreSQL + Python protocol integration passed: publish, resolve, download, recover, missing-capability rejection, frozen secrets, node task validation, task-scoped package/registry access. This test intentionally does not execute Docker.
- Found and fixed a canonicalization bug: unsorted multiple endpoints produced a package that failed its own Compose comparison after YAML key sorting. Regression observed RED then GREEN.
- Verification: Python focused suite 133 passed + 30 subtests. Go all-package suite with CTL_TEST_DATABASE_URL and CTL_TEST_PYTHON: 286 test/subtest passes, 2 explicitly skipped external-registry/Docker cases; 10 packages passed, 1 package has no tests. Legacy Agent lifecycle and tool self-update protocol integration passed. Final artifact tests rerun after regex allocation cleanup.
- Test PostgreSQL uses an isolated private Unix socket and disposable databases, never ctl/Lifelog production. Initial macOS Go HTTP resource tests required TMPDIR=/private/tmp to avoid the known /var symlink restriction; rerun passed.
- Ruling: registry scope stays one project repository for all service/job images, matching the existing scoped-token model; external images must be mirrored before platform publication. No additional broad registry credential is introduced.
- Still pending: Runtime Manager/CLI deployment and observation integration, job/maintenance execution, generic CI build/mirror flow, service UI, real Docker lifecycle/recovery validation, final review and production rollout/cutover. No production changes or published version.

2026-11-15 runtime integration checkpoint:
- Connected schema-3 packages to existing Manager, CLI install/upgrade/rollback/status/logs/stop/restart and Agent execution/observation. Legacy paths retained; installed legacy instances cannot silently adopt a stack or existing volumes.
- Added content-addressed private stack snapshots, stable per-service bundles, container/config/mount/network/volume identity checks, targeted app rollback, and durable pending transactions. Stopped persistent services use start rather than recreation. App stop/restart excludes persistent services.
- Added bootstrap job selection and durable runner integration. Completed restore is bound to target volume identities and skipped across app releases without old restore credentials. Data-changing jobs require fresh target volumes and caller-supplied source SHA256; actual restore program must still verify bytes and restored data. Failed jobs retain a recovery barrier.
- Added optional install/upgrade --plan (read-only JSON; no registry login or deployment receipt), --job-source NAME=SHA256 and --run-job NAME. Ordinary command syntax unchanged. Remote task forwarding of job inputs remains outstanding.
- Fixed secret override classification when the management revision removes a formerly secret key; persisted secret overrides stay in the private secret pool. Fixed legacy release matching when schema_version is absent.
- Latest focused runtime/CLI/Agent/progress verification: 100 passed + 5 subtests. Broader suite before the final secret/CLI additions: 629 passed + 171 subtests, 1 skipped; four environment-related failures remain (three macOS process-group PermissionErrors and one GNU-vs-BSD tar fixture). Final changed paths covered by focused rerun; git diff --check clean.
- Tests use simulated Docker with real Manager/filesystem/planner/executor/job journal. Docker is unavailable locally; no real Docker or production claim. Persistent maintenance, full recovery reconciliation/state hardening, remote job inputs, CI build/mirror, UI and Linux Docker lifecycle verification remain outstanding. Capability advertisement and release version are intentionally not enabled yet.

2026-11-16 remote jobs and Linux validation checkpoint:
- Added bounded job_sources/requested_jobs to the server's frozen task request and Agent lease. Invalid names/digests, duplicate selections, rollback inputs and legacy-release job inputs are rejected. Changed backup identity on an idempotent retry conflicts; Agent task fingerprints include nonempty job inputs while preserving old pending-task fingerprints.
- Agent passes verified job inputs into Manager; integration fixture runs bootstrap through RuntimeExecutor. Python selected multi-service/runtime/Agent suite: 151 passed. Go all-package test run with disposable local PostgreSQL and Python protocol client passed (10 packages, one without tests); database shut down after tests. No production database access.
- Added scripts/multiservice_docker_integration.py to Linux Docker CI: checks actual PostgreSQL container ID, mount paths, volume identity and SQL sentinel through application upgrade, rollback, failing upgrade and stop/restart. New pytest-style multi-service tests are now explicitly included in Linux unit CI (unittest discovery alone did not execute them).
- Docker fixture syntax and deployment schemas validated locally; real Docker execution is still pending CI. No release version or Agent multi_service_v1 advertisement enabled.

2026-11-16 Linux CI evidence (tested commit d93d8a140e12cddcae97a8be2030b9e5c9a38b01):
- Pushed isolated validation branch codex/multi-service-runtime-validation; no production deployment, merge or release tag.
- Deployment toolkit CI passed all jobs: Linux Python 3.10/3.12, Windows Python 3.12, legacy real Docker lifecycle, new retained-PostgreSQL lifecycle, cold/cached pull progress and real build-argument transport. https://github.com/art-shier/deployctl/actions/runs/43792975856
- Control-plane CI passed: real systemd Agent/updater recovery, server installation/update, PostgreSQL transactions with Go race detector and vet, management UI build/tests, browser against real API, managed static and authenticated Docker/Agent deployment compatibility. https://github.com/art-shier/deployctl/actions/runs/43792975820
- First Docker attempt failed because unordered Docker Mounts enumeration was compared as a list. Normalized only list order by Destination while preserving full mount-record equality and printing differences; rerun passed. Database container IDs, mount fields, volume metadata and actual SQL data verified unchanged through upgrade, rollback, failing upgrade, stop and restart.
- This is lifecycle validation, not complete release readiness. Still required: restore job real-Docker and interruption/reconciliation coverage, explicit persistent maintenance/recovery, state hardening, multi-image build/mirror workflow, service management UI, independent review and final Lifelog backup/restore/cutover. Agent capability advertisement remains disabled until those are ready.

2026-11-16 interrupted restore recovery implementation:
- Added explicit `ctl recover APP --env ENV --operation ID` for the original pending stack transaction. Reconciliation validates snapshots, source execution identity and target volumes, inspects the original job container and never creates a replacement attempt. Missing/failed/unknown jobs remain blocked. Unstarted jobs require separate verified recovery.
- Recovery retains the data-change rollback barrier while completing application startup. A subsequent app failure cannot erase that barrier or authorize an automatic rollback.
- RED/GREEN found that a healthy database identity was not durably captured before starting a logical restore. Added inventory checkpoint after each successful service health check so abrupt process loss does not leave that known database unregistered.
- Added Linux fixture for real pg_dump source checksum validation, empty-target validation, restore/data checks, process exit before durable success acknowledgement, explicit recovery without repeated import and subsequent upgrade without backup credentials. Real Docker result pending CI; Python focused result recorded separately after completion.

2026-11-16 recovery verification checkpoint (46b4a2115fe245b45ff6aba49a85e7f26fc5ed2f):
- Hardened persisted network/volume/job/transaction fields and capped state reads at 4 MiB. Malformed inventory is rejected before recovery; local combined focused suite: 168 passed + 5 subtests.
- Real Docker initially exposed a restore fixture permission error: restricted container root could not read a 600 file owned by the nonroot CI host user. Declared the existing database-init permission profile for the PostgreSQL restore task; retained 600 host files and read-only mounts. No permission checks were removed.
- Deployment-toolkit CI fully passed: https://github.com/art-shier/deployctl/actions/runs/43796037456 . Logs confirm real pg_dump restore, abrupt process exit before success acknowledgement, original-container reconciliation without a second import, app readiness, and upgrade after removing backup credentials. Existing retained-DB and legacy Docker checks also passed.
- Operator scope and limitations documented in docs/multi-service-recovery.md. Missing-volume candidate restoration, persistent maintenance, multi-image build/mirror and service UI remain outstanding; production rollout and Lifelog cutover have not occurred.
- Control-plane CI also fully passed for the same commit: https://github.com/art-shier/deployctl/actions/runs/43796037453 (systemd updater recovery, real PostgreSQL/race/vet, UI/browser, static and authenticated Docker/Agent deployment).

2026-11-16 multi-image delivery implementation:
- Added same-repository component build plans, strict in-project build paths, per-service literal build args and deterministic bounded component tags. Identical build/image sources are delivered once and referenced by every applicable service/job.
- Added bounded manifest-tree validation (raw SHA256, descriptor sizes, 32 manifests, depth 2, 256 layers per manifest, 4 GiB unique blobs) and platform checks before mirroring. Foreign blob URLs are rejected. Skopeo performs authenticated transport/blob verification with --all --preserve-digests; copy-result and destination manifest digests are rechecked.
- Ruling: use the maintained Skopeo registry client for credential routing and digest-preserving blob transport rather than implementing another HTTP registry/token client. Source/destination credentials are resolved separately from the existing Docker credential file. TLS is forced on except explicit loopback-only HTTP in disposable integration fixtures; credentials are never command arguments.
- Existing build-release.yml preserves the legacy Docker action and package path. Schema-2 sources use the new build/mirror path and expose a keyed `images` output; existing single-image output remains unchanged for old callers. Ambiguous global build-args overrides are rejected for schema 2; use each service's build.args.
- Added real Linux fixture with separately authenticated source/target registries, literal build args, digest-preserved mirror, local blob SHA verification and complete schema-3 package extraction. CI result pending. No production or version changes.

2026-11-16 multi-image delivery verification (d8c9c320d06bf3e5853395a52bab24cce811aba1):
- Deployment-toolkit CI passed all jobs: https://github.com/art-shier/deployctl/actions/runs/43800152430 . The new real fixture built an application image with literal build arguments, authenticated separately to source/target registries, preserved mirror manifest digests, verified exported layer/config SHA256 and extracted the complete schema-3 package. PostgreSQL retention/interrupted restore and legacy Docker tests also passed.
- Control-plane CI fully passed: https://github.com/art-shier/deployctl/actions/runs/43800152492 (systemd updater recovery, PostgreSQL/race/vet, UI/browser and authenticated managed deployment compatibility).
- Local packaging/build/legacy focused verification: 85 passed + 30 subtests. Added two further entrypoint tests proving that mirror failure emits neither package nor workflow output and success emits matching package/inventory; delivery tests now 12 passed. Operator/build contract documented in docs/multi-service-delivery.md.
- Remaining release gates: explicit persistent maintenance and missing-volume candidate restoration, service observations/log selection/preview UI, whole-branch review and actual ctl release/rollout followed by Lifelog freeze/restore/cutover. Current source version and advertised capabilities remain unchanged; no production deployments occurred.

2026-11-16 persistent maintenance and isolated restore implementation (validation pending):
- Added explicit maintain with selected persistent services, backup SHA and reviewed plan hash. Applications quiesce before database configuration changes; image/volume migration remains isolated-restore-only. Failed maintenance retains the data barrier and supports reconciliation.
- Added generation-bound private snapshots/config bundles and resource namespaces. Upgrades preserve the active generation; rollback across generations is prohibited.
- Added restore preview/apply with frozen source checksums and reviewed plan hash. Pulls precede source quiescence, existing writers are identity-checked and stopped, old containers/volumes are retained, candidate state is separate, and promotion requires completed restoration jobs and healthy services. Original-operation recover resumes an interrupted freeze or reconciles the candidate transaction.
- Restore declarations must cover persistent volumes and gate applications behind restoration jobs. Missing source resources are allowed; changed identities are rejected. No automatic source fallback after new data writes.
- Local focused runtime/snapshot/maintenance/restoration tests: 81 passed. Full Python regression and real Linux maintenance/lost-volume restore integration pending. No production or advertised capability changes.

2026-11-16 restoration CI and service visibility:
- Commit 771f6c3 Linux/Windows Python jobs all passed; control-plane workflow 43816835413 fully passed. Real Docker logs in workflow 43816835375 confirm lost-volume isolated restore, retained source resources, cross-generation rollback rejection and next-upgrade database retention. The final maintenance fixture failed because it requested the existing default 512m and incorrectly expected container recreation; changed to 768m and assert actual HostConfig memory. Full rerun required.
- Added bounded, secret-free per-service/job observations and recovery operation status through Agent/Go protocol. Runtime logs select registered services/jobs with snapshot/identity verification and redact their snapshot secrets. Legacy app log defaults remain compatible.
- Management UI reuses current instance table, native disclosure/select and log dialog; displays service lifecycle/version/health, job states, recovery guidance and component image inventory. Current runtime service selection is omitted from archive/deployment queries.
- Local checks: focused runtime/Agent/log tests 85 passed before adding an additional before-job interruption case (restore tests now 5 passed); Go domain/httpapi passed; UI 43 unit tests and production build passed; 13 browser deployment/log regressions passed, plus narrow multi-service screenshot check.
- Premium static audit reports pre-existing whole-product native-control ownership/form/textarea gaps (25 findings, /private/tmp/ctl-multiservice-ui-audit.json); added scoped UX contract without claiming whole-product compliance. Production deployment remains outstanding.

2026-11-16 whole-branch review fixes in progress:
- Independent reviewer found three Important issues: hard-crash gaps after Docker resource/container creation before inventory save; rollback of a successful release adding an app service; candidate reservation before source restoration intent. Reproduced all relevant durable-state windows with tests and implemented journal-bound reconciliation, journaled retirement of added application services, and source intent before candidate-state creation. Retained services stay stopped on ordinary restart and can be re-enabled by a subsequent declaring release.
- Added real Linux fork/os._exit fixtures for up-before-checkpoint and resources-before-checkpoint; added worker rollback/re-enable coverage. Runtime tests model actual durable disk state rather than relying on exception cleanup.
- Added service-build-args JSON reusable input for explicitly scoped dynamic ordinary values needed by Lifelog release metadata; legacy build-args unchanged. Focused verification: 122 passed + 25 subtests (before final additional rollback phase handling); runtime/restore latest 44 passed. Real CI rerun required.

2026-11-16 candidate v1.20.0 preparation:
- Latest official release checked: v1.19.5. Candidate source/package version now 1.20.0; Agent heartbeat advertises multi_service_v1. No release tag or production rollout yet.
- Independent focused re-review e6e34c0: all Important findings resolved, 95 tests passed, CI remains a release gate. Full review evidence in reports/2026-11-16-multi-service-review.md.
- CI817e4de exposed an actual recovery timing case: the replaced container is still starting when recovery replans. Reconciliation now waits for the intended running container's health before planning, with delayed-health regression. The control-plane run separately failed building the public example Docker image; bounded nonsecret build diagnostics added for the next run, root cause not yet established.
- Added real remote schema-3 Agent install/upgrade/rollback fixture with capability heartbeat, per-service health, stable persistent container/data and selected-service HTTP log relay. Updated distributed skill's mechanical version/protocol/command reference.
- Local full suite: 692 passed, 171 subtests passed, one skipped, four known macOS-specific tests deselected (signal-group permissions and GNU tar assumptions). Linux CI runs those tests. Latest delayed-health cases: 5 passed. Final candidate CI pending.

2026-11-16 candidate d6e89f7 acceptance and legacy cutover closure:
- Both full CI workflows passed: toolkit 43821369515 and control-plane 43821369508. Includes actual post-up/pre-checkpoint hard crash, resource creation interruption, retirement/re-enable, retained PostgreSQL maintenance/restoration and remote schema-3 install/upgrade/rollback/log relay.
- Added explicit stopped-legacy restore to preserve the original app/environment/public binding for Lifelog. Old snapshot/container/volume identities are bound to the reviewed plan; source audit precedes durable parent journal, new generation restores independently, old source remains stopped/retained and no cross-schema previous rollback is created.
- Independent review: no blockers, 52 related tests passed. Parent focused runtime/restore/Docker-contract suite: 68 passed. Added real Docker legacy cutover and subsequent upgrade fixture; final rerun required before release.
