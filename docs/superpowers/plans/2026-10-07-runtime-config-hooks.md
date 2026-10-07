# Runtime Config Files and Install Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if the user chooses delegation. Steps use checkbox (`- [ ]`) syntax for tracking. Native execution is recommended for this plan because configuration, snapshots, hooks and recovery share one deployment transaction.

**Goal:** Add application configuration files through `--env-var`, and optional pre/post-install hooks with separate `--set` installation parameters.

**Architecture:** Pure configuration helpers resolve runtime values; immutable server snapshots supply both JSON and Compose raw env files. Release v2 declares and verifies packaged hooks. A managed Bash runner and the existing Docker boundary are coordinated by a deployment state machine that commits a version and configuration together.

**Tech Stack:** Python >=3.10, standard library, existing PyYAML 6.0.3, unittest, Linux Bash, Docker Engine, Compose >=2.30.0, GitHub Actions.

**Spec:** [2026-10-07-runtime-config-hooks-design.md](../specs/2026-10-07-runtime-config-hooks-design.md). User approved continuing from this design on 2026-10-07; execution of this plan awaits plan review and execution-method selection.

## Global Constraints

- Keep `--env <environment>`; add repeatable `--env-var KEY=value`, `--set KEY=value`, and upgrade-only `--unset-env KEY`. No new init flags.
- Default output is `.env.json`; strings remain strings. First version has no dotenv output, global env layer, runtime build, or container-mode hooks.
- Uppercase ASCII variable names, maximum 128 characters. Each input group has at most 128 entries, each value at most 4096 characters, aggregate at most 64 KiB UTF-8; apply the same bounds to merged business configuration. Platform-generated values are counted separately.
- Preserve whitespace, empty strings, `=`, quotes, `$` and `#`; reject controls, newlines and Unicode line separators. Split at the first `=`; reject duplicates and bare keys. Reserve `APP_VERSION` and `DEPLOYCTL_` in application configuration.
- Runtime precedence: config.env, secrets.env, successful persisted overrides, this invocation's unset/set changes. Preserve original server files. `--set` is invocation-local hook data and never enters application JSON/environment.
- Bind JSON read-only at `/run/deployctl/.env.json`; expose `DEPLOYCTL_ENV_FILE`. Snapshot directories are 700; JSON is 644 within its protected directory; all other snapshot files and hook logs are 600.
- Host Bash hooks are optional, self-contained and run as the ctl identity. Timeout is an integer 1..3600 seconds, default 300; source script is a normal repository file of at most 256 KiB, with no symlinks or path escape.
- No-hooks packages retain release schema 1, minimum CLI 1.0.0 and four standard members. Hook packages use schema 2 and minimum CLI 1.5.0 with exact declared script members and SHA256.
- Snapshot/ref state uses schema 2; old state is readable and migrates during deployment, never during read-only status. Pending recovery, original error exits and fixed image digests remain enforced.
- Commit success only after both hooks and final health checks; pre failure does not replace the existing container. Recovery restores the version and configuration, without rerunning hooks or undoing database/filesystem effects.
- Use an isolated workspace if authorized by the user's execution-method choice. Check AGENTS.md, dependencies and baseline there before code changes; use the app-managed worktree mechanism where it supports this repository.

## Review Focus

1. Quotes, dollar signs, leading/trailing spaces, extra equals signs and Unicode must survive JSON, raw env files, hooks and SSH without shell evaluation: Tasks 1, 4, 6, 8.
2. An archive or local snapshot must not redirect a mount/script through a symlink, altered template, undeclared member or invalid configuration ID: Tasks 2, 3, 5.
3. A same-version configuration update, or failure after a candidate is already healthy, must preserve the right rollback configuration and persisted override layer: Tasks 3, 5, 8.
4. A timed-out script with a grandchild, or a diagnostic write failure, must not leave an uncontrolled process or prevent recovery: Tasks 4, 5, 8.
5. Legacy migration must use the real container's configuration and identity, while status remains read-only and credentials remain absent from child inheritance and diagnostic output: Tasks 4, 5, 6.

## File Responsibilities and Interface Rules

