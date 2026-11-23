# Application logs implementation plan

Goal: Implement ctl-only structured log storage, specified/percentage admission rules, permission queries, management UI, CLI reads, and reusable JS SDK.
Architecture: PostgreSQL policy and event tables scoped by project/environment. Business backend uses existing scoped ctl authentication; browser SDK uses injectable business-authenticated transport, never ctl server credentials. Runtime logs stay on nodes; unified project logs UI links both sources.
Spec: Approved conversation and local prototype; OR over enabled rules; no expiry, no diagnostic capabilities. No matching rule denies upload. Percentage is stable per project/environment/device. Model matching is exact after SDK normalization. SDK exposes shouldReport/report and getContext.

## Tasks
- [x] Domain tests then implementation: attributes validation, specified matching, stable monotonic percentage, malformed events.
- [x] Persistence/API tests then implementation: policy optimistic version, transactions, scoped auth, server-side admission, duplicate event IDs, retention, pagination.
- [x] CLI tests then implementation: logs --source app retains legacy runtime behavior; filters, JSON, credentials.
- [x] SDK tests then implementation: installation identity, context, permission checks, revocation/cache/identity switching, bounded batch reports.
- [x] UI: reuse Modal, api, tokens; unified logs entry with runtime/app/rules/integration; CRUD and permission tester using real APIs.
- [x] Run Go/Python/web/SDK suites and builds; integration DB; final independent review and fixes.

## Review focus
Cross-project or environment access must fail. No rules means denied. Changed policy must be rechecked on write. Duplicate and delayed retries must not duplicate events. Rapid UI context switching must not reveal stale logs. No ctl credential in SDK browser storage. No changes to Lifelog or production deployment.

## Execution ledger
Existing isolated ctl worktree is clean at 7f2f2cf. Use current branch and preserve runtime logs. Plan approval already provided by user; execute inline without repeated confirmation.

## Verification result (2026-11-23)

- Go full suite passed with real isolated PostgreSQL (CTL_TEST_DATABASE_URL) and a non-symlink TMPDIR.
- Web: 47 tests passed; production build passed. SDK: 5 tests passed. New CLI: 2 tests passed.
- Live local HTTP: SDK permission → upload → query succeeded; ctl CLI read the same event as JSON.
- Browser: event detail, rule creation, percentage permission, manual disable and specified-device matching verified.
- Python full suite: 702 passed, 1 skipped, 171 subtests passed, 4 failures. All four reproduced on unchanged baseline 7f2f2cf: three process-group PermissionError failures and macOS tar metadata expectation. No unrelated fixes included.
- Independent review completed; fixed independent three-day deduplication keys, PostgreSQL JSON validation, UTF-8 identity bounds, configured-environment validation, immutable SDK event payloads, and year-zero timestamps with regression tests.
- git diff --check passed. No Lifelog changes, production deployment, npm publication or Git push.
