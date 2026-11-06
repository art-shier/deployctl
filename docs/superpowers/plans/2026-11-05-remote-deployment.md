# Remote Deployment Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded components and independent review. Track checks below. User has authorized development after the design; no additional approval gate.

**Goal:** Deliver node registration/authorization, durable frozen deployment tasks, a Linux outbound Agent and Web deployment workflow supporting one project/environment across multiple nodes.

**Architecture:** Extend the existing Go/PostgreSQL control plane and React management UI. A Python Agent uses the existing Manager runtime, private durable journals and HTTPS polling. Manual load balancing remains external.

**Tech Stack:** Go, PostgreSQL/pgx, Python >=3.10 standard library plus existing dependencies, React/TypeScript/Vite.

**Spec:** ../specs/2026-11-05-remote-deployment-design.md

## Global Constraints

- Preserve existing CLI paths and behavior; one instance per node/project/environment.
- Only install/upgrade/rollback; no arbitrary remote shell, automatic LB or untrusted tenant isolation.
- Node identity separate from user credentials; never expose frozen configuration in management responses.
- Freeze effective group/project config and release when creating a task; recheck authorization before execution.
- Uncertain execution is needs_attention, never automatic replay of hooks.
- Implement in isolated codex/remote-deploy branch. Do not publish or deploy live infrastructure.

## Review Focus

- Lost start/report response and process crash: durable journal prevents repeat execution (Task 2).
- Group membership, credential revocation or node grant change while queued: dispatch/start must reject (Task 1).
- Same project environment on two nodes: independent state and idempotent requests (Tasks 1/3).
- Frozen secrets never appear in task lists, errors, progress or frontend (Tasks 1/2/3).
- Explicit port/bind settings and manual LB instructions preserve existing local-default binding (Tasks 2/3/4).

## Task 1: Control plane and wire contract

**Files:** create internal/domain/remote.go, internal/store/remote.go, internal/httpapi/remote.go and tests under control-server; modify schema.sql and server.go; preserve existing permission behavior outside new routes.
**Interfaces:** exact management and Agent API from spec; AgentTask resolution compatible with PlatformClient validation. HTTP exceptions/errors use existing handlers. PostgreSQL atomic locking and encrypted specs.

- [x] Write tests for single-use registration, node-scoped identity and cross-node denial, authorization intersection, multi-node instance uniqueness, task request idempotency and conflicts, pinned config, cancel/start race, terminal report immutability, phase restrictions and no secret responses.
- [x] Run focused Go tests and observe expected failure.
- [x] Implement tables/models/store transactions/endpoints; one active claimed task per node, queued deadline, revalidation and task-scoped artifacts/registry token.
- [x] Run `go test ./...` with a disposable PostgreSQL fixture where available; otherwise retain CI integration coverage and document skips.
- [x] Independent review and address actionable findings.

## Task 2: Agent and runtime reuse

**Files:** create deployctl/node_agent.py, tests/test_node_agent.py; modify deployctl/cli.py only to register/dispatch agent commands.
**Consumes:** Task 1 wire schema. Existing PlatformClient HTTP transport/download validation and Manager runtime.
**Produces:** enroll(server, token, home), Agent.run/run_once, safe durable journal, systemd unit rendering and observation reporting.

- [x] Write failing tests for API task validation, exact frozen config use, no repeated execution after lost report/start response, crash journal recovery, unknown observations, private credentials, and command parsing.
- [x] Run `python -m unittest tests.test_node_agent -v` and verify failure.
- [x] Implement registry auth context scoped to task, host runtime executor, durable before/after execution journal, nonblocking heartbeat and safe fixed phases; reject malformed identities/paths and only run on Linux.
- [x] Run focused and full Python unittest suite; check zipapp packaging includes the new module without external dependencies.
- [x] Independent review and address actionable findings.

## Task 3: Web workflows

**Files:** create control-web/src/NodesPage.tsx, ProjectDeployments.tsx, remote.ts and focused tests; modify main.tsx navigation/project tab; scoped styles only as necessary.
**Consumes:** Task 1 management endpoints, exact Node/Instance/Task fields in spec.
**Produces:** node registration/grants/disable/re-enroll and project deployment/rollback/cancel/status views.

- [x] Write failing tests for request id persistence, input validation, status labeling and multi-node instance mapping; browser test complete registration/deployment flow with API fixture.
- [x] Run focused test to prove failure.
- [x] Implement with existing controls, accessible labels, busy/error/empty states and polling cleanup. Secrets displayed only at initial enrollment response, never retained in URLs/storage.
- [x] Run `npm test`, `npm run build` and focused Playwright flow.
- [x] Independent review and address actionable findings.

## Task 4: End-to-end verification and operations

**Files:** docs/remote-deployment.md, README.md links; integration test fixture as appropriate; this plan tracks evidence.
**Consumes:** all components.

- [x] Document actual enrollment/systemd commands, config ownership, rollback/recovery, lost credentials, manual gateway setup, limitations and upgrade compatibility.
- [x] Verify Agent with real control-plane fixture where possible and fake Docker runtime for protocol cases; distinguish Linux Docker test availability.
- [x] Run Python suite, Go suite, Web tests/build and diff checks. Review whole change for authorization, replay and secret boundaries.
- [x] Record verification evidence and remaining infrastructure-dependent checks; leave changes reviewable without live deployment.

