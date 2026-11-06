# Static release catalog and explicit synchronization

Accepted user scope: replace runtime GitHub REST release discovery with a static official release index, provide initial data on first center installation, persist successful syncs, and add a manual synchronization action. Manual sync retries immediately but cannot guarantee network availability; failed sync must preserve usable data and disclose the error.

## Shared contract

- Official URL: `https://github.com/art-shier/deployctl/releases/latest/download/update-index.json`. No runtime GitHub REST requests for the center catalog.
- Manifest: `{schema_version:1,generated_at:<RFC3339 UTC>,releases:[{version,published_at,cli_sha256,server_sha256}]}`. Exact stable versions, lowercase 64-character hex digests, unique versions, bounded size/count. Sort versions descending. Use existing protocol compatibility rules. Every executable update retains immutable version/digest validation.
- Bundle seed path: `/app/update-index.seed.json`, source `control-deploy/update-index.seed.json`. This contains previously published releases available before image construction; do NOT include an invented current-image checksum (self-reference). Publish the completed current release's index after packages exist.
- Center cache: under the existing persistent ArtifactsDir, atomic replace only after validation. Prefer the newest valid cache/seed, preserve on failure, use bundled seed immediately without blocking readiness. Background sync at startup and every 10 minutes, bounded failure retry (~1 minute), request deduplication and cancellation. Manual sync bypasses freshness/retry delay but joins in-flight request. Shutdown must cancel worker.
- Preserve legacy GET `/api/v1/system-updates/releases` array and existing SystemReleaseProvider test injection. Add owner-only GET `/api/v1/system-updates/catalog` and POST `/api/v1/system-updates/catalog/sync` (empty JSON object, normal mutation CSRF protections).
- Snapshot shape: `{releases:[],source:'bundled'|'cache'|'remote'|'none',generated_at:null|string,last_success_at:null|string,last_attempt_at:null|string,next_sync_at:null|string,stale:boolean,sync_error:string}`. Error codes: empty on success, otherwise `network_error`, `timeout`, `rate_limited`, `not_found`, `http_error`, `invalid_manifest`, `cache_write_failed`. Sync failure returns snapshot HTTP200 with error code so cached data survives; API/auth failure still normal error. Bundled data is stale until live sync succeeds. Do not imply cached data is latest.
- UI shows data source, last successful sync, failure explanation and button `立即同步清单`/`正在同步…`; repeat clicks disabled; successful sync gives feedback. GET is read-only snapshot (no forced network). Failed sync with cached versions must leave them usable; no versions = honest empty/error state. Keep original frozen/uncertain update submissions intact.

## Task A — Go provider and API

Own control-server only. Implement manifest provider, disk+seed loading, scheduling lifecycle, API compatibility and owner-only sync/status. Focused regression tests: no GitHub API calls, seed first start offline, disk restart, old seed/new cache, malformed/truncated/oversized manifests, bad checksum/duplicate version, failed forced refresh retains state, overlapping sync dedup, cancellation, HTTP error classification, auth. Preserve existing job pinning and permissions. Report exact interface choices before changing shared contract.

## Task B — Python/CI publication and initial install

Own deployctl/bootstrap.py, deployctl/server_bundle.py and related Python modules/tests, scripts, install.sh generated installer, control-deploy/Dockerfile, seed JSON, platform-release workflow. Generate seed/index reproducibly with bounded official metadata inputs; history is published versions, current candidate added only after packages/checksums exist. GitHub API permitted only in authenticated publisher CI to bootstrap history; normal CLI latest acquisition and center catalog must use static files. Preserve explicit version, private/custom repository and CI draft release-id contracts. Test publication hash correctness, deterministic seed handling, invalid manifest rejection, installer discovery and direct exact-version behavior. Add released index + SHA to draft/public assets and verify public availability. Docker includes seed. No publication or tag actions by agent.

## Task C — UI/docs/integration (root)

Own control-web, docs, version files, regenerated assets and preview. Consume shared catalog snapshot, add force sync UX and tests. Set next source versions CLI1.16.0/Web0.9.0; do not claim publication. Verify first-start seed, stale cache, failed retry, refresh success, button dedup, preserved pending submission. Rebuild assets after shared sources complete; run affected tests + CI, source review, preview validation. Keep user production hosts untouched.

## Boundaries and acceptance

Do not weaken path/identity checks, bypass checksums, invent releases, expose credentials, or erase existing cache on transient errors. Runtime changes remain backward compatible with updater protocol1. Existing v1.15.1 release remains immutable. Complete reviewable implementation/PR; release only within explicit user publication authorization. This plan is the task's maintained design contract.