- Create `deployctl/runtime_config.py`: assignment parsing, raw env parsing, limits, resolution, serialization. No Docker/state/process dependencies.
- Extend `deployctl/contract.py`, `deployctl/release.py` and both schemas: project hook declarations and versioned, integrity-checked archives.
- Create `deployctl/runtime_snapshot.py`: protected immutable configuration bundles and the platform-generated runtime Compose file.
- Create `deployctl/hooks.py`: native Bash execution, context, bounded logs, timeout and process-group cleanup.
- Create `deployctl/deployment_state.py`: v1/v2 normalization, validation and deployment references. Keep filesystem locks/atomic writes in their existing location.
- Extend `deployctl/runtime.py`: coordinate snapshots, state, hooks and Docker; retain the injectable driver and add an injectable hook runner.
- Extend `deployctl/cli.py`; create `scripts/prepare_deploy.py` and `scripts/deploy_remote.py`: CLI wiring and safe SSH payload transport.
- Update documentation, example startup, templates, skill and release verification together with the component they describe.

`ConfigurationSnapshot` is defined in runtime_snapshot.py with `id: str`, `directory: Path`, `values: dict[str,str]`, `overrides: dict[str,str]`, `install_params: dict[str,str]`, `sha256: str`. `DeploymentRef` is defined in deployment_state.py with `version: str`, `configuration: str|None`, `configuration_sha256: str|None`, `binding: dict`, `legacy: bool`. Null configuration/digest is allowed only for a legacy reference. Other tasks import these names or use the stated dictionary representation; do not invent parallel interfaces.

---

### Task 1: Runtime assignment resolution and serialization

**Files:** Create `deployctl/runtime_config.py`, `tests/test_runtime_config.py`; modify `deployctl/runtime.py` only to retain `parse_env` as a compatibility wrapper if raw parsing is moved.

**Interfaces produced:**
- `parse_assignments(items: list[str], field: str, reserve_platform: bool = True) -> dict[str,str]`.
- `read_raw_env(path: Path) -> dict[str,str]` preserves the existing raw KEY=value format and duplicate rejection.
- `merge_runtime_values(config: dict, secrets: dict, previous_overrides: dict, updates: dict, unset: list[str], version: str) -> tuple[dict[str,str],dict[str,str]]`, returning final values including APP_VERSION, then the new persisted override map.
- `render_json(values: dict[str,str]) -> str` and `render_raw_env(values: dict[str,str]) -> str`, with deterministic ordering and trailing newline.

- [ ] **Write failing tests.** `test_assignments_preserve_literal_values`: assert parsing `['EMPTY=', 'TEXT= a,b "quoted" $literal #tag = ']` gives the exact strings. `test_duplicate_or_bare_key_rejected`: assert `ValueError` for `['A=x','A=y']` and `['A']`. Boundary subcases assert 128 names/4096-character values/64 KiB pass, and the next size fails. Control/line-separator and reserved-name subcases fail.
- [ ] **Observe red.** Run `python -m unittest discover -s tests -p test_runtime_config.py -v`; expect the new module or functions to be missing, not an unrelated fixture failure.
- [ ] **Implement the module.** Use dictionary copies, first-equals splitting, UTF-8 aggregate accounting and standard JSON serialization with `ensure_ascii=False`; do not trim values or invoke a shell. Reject set/unset overlap and duplicate unset names. Empty values do not satisfy required nonempty configuration later in Task 5.
- [ ] **Verify resolution.** `test_precedence_persistence_and_unset` asserts config `A=base`, secret `A=secret`, previous override `A=old`, update `A=new` yields `A=new`; removing the previous override yields `A=secret`. Assert install parameters are never arguments to this resolver. Run the focused suite and existing raw-env tests.
- [ ] **Commit** as `Add runtime parameter parsing and configuration resolution`.

### Task 2: Hook contract and release v2 integrity

**Files:** Modify `deployctl/contract.py`, `deployctl/release.py`, `schemas/deployment.schema.json`, `schemas/release.schema.json`, `tests/test_release.py`; create `tests/test_hook_release.py`.

**Interfaces consumed:** Existing deployment/release validation and `build_release(config, image, version, output, commit='')`.
**Interfaces produced:** `validate_project_hooks(value: dict) -> dict`; `build_release(..., project_root: Path|None = None) -> Path`, defaulting root to the current business-project directory. Release `hooks` maps `pre_install`/`post_install` to `{path, sha256, timeout_seconds}` with fixed `hooks/pre-install.sh`/`hooks/post-install.sh` members. `unpack_release` and `validate_release` keep their signatures and support both protocols.