## Decisions and progress

- 2026-11-05: User explicitly authorized implementation after completing the proposal. Adopt above concrete defaults and proceed without another design approval.
- 2026-11-05: Worktree C:/Users/yeshaopeng/.codex/worktrees/remote-deploy/deployctl, branch codex/remote-deploy. Component implementers own disjoint directories; shared wire contract is this spec. Integration reviewer verifies cross-component agreement.
- Completed Task 1: real PostgreSQL integration tests cover node identity, permissions, concurrent claims/cancel/start, encrypted frozen configuration, scope changes, archival queue recovery and immutable terminal reports. Independent review completed.
- Completed Task 2: durable Agent journal, task-scoped material retrieval, independent heartbeat/observation/report scheduling and shared runtime reuse. Reviews reproduced and fixed stale observation ordering, private observation marker leakage, cooperative systemd shutdown and observation/deployment lock contention.
- Completed Task 3: node and project deployment workflows, retry-preserved request identity, environment-filtered tasks, unknown versus offline connectivity and stale observations. Existing styles preserved; 375px layout inspected.
- Completed Task 4: operations guide, standalone zipapp checks, real Go/PostgreSQL/Python protocol fixture and Linux CI Docker helper integration. No live deployment or external publication performed.

## Verification evidence (2026-11-05)

- Python full suite: 301 tests, OK, 35 platform-dependent skips on Windows. Agent focused suite: 23 tests, OK, 1 POSIX-only skip.
- Go full suite with disposable PostgreSQL 17.6: `go test ./... -count=1` passed; `go vet ./...` passed. `CTL_TEST_PYTHON` enabled real HTTP/SQL/Python integration; rerun after final Agent changes passed.
- Real protocol integration verifies two-node independent versions, encrypted/pinned configs, actual task-scoped package download and registry token issuance, upgrade/rollback, failed health recovery, crash journal and cross-node denial. Docker operations are simulated in this portable test.
- Web: 30 unit tests, production build, 31 mocked browser regression cases passed; final 4 remote workflow cases rerun successfully. An initial final rerun failed because the temporary Vite server had stopped; restarting the fixture resolved ERR_CONNECTION_REFUSED without product changes.
- Built `dist/deployctl.pyz`; isolated `-I -S` agent help/service and deployment validation pass. Installer generation check passed. `git diff --check` passed.
- Full-change review and scoped re-review approved; no known remaining must-fix findings.
- Environment limit: no Linux Docker runtime and no Windows GCC/CGO race instrumentation here. Real authenticated Agent upgrade/rollback coverage was added to existing Linux CI; `go test -race` remains in that CI. These infrastructure-dependent checks have not been executed locally.

## Discovery-first onboarding revision (2026-11-06)

- [x] Generate a 15-minute, single-use enrollment invitation without creating a node or assigning grants. Atomically consume it when Agent registers; create a pending node with empty scopes and issue its independent identity. Preserve legacy owner pre-provisioning API and known-node reenrollment.
- [x] Agent reports hostname as a display label. Pending nodes heartbeat and poll but cannot receive deployment tasks/material; explicit enabled + group/project + environment grants complete authorization.
- [x] Web uses 接入节点 → generate credential → discover pending node → 分配授权, with 5-second refresh while instructions remain open and transient credential cleanup on dismissal/navigation. Existing edit/disable/reenrollment flows remain available.
- [x] Go full suite against real PostgreSQL and Go/Python protocol fixture passed; go vet passed. New tests cover no precreation, expiry, concurrent one-use redemption, pending task denial and retained identity/grants on reenrollment. Protocol and Linux Docker helper now use discovery and subsequent grants.
- [x] Python full suite: 301 tests, OK, 35 Windows/platform skips. Web: 30 unit tests, build, 33 mocked browser cases passed; mobile onboarding inspected. Independent review found no actionable issues.
- [x] Rebuilt bundled CLI and local preview Agent assets; restarted API with original keys/database and retained Vite preview at http://127.0.0.1:5174/#nodes. Live proxy smoke verified readiness and successful credential generation with unchanged node count; no fake nodes added. git diff --check passed.
- Remaining environment limit unchanged: actual Linux Docker deployment was not run locally; protocol Docker operations are simulated, and real Docker coverage remains in CI.

## Release integration (2026-11-06)

- User authorized publishing installation assets, without upgrading a production server. Release target: CLI 1.14.0 / Web 0.7.0.
- Integrated origin/main 9205c9a (v1.13.0 static deployment support). Rebuilt conflicting bundled CLI from merged sources using pinned PyYAML 6.0.3. Static projects retain local CLI deployment and are excluded from Docker-only remote tasks and the remote deployment tab.
- Full Python suite: 340 tests, 57 platform skips, passed. Full Go suite with real PostgreSQL/Python protocol and go vet passed. Web 33 unit tests and production build passed. Independent integration/release review found no must-fix findings.
- Browser verification on an isolated API/database passed 38 of 39 local cases initially; the remaining test incorrectly hardcoded development port 5174. Corrected it to use the current page origin and its focused rerun passed. The Docker image upload browser case requires Linux Registry and is left to CI. Earlier missing-credential failures were fixture setup errors, resolved with isolated owner credentials.
- Upstream directory fsync failed on Windows; retained file fsync and Linux directory fsync, skipping only unsupported Windows directory fsync. Existing static publication regressions now pass.
