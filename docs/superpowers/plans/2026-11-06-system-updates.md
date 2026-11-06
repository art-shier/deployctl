# System updates implementation

Spec: `docs/superpowers/specs/2026-11-06-system-updates.md`.
Workspace: existing isolated worktree, branch `codex/system-updates`, base a7d04fa (v1.14.0).

Global constraints: no live server upgrade/publish in this task, no arbitrary remote commands or URLs, secrets remain private. Existing business deployment protocol remains compatible. Each component owns its files; shared API contract is spec. Tests before implementation; independent review before completion.

## Task 1: Control plane
Own control-server only. Add official release provider, domain/schema/store/API updater identities and durable batch jobs. Owner audit, idempotency, enrollment, protocol bounds and lease exclusion with existing remote tasks. Tests with real PostgreSQL, injectable release provider and HTTP auth boundaries. Follow exact spec wire fields.

## Task 2: Host updater
Own deployctl and tests/test_updater*.py only. Add independent helper CLI enrollment/run/service/setup, private bootstrap copy, node rollback, center backup/upgrade and durable recovery/reporting. Integrate Agent fresh health marker. Reuse installers, validate paths and identity, never expose arbitrary remote commands. Test lifecycle with injected system runner and protocol fakes. Do not change server/UI/docs/version until coordinated.

## Task 3: Web system updates
Own control-web only. Owner route/sidebar, target list, enrollment instructions, version chooser, batch node updates, center confirmation, current progress/history/cancel, state polling, fixed-id uncertain retry. Match spec; reuse current styles. Unit/build/browser regression and mobile checks. Do not change package version until coordinated.

## Task 4: Integration and docs
Root owns contract coordination, version metadata, runtime build/preview, cross-language protocol fixture, docs/system-updates.md and README entry. Verify full Python/Go/Web suites, independent component+whole review, fix findings; preserve original preview keys/data. No publishing without a later explicit release request.

## Ledger

Preflight pairs: 1/2 consume same updater JSON fields; spec is authoritative. 1/3 owner API and errors; fixed response documented. 2/3 onboarding CLI commands; synchronize exact flags after CLI ready. 1/4 test fixture needs current endpoints; integration after component completion. 2/4 helper requires new CLI baseline; root bumps1.15.0 and builds. 3/4 root changes package version only after UI handoff. No shared implementation file ownership.
Self-check: Task1 gates leases not running work; Task2 independent copied helper avoids self-termination; Task3 only owner requests, no credential storage; Task4 tests not production execution.
Ruling: protocol1 supports official1.x releases from1.15.0 onward, not automatic major compatibility. Ruling: center image/artifact stores are retained, helper backs up DB+keys/config and admin confirmation covers larger external backups. Ruling: all workers are manually bootstrapped once; no silent existing-host installation.

- [ ] Control plane and component review
- [ ] Host updater and component review
- [ ] Web and component review
- [ ] Integrated verification, operational docs and whole review