- [ ] **Write failing tests.** `test_no_hooks_retains_four_member_v1` asserts schema 1/minimum 1.0.0/exact original members. `test_hook_package_has_v2_manifest_and_original_bytes` asserts schema 2/minimum 1.5.0, actual SHA256 and original UTF-8/Bash bytes. Empty hooks normalize to no-hooks; default timeout is 300.
- [ ] **Observe red.** Run `python -m unittest discover -s tests -p test_hook_release.py -v`; expect unknown hook fields or missing protocol behavior.
- [ ] **Implement versioned packaging.** Normalize project declarations; read bounded normal files inside project_root, rejecting symlink files and symlink parent components. Consume source hook paths during packaging and put only the release manifest in the package. Keep build.args stripping and original Compose rendering. Require schema/minimum/manifest combinations to agree.
- [ ] **Verify rejection.** `test_rejects_undeclared_changed_or_linked_scripts` asserts undeclared members, tampered script bytes, bad digest, symlink, traversal and duplicate entries fail before destination creation. Boundary tests cover 256 KiB script, 1/300/3600-second timeouts and invalid booleans; retain existing 10 MiB compressed/5 MiB expanded/1 MiB member limits. Run both release suites and jsonschema valid/invalid examples.
- [ ] **Commit** as `Package verified installation hooks in release protocol v2`.

### Task 3: Immutable snapshots and controlled runtime Compose

**Files:** Create `deployctl/runtime_snapshot.py`, `tests/test_runtime_snapshot.py`; modify `deployctl/runtime.py` DockerDriver methods.

**Interfaces consumed:** Task 1 serializers/resolver, Task 2 canonical release rendering.
**Interfaces produced:** `create_snapshot(folder: Path, app: str, env: str, release: dict, values: dict, overrides: dict, install_params: dict) -> ConfigurationSnapshot`; `load_snapshot(folder: Path, ref: dict, release: dict) -> ConfigurationSnapshot`; `verify_snapshot(snapshot: ConfigurationSnapshot) -> None`; `render_runtime_compose(release: dict, configuration_id: str) -> dict`.

- [ ] **Write failing tests.** `test_snapshot_uses_private_directory_and_nonroot_readable_json` asserts modes 700/644/600 on POSIX and exact JSON/raw values on both platforms. `test_runtime_compose_has_only_platform_mount_and_final_env` asserts a single read-only JSON bind with `create_host_path: false`, raw env_file, fixed target and configuration label; application environment includes DEPLOYCTL_ENV_FILE, excludes installation parameters.
- [ ] **Observe red.** Run `python -m unittest discover -s tests -p test_runtime_snapshot.py -v` and confirm missing snapshot behavior.
- [ ] **Implement the bundle.** Generate a UUID hex ID under `folder/runtime`; write exactly `.env.json`, `effective.env`, `overrides.json`, `.install-params.json`, `compose.yaml`. Hash sorted member names and bytes with explicit separators into snapshot.sha256. Bound each generated/read JSON file to 256 KiB to allow escaping of the 64 KiB input; reject links/unknown members/path escape. Fully write and sync before returning a snapshot. On write failure remove only this operation's verified snapshot directory, leaving all referenced snapshots intact.
- [ ] **Wire the Docker boundary.** DockerDriver.compose chooses a verified platform `DEPLOYCTL_COMPOSE_FILE` path when provided, otherwise the canonical release file; use environment substitutions `DEPLOYCTL_EFFECTIVE_ENV_FILE` and `DEPLOYCTL_ENV_SOURCE` for host file paths. Manager.docker_environment removes inherited COMPOSE_* and DEPLOYCTL_* control values before assigning platform paths and COMPOSE_DISABLE_ENV_FILE. Extend probe to compare configuration label when present. Add stopped-container-aware inspection and `inspect_image_environment(image: str) -> list[str]`; keep captured credentials out of diagnostics.
- [ ] **Verify mutation and literal paths.** `test_changed_json_raw_env_or_compose_rejected` changes each file after creation and asserts verification fails; `test_invalid_id_or_linked_directory_cannot_escape` rejects invalid IDs/parent links; `test_host_path_with_spaces_and_dollar_is_data` asserts controlled substitutions rather than embedded shell commands; `test_parent_compose_controls_cannot_select_another_runtime_file` asserts inherited paths are removed. Run snapshot and existing health tests.
- [ ] **Commit** as `Generate protected runtime configuration snapshots and Docker mounts`.

### Task 4: Host hook runner with bounded diagnostics

**Files:** Create `deployctl/hooks.py`, `tests/test_hooks.py`; POSIX process tests explicitly skip on Windows.

