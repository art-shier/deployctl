# Team Deploy Implementation Plan

> Execute inline, task by task, with test-driven-development and verification-before-completion.

**Goal:** Deliver a reusable GitHub Actions and Linux Docker Compose deployment kit.
**Architecture:** Validated project contract → immutable image + release tarball → deployctl state machine. Release generation, download, runtime and CLI are separate modules.
**Tech Stack:** Python >=3.10, PyYAML 6.0.3, unittest, Docker Compose >=2.30, GitHub Actions.
**Spec:** design.md

## Global Constraints
- One stateless HTTP container; default host bind 127.0.0.1.
- Deployment operations target Linux; package/validate work cross-platform.
- No automatic database migration; no arbitrary scripts from release packages.
- Immutable image digest, external checksum, separate secrets and atomic state.

## Review Focus
- Archive traversal/symlink/duplicate/oversize input rejected before extraction (Task 1).
- Missing configuration and pull failures leave existing service untouched (Task 2).
- Failed upgrade and failed recovery never return success or advance version (Task 2).
- Downloads strip authentication across redirects and private assets use GitHub API (Task 3).
- Workflow templates run outside platform repo and refer to a pinned platform revision (Task 4).

## Task 1: Contract and release package
Files: deployctl/contract.py, release.py, cli.py, tests/test_release.py; schema and template files.
Interfaces: validate_deployment(data)->dict; build_release(config,image,version,output,commit)->Path; unpack_release(path,destination)->dict.
- [x] Write CLI/package success, invalid field, digest and archive rejection tests; observe red.
- [x] Implement strict validation, safe packaging and extraction; run tests green.

## Task 2: Runtime transaction
Files: deployctl/runtime.py, tests/test_runtime.py, tests/test_health.py.
Interfaces: Manager(root,config_root,driver).deploy(app,env,release,upgrade=False); rollback; operate. DockerDriver implements check/pull/up/down/probe/ps/logs/stop/restart.
- [x] Write success, missing config, upgrade recovery, failed recovery, pending-operation and config-preservation tests; observe red.
- [x] Implement locking, atomic state, raw config, checked Docker execution and health polling; run green.

## Task 3: Acquisition and distribution
Files: deployctl/download.py, scripts/build_zipapp.py, install.sh, tests/test_download.py.
Interfaces: acquire_release(source,cache,expected_sha256=None)->Path; GitHub URL resolver and credential-stripping redirect handler.
- [x] Write checksum, URL and auth tests; observe red.
- [x] Implement HTTPS download and credential boundaries; build self-contained zipapp and test CLI.

## Task 4: Workflows, example and onboarding
Files: .github/workflows/{ci,build-release,deploy,platform-release}.yml; examples/project-a; templates; README.md; docs/onboarding.md.
- [x] Add real Docker integration script (Linux CI) exercising install/upgrade/recovery/rollback.
- [x] Implement reusable build and opt-in SSH deploy workflows, versioned platform packaging and sample service.
- [x] Verify local suite, schemas, YAML, shell syntax, example health/version, wheel and zipapp; document unexecuted cloud integration checks.

## Execution record
User approved the described approach and requested implementation on 2026-10-04. New projectless directory: deliverable lives under outputs/team-deploy; no existing repository to modify. Implement inline; review complete changes before packaging.

Tasks 1–4 complete. Fresh review identified zipapp exit propagation, same-version rollback pointer, hidden tar metadata expansion and failure-log preservation; reproduced each and added regression tests. Failure diagnostics are best-effort and cannot block container recovery. Full local suite:30 passing tests. Ruling: document and configure real Docker checks in Linux CI because this Windows host has no Docker; do not claim end-to-end deployment validation.
