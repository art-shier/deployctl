# Multi-service review and acceptance

Independent whole-branch review: base 7e744b06, initial head f420879, fixes 817e4de and e6e34c0. Reviewer operated read-only; no implementation delegation.

Initial Important findings and resolutions:

- Durable crashes after Docker side effects but before state checkpoints: explicit recovery now verifies the journal's intended resources/configurations and records only matching identities. Unexpected established-volume changes still block. Tests restore pre-side-effect disk journals; Linux fixture uses fork and immediate process exit.
- Added application services could not roll back: explicit rollback now journals retirement, stops only applications removed by the target, retains identity records and prevents ordinary restart from reactivating them. Later releases can declare them again.
- Candidate reservation could precede source recovery intent: source transaction now becomes durable first; an interrupted allocation resumes only in the preparation stage.

Follow-up Important findings and resolutions:

- Retirement failure now preserves a recovery barrier instead of falsely clearing the transaction with a stopped worker.
- Approved persistent recreation can reconcile an absent container only for the selected attempted maintenance service; existing volume identity remains mandatory.
- Reusable multi-image build consumes the normalized declaration, so scoped build argument overrides reach the actual builder. End-to-end preparation/workflow/builder regression added.

Minor default-log inconsistency resolved: node and CLI no-selector runtime logs aggregate active services within response limits.

Focused re-review e6e34c0: no remaining blocking findings, 95 focused tests passed. Acceptance remains conditional on CI for the release commit. Real production cutover, application-specific backup data validation and release/updater acceptance are performed separately; the reviewer did not independently repeat Docker/live database/browser tests.

CI evidence prior to final review fixes:
- f420879 deployment toolkit: https://github.com/art-shier/deployctl/actions/runs/43817970686 — all passed, including real maintenance, lost-volume restore and multi-image authenticated mirror.
- f420879 control plane: https://github.com/art-shier/deployctl/actions/runs/43817970908 — all passed, including systemd updater, API/registry, browser and managed Agent compatibility.

New actual hard-crash fixtures, capability advertisement, release version and remote multi-service Agent path require final CI acceptance. No production migration is claimed by this report.