**Interfaces consumed:** ConfigurationSnapshot and `verify_snapshot` from Task 3; release hook manifest from Task 2.
**Interfaces produced:** `HookRunner.check() -> None`; `HookRunner.run(phase: str, descriptor: dict, release_dir: Path, snapshot: ConfigurationSnapshot, context: dict[str,str]) -> None`. `HookFailure(RuntimeError)` exposes phase, exit_code/timeout and log_path without embedding hook output.

- [ ] **Write failing tests.** `test_hook_reads_same_runtime_json_and_separate_install_params` runs a real short Bash script, asserting runtime DATA_DIR and DEPLOYCTL_PARAM_DATA_DIR stay distinct and both JSON paths contain the right maps. `test_parent_download_credentials_not_inherited` places a sentinel GH_TOKEN/GITHUB_TOKEN in the parent and asserts neither reaches the script. `test_bash_startup_control_variables_rejected` covers BASH_ENV, ENV, SHELLOPTS and BASHOPTS in runtime configuration.
- [ ] **Observe red.** Run `python -m unittest discover -s tests -p test_hooks.py -v`; on Linux, expect missing runner behavior. Do not treat Windows skips as real Bash verification.
- [ ] **Implement managed Bash execution.** Use `/bin/bash` or a checked Bash executable with `--noprofile --norc`, an argument array, stdin DEVNULL and a new POSIX session/process group. Build a minimal system environment plus runtime/config context and prefixed installation parameters; verify script digest and snapshot before/after execution. Merge stdout/stderr into a bounded 64 KiB tail, stored with mode 600; no arbitrary output in exception messages.
- [ ] **Verify failure lifecycle.** `test_timeout_kills_script_and_grandchild` uses timeout=1 and a child heartbeat to assert the whole group stops. `test_keyboard_interrupt_cleans_group` asserts interruption propagates after cleanup. `test_large_output_is_drained_and_bounded` emits more than 64 KiB without blocking and asserts the log cap. `test_log_write_failure_is_reported_without_secret_output` injects OSError. Normal completion closes remaining same-group background processes.
- [ ] **Commit** as `Execute installation hooks with context and process cleanup`.

### Task 5: Version-and-configuration deployment transactions

**Files:** Create `deployctl/deployment_state.py`, `tests/test_deployment_state.py`; modify `deployctl/runtime.py`, `tests/test_runtime.py`; create `tests/test_runtime_hooks.py`.

**Interfaces consumed:** Tasks 1–4, plus existing driver methods/locks/atomic state writes.
**Interfaces produced:** `normalize_state(data: dict|None, app: str, env: str) -> dict`; `promote_state(state: dict, candidate: DeploymentRef, preserve_previous: bool = False) -> dict`; `collect_legacy_values(container_env: list[str], image_env: list[str], configured_names: set[str], version: str) -> dict[str,str]`. `Manager(..., driver=None, hook_runner=None).deploy(..., runtime_env=None, unset_env=None, install_params=None) -> dict`, retaining original positional parameters and port/bind behavior.

- [ ] **Write failing transaction tests.** Extend FakeDocker to retain active version/configuration/binding and expose actual/image env; add FakeHookRunner to record phases and inject failures. `test_pre_failure_never_replaces_container` asserts no candidate up/old restore call. `test_post_failure_restores_old_snapshot_and_exits_nonzero` asserts old JSON/environment/ref are restored and candidate overrides do not become current. `test_set_without_hooks_rejected_before_pull` asserts no ignored installation parameters.
- [ ] **Observe red.** Run `python -m unittest discover -s tests -p test_runtime_hooks.py -v`; confirm missing integrated behavior.
- [ ] **Implement state/ref and migration helpers.** Schema 2 uses current/previous DeploymentRef and transaction `{from, to, phase}`; retain binding as the current binding summary. Validate UUID/digest/version/ownership and reserved shapes before path use. Normalize v1 refs as legacy without writing. Before deploying from v1, verify actual container image/application/version, select actual user-configured/different-from-image values and create the rollback baseline; missing/mismatched container refuses migration. Uncaptured historical refs remain legacy and use the original server-file behavior.
- [ ] **Integrate deployment phases.** Resolve config and required_config before pulling; create candidate and legacy baseline snapshots before saving the transaction. Run pre, up, readiness/config check, post and final readiness/config check, saving phases. Commit current and overrides together only after success. On pre failure keep old container; after replacement restore old snapshot or stop first-install candidate. Keep pending on interrupted execution/recovery failure. Best-effort diagnostics cannot block recovery.
- [ ] **Integrate operations/recovery.** Rollback resolves the target ref's release and snapshot, restores its binding and config, and never runs hooks. Stop/restart/status/logs resolve the current or pending ref consistently; status does not migrate. `current` text pointer contains only version; events/failure metadata contain refs/phases, never values. Same-version retry preserves previous only when final values, persisted override layer and binding all match; a changed configuration/override layer produces a rollback target even with the same version.
- [ ] **Verify state boundaries.** `test_env_var_satisfies_required_config_and_persists` asserts runtime flags satisfy required values and survive upgrade; `test_unset_env_restores_server_value` removes the layer; `test_same_version_config_change_can_roll_back` restores prior config; `test_legacy_capture_uses_actual_not_edited_server_values` exercises migration; `test_status_does_not_write_legacy_state` compares bytes. Retain pull-failure, disk-full diagnostics, interruption, failed recovery, binding, immutable package and unchanged retry tests, updating only v2 state assertions.
- [ ] **Commit** as `Commit and recover deployment versions with configuration snapshots`.

