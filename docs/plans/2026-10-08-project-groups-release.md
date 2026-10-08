# Project groups v1.8.0 release

User authorized continuing from the completed and green project-groups draft PR to merge and publish the CLI and server deliverables. This release does not perform a production host upgrade.

- [x] Confirm feature-head CI, independent review and migration compatibility.
- [x] Update CLI1.8.0 / management UI0.2.0 metadata, generated skill assets and upgrade instructions. Keep minimum compatible CLI1.7.0. Generated CLI/skill version and checksum verified, Python206 tests passed (19 existing platform skips), Web9 tests and build passed.
- [ ] Verify version/assets, merge PR4 using its exact approved head and publish immutable tag v1.8.0.
- [ ] Wait for the release workflow to verify CLI installation and server install/upgrade from actual assets, anonymous image pull and checksums.
- [ ] Inspect the public release assets and provide actual upgrade commands/URLs. No production database or server changes.
