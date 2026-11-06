# Integrated updater installation

User correction: updater registration/service setup must accompany normal center/node installation. No second credential generation, manual updater registration, or separate updater install in normal UX. Independent process and dedicated updater identity remain implementation details.

Base5dcaa40, branchcodex/system-updates, draftPR16. Existing updater lifecycle/scheduler/security preserved; no live host changes/releases/merge.

## Task1 Host/API integration
Implement node-scoped automatic enrollment using existing node credential; validate own identity under scheduler/node lock, no caller-selected target, no owner access. Normal new node command `ctl agent install --server URL` prompts original node token only when no node identity; enrolls once, installs/starts Agent+updater. Existing `ctl agent install` uses saved identity, never prompts or rotates node; safe repeat, custompaths/unit support, no overwriting different units. Existing low-level enroll/run/service remain compatible.
Center normal CLI server install/upgrade enables updater after successful deployment, using local owner credential/loopback API without publicDNS prerequisite. Remote updater calls deploy_server directly and must not recursively reconfigure itself. Local owner never stored in updaterconfig or logged. New `ctl updater setup --kind node|center` is credential-free repair/migration entry that uses existing local identity and installs updater service; advanced explicit enroll remains compatibility only.
Dedicated role-specific updater homes/services to avoid center+node samehost collisions; reused valid identity must not rotate. Validate target/server/path and existing execution journals. Preserve active update locks; non-ready/unavailable systemd/setup failure produces explicit partial-success diagnostic (center/Agent installed but auto-update not configured), not misleading success or rollback of installed service. No weakening privileged path checks. Expose exact accepted command contract to root before UI edits.
Test own-only auth/revoked node race/activeupdate refusal, API one-use reuse, automatic install and repeat, rootpermissions, existingconfig mismatch, no duplicated node/token prompt, centerlocalcredential not persisted, setupfailure recovery. Extend disposable real Linux fixtures for actual integrated node/center installer paths; reuse current systemd/Docker CI. Root owns UI/docs/rebuild/fullCI.

## Task2 UI/docs
Root removes ordinary updater enrollment credential form. Setup card explains automatic installation; repair action gives credential-free commands based on existing local identity. Node onboarding standard command becomes agentinstall (original enrollment credential only), preserves reenroll action for actual node identity recovery. Update README/remote/systemupdate guides and canonical spec to align; focused UI tests/build/browser verify.

## Task3 review/integration
Independent source review of integration changes; rebuild zipapp/assets; Go/Postgres, affectedPython/Web and realLinuxCI; same draftPR branch. Preserve running localpreview keys/data; final report no production upgrade/publication.