### Task 6: CLI flags and safe SSH parameter transport

**Files:** Modify `deployctl/cli.py`, `.github/workflows/deploy.yml`, `templates/deploy.yml`, `skills/team-deploy/assets/templates/deploy.yml`; create `scripts/prepare_deploy.py`, `scripts/deploy_remote.py`, `tests/test_runtime_cli.py`, `tests/test_deploy_workflow.py`.

**Interfaces consumed:** parse_assignments and Manager.deploy from Tasks 1 and 5.
**Interfaces produced:** `scripts.prepare_deploy.prepare_parameters(runtime_env: str, install_params: str) -> dict` with payload `{runtime_env: dict, install_params: dict}`; standalone server helper `scripts.deploy_remote.build_command(payload: dict, common_args: list[str]) -> list[str]` and `main(argv=None) -> int`. The server helper uses only the standard library and invokes the installed deployctl through argv.

- [ ] **Write failing CLI/transport tests.** `test_env_keeps_environment_and_new_maps_are_separate` asserts --env stays production and env-var/set are sent to distinct Manager arguments. `test_unset_only_available_for_upgrade` asserts install rejection. `test_remote_values_never_become_shell_source` asserts a literal `a,b "quoted" $literal $(printf executed) = x` remains one argv value and no command substitution executes.
- [ ] **Observe red.** Run focused runtime_cli and deploy_workflow suites; expect unknown flags/missing helpers.
- [ ] **Wire the CLI.** Add action=append single-value flags to install/upgrade, validate before release acquisition, then pass parsed maps to Manager. Read successful schema 2 current.version for the existing OK message; diagnostics do not print supplied maps. Preserve all existing root/config-root/install-only port/bind commands.
- [ ] **Wire reusable SSH deployment.** Add optional string inputs `runtime-env` and `install-params`, default empty. Move existing local validation/download preparation into prepare_deploy.py; validate KEY=value lines including duplicates and blank-line handling, then write a mode-600 JSON payload. Transfer payload, helper and verified archive with scp. Use the existing validated remote directory and common args to invoke the helper; parameter values never appear in SSH command text. Bound payload to 512 KiB, validate schema/string maps again, require ctl >=1.5.0 when new parameters are supplied and propagate ctl failure exits.
- [ ] **Verify backwards behavior.** `test_empty_payload_preserves_old_deploy_arguments` asserts unchanged no-param invocation; `test_plain_cli_and_remote_payload_are_equivalent` compares Manager input maps; `test_private_values_not_printed` checks captured output. Run actionlint with `-shellcheck= -pyflakes=` and Bash syntax checks for modified embedded steps.
- [ ] **Commit** as `Expose runtime env and installation parameters through CLI and SSH workflow`.

### Task 7: Project example, docs, skill and versioned assets

**Files:** Modify `examples/project-a/app.py`, `templates/deployment.yaml`, `skills/team-deploy/assets/templates/deployment.yaml`, README.md, `docs/{onboarding,operations,release-pipeline,agent-usage}.md`, `skills/team-deploy/{SKILL.md,references/operations.md,references/onboarding.md,references/cli-lifecycle.md}`, `deployctl/__init__.py`, `pyproject.toml`, relevant version-pinned templates and `.github/workflows/platform-release.yml`; create `docs/runtime-config.md`, `skills/team-deploy/references/runtime-config-hooks.md`; modify `tests/test_skill_bundle.py`.

**Interfaces consumed:** The delivered CLI/manifest/snapshot behavior from Tasks 1–6.

