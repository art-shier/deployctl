# ctl server Release delivery

Goal: publish CLI1.7.0 and a verified server bundle in one GitHub Release, then install the multi-service platform with `ctl server install --release URL` without a source checkout. User explicitly authorized publication and CLI updates; no production host deployment requested.

Use the existing independent Compose/bootstrap for API, Registry and PostgreSQL. Ordinary application packages remain single-service. The dedicated bundle contains the bootstrap, config generator, Compose and a manifest with immutable image digest and member hashes; no credentials. Defaults use ctl.shier.art for both public endpoints. Login remains scoped-token based.

Execution: inline, using existing feature checkout. No new parallel workers required for implementation.

1. Write regression tests for bounded archive validation, digest/member integrity, unsafe entries, command parsing and failed/retried bootstrap state; observe RED.
2. Add server bundle builder and dedicated CLI install/upgrade. Reuse HTTPS/checksum acquisition; Linux/root only, private persistent bundle directories, per-instance lock, pending state on failure. No automatic destructive rollback or database recreation.
3. Extend real CI to install/upgrade the bundle through the actual CLI using a local Registry digest and preserve platform keys. Build/push dual-architecture server image in the release workflow; publish bundle+checksum alongside CLI assets only after authenticated draft verification. Verify anonymous GHCR pull before publishing.
4. Update operations, README and CLI/skill documentation; build and test. Review this release-delivery increment, fix concrete issues and pass real CI.
5. Integrate ctl PR, publish immutable v1.7.0, verify actual public assets and CLI version. Update the locally installed ctl only where an existing managed installation is identified. Provide exact Release URL/install commands and token login instructions. Notes PR/production deployment are outside this publication.

Validation: Python and distributed CLI tests, workflow/actionlint checks, actual Docker platform bundle installation+retry/upgrade CI, exact tag/version checks, actual draft/public download installation, public Registry digest verification. Do not report a server as deployed merely because its Release is published.
