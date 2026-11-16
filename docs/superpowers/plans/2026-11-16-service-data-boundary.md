# Service deployment / application data responsibility correction

User direction (2026-11-16): ctl manages service deployment, upgrades, logs and
container/volume lifecycle. Database backup, recovery, verification and data
cutover belong to Lifelog, possibly an explicit temporary operator script.
This supersedes the data-recovery portions of the 2026-11-15 design.

No v1.20.0 release or production changes have occurred. Earlier candidate CI
success does not satisfy this revised scope.

## Boundaries
- Keep schema-2 multi-image declarations, service-scoped config, dependency DAG,
  generic one-shot jobs, per-service health/logs, retained volume inventory,
  deployment transaction journals and existing install/upgrade/online upgrade.
- Remove ctl restore, backup SHA CLI/API inputs, backup freshness/source policy,
  data-volume recovery/promotion workflow and claims that ctl restores data.
- Generic jobs bind execution to their immutable command/image/configuration and
  target resources. ctl checks execution/exit state and never blindly repeats an
  uncertain job. A project may pass a checksum through its normal configuration;
  ctl does not interpret it as a backup identifier or determine data validity.
- Persistent-service maintenance is a deployment operation. Its reviewed plan
  concerns service resources/configuration, not a backup attestation.
- The old single-service -> multi-service transition is a service layout change
  under normal upgrade. Lifelog's operator script stops the old app beforehand.
  ctl verifies/retains its stopped snapshot/container and creates new owned
  resources without adopting the old hook database. The transition journal must
  survive interruption and preserve the original app/env/public binding. No
  automatic return to old data after a job with incompatible side effects.
- Lifelog's script owns maintenance mode, original-role checkpoint, writer fence,
  final A/B backup and uncertain-upload quarantine, archive checks, canonical
  data fingerprints, installer files, write-enable decision and one backup owner.

## Work
- [x] Add failing CLI/data-boundary tests; remove public restore and backup flags.
- [x] Remove job_sources from Agent/Go protocol and runtime interfaces; retain only generic job selection.
- [x] Replace ctl data restoration module with narrowly scoped legacy service-layout transition under upgrade; remove whole-stack disaster restore path.
- [x] Update maintenance contract, existing tests, Docker fixtures and docs/skill references.
- [ ] Complete Lifelog business migration script and durable backup/freeze reporting; review and real tests.
- [ ] Re-run whole acceptance, review corrected boundary, release ctl, update center/node.
- [ ] Publish Lifelog, execute business-owned cutover, validate new backup and isolated restore.

## Current evidence
Prior ctl d6e89f7 full workflows passed (43821369515,43821369508).
Prior Lifelog native restore Docker acceptance passed (43822170305).
Revised boundary CLI/job tests: 5 passed. Full suite not yet updated/re-run.

Boundary review: independent review found the old fresh-volume requirement on
all incompatible jobs. Removed it; volumeless and existing-resource explicit
job regressions pass (RED two failures, GREEN). Independent re-review: 28 passed,
no remaining blocker in that correction. Prior full Python revision: 698 passed,
171 subtests, one skipped and four known macOS exclusions; current final rerun
and Linux Docker CI remain required. Go domain/httpapi/store local checks passed
(store integration database coverage is delegated to Linux CI).

Final local correction verification: 700 Python tests passed, 171 subtests passed,
one skipped and four known macOS-specific exclusions; Linux CI runs those cases.
Both real-Docker fixture declarations validate, published pyz help contains no
restore command, and whitespace checks pass. Re-review closed the generic-job
policy issue. New full Linux acceptance is the remaining release gate.