- [ ] **Update the example startup.** Read optional DEPLOYCTL_ENV_FILE using JSON, validate string values and APP_VERSION; fail startup on an explicitly supplied unreadable/invalid file. Preserve existing no-file behavior and HTTP health/version routes. Keep existing nonroot UID10001 Dockerfile.
- [ ] **Update human docs/templates.** Show repeated env-var versus set, persistence/unset, startup file reading, optional host hooks, timeout/failure rules and legacy versus snapshot rollback. Add only commented hook examples to init templates. Record that post is before success commit, not before possible traffic; no migration rollback promise.
- [ ] **Update skill resources.** Apply skill-creator/writing-skills instructions before edits. Route runtime-file and hook requests to the self-contained reference; explain CLI/version checks and both workflow references. Keep agents/openai.yaml metadata and the self-update floor >=1.3.0. Add bundled CLI help/validation tests for new parameters and protocol 2 without site-packages.
- [ ] **Stamp and build v1.5.0 assets.** Bump package/CLI version and active default platform refs together; keep historical verification records and schema-1 minimum unchanged. Run build_install_script.py --check, build_release_assets.py, wheel build, `python -I -S dist/deployctl.pyz --version`, CLI help, skill quick_validate.py with `-X utf8`, and checksum checks.
- [ ] **Commit** as `Document runtime config and hook onboarding for v1.5.0`.

### Task 8: Real Docker acceptance, review and publication

**Files:** Modify `scripts/docker_integration.py`, `.github/workflows/verify.yml`, `docs/verification.md`; add bounded test hook fixtures generated inside the integration runner's temporary project.

**Interfaces consumed:** All completed components, versioned artifacts and existing release pipeline.

- [ ] **Extend real integration.** Continue using the temporary registry, unique Compose project and existing nonroot example. Invoke the actual CLI with runtime and hook parameters, use docker exec to read/compare JSON and process env, and assert JSON cannot be written from the container. Execute actual pre/post Bash scripts against both runtime and installation JSON; assert ordering and values without publishing fixture configuration.
- [ ] **Verify failure/recovery end to end.** Add pre nonzero, post nonzero, timeout, new image readiness failure, old configuration restoration, unset, same-version configuration rollback and unchanged-retry preservation. Keep existing install/upgrade/rollback/status/logs/stop/restart checks. Cleanup only this run's UUID containers/network/images and verified temporary paths.
- [ ] **Run complete verification.** Full unittest discovery; both JSON schemas against v1/v2 valid and malformed cases; actionlint; generated installer consistency; zipapp/wheel/skill builds. GitHub Linux jobs must run actual Bash/process tests and Docker integration; Windows skips remain explicit. Existing build-args real Docker Action transport must still pass.
- [ ] **Request whole-branch review.** For native execution, use a fresh reviewer on the most capable available model after implementation, as required by writing-plans. Supply spec/plan/diff/test evidence; reproduce and fix actionable findings, rerunning only affected checks plus required final verification. Do not add per-task agents unless the user chose that method.
- [ ] **Publish using existing authorization.** Merge/push verified feature changes to the authorized repository, wait for successful CI, then create v1.5.0 tag on the feature commit and run existing draft-install/publish/latest checks. Never move a published tag. Confirm formal/latest release and all nine assets, download and verify each checksum, sync installed skill from official ZIP preserving UI metadata.
- [ ] **Record completed evidence.** Append actual local/CI/Bash/Docker/release verification results, protocol compatibility and asset hashes to docs/verification.md; keep unexecuted SSH/production checks explicit. Commit documentation with [skip ci], then confirm clean repository and report usage/upgrade instructions.

## Plan Review and Execution

Recommended method: **native implementation in this task, in an authorized isolated workspace**, following each test/commit cycle above and one independent whole-branch review. The components share state and snapshots, so keeping the implementation in one context reduces interface drift. Subagent-per-task execution remains an alternative if the user prefers it.

The repository was clean at plan preparation. Baseline on Windows: `python -X utf8 -m unittest discover -s tests` ran 78 tests, with 76 passed and 2 environment skips. Local Docker is unavailable; GitHub Linux CI supplies actual Docker/Bash coverage. These are baseline results, not evidence that the planned features work.

Self-review completed: all spec sections map to Tasks 1–8; the five Review Focus items have named tests; interfaces are shared by their producing tasks; no placeholders or implementation bodies substitute for decisions. Await user review of this plan and selection of execution method before product-code changes.
