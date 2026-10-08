# Project groups and reusable deployment credentials

User intent: log in once to deploy an authorized collection of projects. All pre-existing projects belong to `default`. Existing owner, publisher, deployer, configuration, release and image behavior must remain compatible.

## Design

- Add persistent groups (`slug`, display name, description, creation time), group list/create/update APIs and management UI. Group identifiers are immutable; group deletion is out of this change so references cannot silently acquire access through identifier reuse.
- Each project belongs to exactly one group, default `default`. Idempotent transactional migration creates default, backfills old project JSON and adds an indexed group FK column; it preserves revisions, releases, secrets, token hashes and sessions. Old project update payloads retain the existing group when omitted.
- Tokens retain legacy `project` and add optional `projects` and `groups`. Grant scope is their union, with a bounded, validated list of existing identifiers. Both publisher/deployer keep their existing action restrictions; deployers still require explicit environments. Old tokens retain their original project scope, never expand to all of default.
- Group grants include present and future group projects. Authentication resolves current membership for each API/Registry token request. Moving a project changes group grants on subsequent requests. Issued Registry bearers retain existing five-minute lifetime.
- Owner remains the global administrator; this task does not add personal accounts or organization membership. UI exposes groups, project assignment and a global credential page with explicit multi-project/group selection and one-time secret display.
- CLI credentials remain one server plus one opaque token. Existing ctl1.7 clients work with widened token scope; login reports authorized role/scope, `ctl whoami` and `ctl projects` expose current identity and deployable projects without exposing secrets.

## Implementation plan (inline execution)

1. [x] Database/groups/project migration and store operations. Tests: old-schema data backfill, repeat migration, preserve settings/releases/token hashes, valid/unknown groups, omission on old updates.
2. [x] Scoped credentials and authorization. Tests: legacy isolation, multi-project scope, empty/unknown/duplicate scopes, group membership additions/moves, forbidden environments/actions, expiry/revocation, Registry pull/push scope and API parity.
3. [x] Groups/token management UI and CLI discovery. Tests: actual group/project/token interactions, multi-project credential usage, no secret persistence, retained existing workflows; CLI identity/project list never prints raw token.
4. [x] Documentation and full verification: Go test/race/vet with disposable PostgreSQL, Web tests/build/browser checks, relevant Python CLI regressions. Independent review completed and findings corrected. No production migration or release is performed by this task.
5. [ ] Commit and push the reviewed branch; create a reviewable draft PR.

## Decisions

- Explicit implementation request authorizes this extension and default migration; proceed without another design approval gate.
- Reuse the clean dedicated ctl checkout on a feature branch; no additional checkout is needed.
- Group authorization is dynamic; explicit project grants are stable when projects move groups. Default is a grouping, not an implicit permission grant.
- Existing JSON token metadata stays readable. Nullable legacy project FK permits empty groups/multiple-project tokens without picking an arbitrary anchor project.

## Verification ledger

- Baseline: Go auth/domain and6 Web unit tests passed before changes.
- RED: new Go group/multiple-project tests failed on absent types/APIs; CLI discovery tests failed on absent commands; Web scope/payload tests failed before implementation.
- GREEN: full Go tests and vet, Linux Go race/vet with disposable PostgreSQL passed. Python206 tests passed with19 existing platform-specific skips. Web9 unit tests and production build passed.
- Independent review found stale project forms could restore a previous group during metadata saves. Reproduced in real browser/API test (expected403, received200), then fixed by omitting unchanged group; same test now passes. Desktop/mobile screenshots checked;390px view has no horizontal overflow.
- Extended real Linux Docker/Registry/CLI fixture passed: one group login installs two independent projects/repositories; moving one out denies upgrade without changing deployed state. Existing precedence, failed-post restoration, receipts, token revocation, legacy install and offline rollback passed.
- Full browser suite against the actual API and Registry passed (2 tests): new group/shared-token flows and existing image/configuration/credential management. Migration regression also confirms releases and stable pointers survive repeated migration.
- Focused independent re-review confirmed the stale-form finding is resolved; no remaining blockers. README and control-plane guide describe compatibility and mark the extension as unreleased.
- Final pre-commit Go tests/vet, Python206 tests (19 existing skips), Web9 tests/build passed. Browser rerun initially hit the legacy fixed `browser-notes` fixture left by the earlier run; after truncating projects only in the disposable browser database, both browser tests passed again (6.1s). No application fix was needed for the fixture collision.
- All fixtures are local disposable data; no production migration, token creation, merge or release is performed by this feature task.
