"""Locked deployment transactions, persistent state and Docker boundary."""

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from .contract import ENV_NAME, integer, read_yaml, validate_name, validate_release, version
from .release import unpack_release
from .deployment_state import DeploymentRef, collect_legacy_values, normalize_state, promote_state
from .hooks import HookRunner, validate_hook_values
from .runtime_config import read_raw_env, merge_runtime_values, validate_values, validate_unset
from .runtime_snapshot import create_snapshot, load_snapshot, reject_links, verify_snapshot


def project_name(app, environment):
    suffix = hashlib.sha256(f'{app}/{environment}'.encode()).hexdigest()[:8]
    return f'{app}-{environment}-{suffix}'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_ready(url):
    try:
        # Local service checks must not go through the operator's HTTP proxy.
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=2) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


@contextmanager
def service_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            handle.write(b'0')
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('another deployment operation is running') from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('another deployment operation is running') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_env(path):
    return read_raw_env(path)


class DockerDriver:
    def _run(self, command, environment=None, timeout=600):
        try:
            result = subprocess.run(command, env=environment, capture_output=True,
                                    text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f'cannot execute {command[0]} or command timed out') from exc
        if result.returncode:
            # Do not echo arbitrary subprocess output that may contain credentials.
            raise RuntimeError(f'Docker command failed (exit {result.returncode}); check deployctl logs and Docker daemon')
        return result.stdout.strip()

    def check(self):
        if os.name == 'nt':
            raise RuntimeError('deployment commands require a Linux server')
        output = self._run(['docker', 'compose', 'version', '--short'], timeout=30)
        match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', output)
        if not match or tuple(map(int, match.groups())) < (2, 30, 0):
            raise RuntimeError('Docker Compose >=2.30.0 is required for raw env files')
        self._run(['docker', 'info', '--format', '{{.ServerVersion}}'], timeout=30)

    def compose(self, directory, project, environment, *arguments):
        return self._run(['docker', 'compose', '--project-name', project,
                          '--project-directory', str(directory),
                          '--file', environment.get('DEPLOYCTL_COMPOSE_FILE', str(directory / 'compose.yaml')),
                          *arguments], environment)

    def pull(self, directory, project, environment):
        self.compose(directory, project, environment, 'config', '--quiet')
        self.compose(directory, project, environment, 'pull', '--policy', 'always', 'app')

    def up(self, directory, project, environment):
        self.compose(directory, project, environment, 'up', '-d', '--no-build',
                     '--pull', 'never', '--remove-orphans', 'app')

    def down(self, directory, project, environment):
        self.compose(directory, project, environment, 'down', '--remove-orphans')

    def inspect_container(self, directory, project, environment):
        ids = self.compose(directory, project, environment, 'ps', '--all', '-q', 'app').splitlines()
        if len(ids) != 1:
            return None
        template = ('{"image":{{json .Config.Image}},"labels":{{json .Config.Labels}},'
                    '"running":{{json .State.Running}},"environment":{{json .Config.Env}}}')
        return json.loads(self._run(['docker', 'inspect', '--format', template, ids[0]]))

    def inspect_image_environment(self, image):
        return json.loads(self._run(['docker', 'image', 'inspect', '--format', '{{json .Config.Env}}', image])) or []

    def probe(self, directory, project, environment, release, binding):
        host = '127.0.0.1' if binding['address'] == '0.0.0.0' else binding['address']
        url = f"http://{host}:{binding['port']}{release['deployment']['health']['readiness_path']}"
        deadline = time.monotonic() + release['deployment']['health']['startup_timeout_seconds']
        while True:
            info = self.inspect_container(directory, project, environment)
            if (info and info['running'] and info['image'] == release['image']
                    and info['labels'].get('io.team-deploy.version') == release['version']
                    and info['labels'].get('io.team-deploy.application') == release['application']
                    and (not environment.get('DEPLOYCTL_CONFIGURATION') or
                         info['labels'].get('io.team-deploy.configuration') == environment['DEPLOYCTL_CONFIGURATION'])
                    and http_ready(url)):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('readiness/actual image/version/configuration check timed out')
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def operate(self, directory, project, environment, action, tail=100):
        if action == 'status':
            return self.compose(directory, project, environment, 'ps', '--format', 'json')
        if action == 'logs':
            return self.compose(directory, project, environment, 'logs', '--no-color', '--tail', str(tail), 'app')
        if action in ('stop', 'restart'):
            return self.compose(directory, project, environment, action, 'app')
        raise ValueError('unsupported operation')


class Manager:
    def __init__(self, root='/opt/deployments', config_root='/etc/deployctl', driver=None, hook_runner=None):
        self.root = Path(root).resolve()
        self.config_root = Path(config_root).resolve()
        self.driver = driver or DockerDriver()
        self.hook_runner = hook_runner or HookRunner()

    def paths(self, app, env):
        validate_name(app)
        validate_name(env, 'environment', 32)
        home = self.root / app / env
        reject_links(home)
        reject_links(self.config_root / app / env)
        home.mkdir(parents=True, exist_ok=True)
        (home / 'releases').mkdir(exist_ok=True)
        return home, self.config_root / app / env

    def state(self, home, app, env):
        path = home / 'state.json'
        reject_links(path)
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        # v1 did not save a binding before the first successful install. Its
        # interrupted candidate used the release defaults; infer only in memory.
        if (isinstance(value, dict) and value.get('schema_version') == 1
                and value.get('binding') is None and isinstance(value.get('transaction'), dict)):
            _, candidate = self.stored_release(home, value['transaction'].get('to'), app)
            value['binding'] = self.binding(candidate)
        return normalize_state(value, app, env)

    def save(self, home, state, event):
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        atomic_json(home / 'state.json', state)
        # state.json is authoritative; this is only a human-readable pointer.
        try:
            (home / 'current').write_text((state['current']['version'] if state['current'] else '') + '\n', encoding='utf-8')
            with (home / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'time': state['updated_at'], 'event': event,
                                         'current': state['current'], 'pending': bool(state['transaction'])}) + '\n')
        except OSError:
            pass  # The authoritative commit must not fail because a secondary pointer/log failed.

    def configuration(self, folder, release=None):
        reject_links(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        layers = []
        for name in ('config.env', 'secrets.env'):
            path = folder / name
            reject_links(path)
            if not path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            if name == 'secrets.env' and os.name != 'nt' and path.stat().st_mode & 0o077:
                raise ValueError('secrets.env must have permissions 600 (chmod 600)')
            layers.append(parse_env(path))
        if release is not None:
            self.required_configuration(dict(layers[0], **layers[1]), release)
        return tuple(layers)

    def required_configuration(self, values, release):
        missing = [key for key in release['deployment']['required_config'] if not values.get(key)]
        if missing:
            raise ValueError(f'Missing required configuration: {", ".join(missing)}; fill server files or use --env-var')

    def docker_environment(self, folder, binding, snapshot=None):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('COMPOSE_', 'DEPLOYCTL_'))}
        environment.update({'COMPOSE_DISABLE_ENV_FILE': '1',
                            'DEPLOY_CONFIG_FILE': str(folder / 'config.env'),
                            'DEPLOY_SECRETS_FILE': str(folder / 'secrets.env'),
                            'DEPLOY_BIND_ADDRESS': binding['address'], 'DEPLOY_PORT': str(binding['port'])})
        if snapshot is not None:
            from .runtime_snapshot import verify_snapshot
            verify_snapshot(snapshot)
            environment.update({'DEPLOYCTL_COMPOSE_FILE': str(snapshot.directory / 'compose.yaml'),
                                'DEPLOYCTL_EFFECTIVE_ENV_FILE': str(snapshot.directory / 'effective.env'),
                                'DEPLOYCTL_ENV_SOURCE': str(snapshot.directory / '.env.json'),
                                'DEPLOYCTL_CONFIGURATION': snapshot.id})
        return environment

    def binding(self, release, old_binding=None, port=None, bind=None):
        base = old_binding or {'port': release['deployment']['container']['host_port'],
                               'address': release['deployment']['container']['bind_address']}
        result = dict(base)
        if port is not None:
            result['port'] = port
        if bind is not None:
            result['address'] = bind
        integer(result['port'], 1, 65535, 'host port')
        try:
            ipaddress.IPv4Address(result['address'])
        except (ValueError, TypeError) as exc:
            raise ValueError('bind address must be IPv4') from exc
        return result

    def stored_release(self, home, release_version, app):
        version(release_version)
        directory = home / 'releases' / release_version
        reject_links(directory)
        reject_links(directory / 'release.yaml')
        reject_links(directory / 'compose.yaml')
        release = validate_release(read_yaml(directory / 'release.yaml'))
        if release['application'] != app or release['version'] != release_version:
            raise ValueError('stored release application/version mismatch')
        # Recheck the standard Compose contract even on later rollback/operations.
        from .release import render_compose
        if read_yaml(directory / 'compose.yaml') != render_compose(release):
            raise ValueError('stored compose.yaml has been modified')
        from .release import read_hook_script
        for descriptor in release.get('hooks', {}).values():
            if hashlib.sha256(read_hook_script(directory, descriptor['path'])).hexdigest() != descriptor['sha256']:
                raise ValueError('stored hook script has been modified')
        return directory, release

    def stage(self, home, package, app):
        import uuid
        stage = home / 'releases' / ('.stage-' + uuid.uuid4().hex)
        try:
            release = unpack_release(package, stage)
            if release['application'] != app:
                raise ValueError('release application does not match requested application')
            target = home / 'releases' / release['version']
            reject_links(target)
            checksum = hashlib.sha256(Path(package).read_bytes()).hexdigest()
            if target.exists():
                recorded = (target / 'archive.sha256').read_text().strip()
                if recorded != checksum:
                    raise ValueError('release versions are immutable; publish a new version')
            else:
                (stage / 'archive.sha256').write_text(checksum, encoding='ascii')
                stage.rename(target)
            return target, release
        finally:
            if stage.exists():
                # Only remove our own staging path, within this service's releases directory.
                if not stage.resolve().is_relative_to((home / 'releases').resolve()):
                    raise ValueError('staging path escapes releases directory')
                shutil.rmtree(stage)

    def capture_failure(self, home, directory, project, environment, release, phase, reason):
        diagnostic = {'time': datetime.now(timezone.utc).isoformat(),
                      'version': release['version'], 'phase': phase, 'reason': reason}
        atomic_json(home / 'last-failure.json', diagnostic)
        try:
            logs = self.driver.operate(directory, project, environment, 'logs', 200)
        except Exception:
            logs = 'Container logs could not be captured.\n'
        path = home / 'last-failure.log'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(f"Failed {release['version']} ({phase}): {reason}\n")
            handle.write(logs[-65536:])
        path.chmod(0o600)

    def reference_environment(self, home, folder, ref, app):
        directory, release = self.stored_release(home, ref['version'], app)
        snapshot = None if ref['legacy'] else load_snapshot(folder, ref, release)
        if snapshot is None:
            self.configuration(folder, release)
        environment = self.docker_environment(folder, ref['binding'], snapshot)
        return directory, release, snapshot, environment

    def capture_legacy(self, home, folder, ref, app, project, configured_names):
        directory, release = self.stored_release(home, ref['version'], app)
        environment = self.docker_environment(folder, ref['binding'])
        info = self.driver.inspect_container(directory, project, environment)
        if (not info or info.get('image') != release['image']
                or info.get('labels', {}).get('io.team-deploy.application') != app
                or info.get('labels', {}).get('io.team-deploy.version') != ref['version']):
            raise ValueError('legacy migration requires the actual matching container; restore it before upgrading')
        values = collect_legacy_values(info.get('environment') or [],
                                       self.driver.inspect_image_environment(release['image']),
                                       configured_names | set(release['deployment']['required_config']), ref['version'])
        snapshot = create_snapshot(folder, app, home.name, release, values, {}, {})
        return DeploymentRef(ref['version'], snapshot.id, snapshot.sha256, ref['binding']).as_dict()

    def deploy(self, app, env, package, upgrade=False, port=None, bind=None,
               runtime_env=None, unset_env=None, install_params=None):
        updates = validate_values(runtime_env or {}, 'env-var')
        unset = list(unset_env or [])
        validate_unset(unset)
        params = validate_values(install_params or {}, 'installation parameters', False)
        if unset and not upgrade:
            raise ValueError('unset-env is only available for upgrade')
        home, folder = self.paths(app, env)
        with service_lock(home / '.lock'):
            state = self.state(home, app, env)
            if state['transaction']:
                raise RuntimeError('pending deployment; inspect status then run rollback')
            if upgrade and not state['current']:
                raise ValueError('application is not installed; use install first')
            if not upgrade and state['current']:
                raise ValueError('application is already installed; use upgrade')
            directory, release = self.stage(home, package, app)
            if params and not release.get('hooks'):
                raise ValueError('installation parameters require a package declaring hooks')
            config, secrets = self.configuration(folder)
            old_snapshot = None
            if state['current'] and not state['current']['legacy']:
                _, _, old_snapshot, _ = self.reference_environment(home, folder, state['current'], app)
            values, overrides = merge_runtime_values(config, secrets,
                old_snapshot.overrides if old_snapshot else {}, updates, unset, release['version'])
            refresh_config = release.get('hooks', {}).get('pre_install', {}).get('refresh_config', False)
            if not refresh_config:
                self.required_configuration(values, release)
            if release.get('hooks'):
                validate_hook_values(values)
                self.hook_runner.check()
            binding = self.binding(release, state['binding'], port, bind)
            project = project_name(app, env)
            self.driver.check()
            unchanged_before = copy.deepcopy(state)
            if state['current'] and state['current']['legacy']:
                state['current'] = self.capture_legacy(home, folder, state['current'], app, project,
                                                      set(config) | set(secrets))
                _, _, old_snapshot, _ = self.reference_environment(home, folder, state['current'], app)
            snapshot = create_snapshot(folder, app, env, release, values, overrides, params)
            candidate = DeploymentRef(release['version'], snapshot.id, snapshot.sha256, binding).as_dict()
            environment = self.docker_environment(folder, binding, snapshot)
            self.driver.pull(directory, project, environment)
            before = copy.deepcopy(state)
            state['transaction'] = {'from': state['current'], 'to': candidate, 'phase': 'prepared'}
            self.save(home, state, 'deployment_started')
            context = {'DEPLOYCTL_APPLICATION': app, 'DEPLOYCTL_ENVIRONMENT': env,
                       'DEPLOYCTL_VERSION': release['version'],
                       'DEPLOYCTL_PREVIOUS_VERSION': before['current']['version'] if before['current'] else '',
                       'DEPLOYCTL_ACTION': 'upgrade' if upgrade else 'install',
                       'DEPLOYCTL_RELEASE_DIR': str(directory), 'DEPLOYCTL_CONFIG_DIR': str(folder),
                       'DEPLOYCTL_IMAGE': release['image']}
            replacement_started = False
            phase = 'prepared'
            try:
                def step(name):
                    state['transaction']['phase'] = name
                    self.save(home, state, 'deployment_phase')
                if 'pre_install' in release.get('hooks', {}):
                    phase = 'pre_install'
                    step(phase)
                    self.hook_runner.run(phase, release['hooks'][phase], directory, snapshot, context)
                if refresh_config:
                    phase = 'configuration'
                    step(phase)
                    config, secrets = self.configuration(folder)
                    refreshed_values, refreshed_overrides = merge_runtime_values(config, secrets,
                        old_snapshot.overrides if old_snapshot else {}, updates, unset, release['version'])
                    self.required_configuration(refreshed_values, release)
                    validate_hook_values(refreshed_values)
                    if refreshed_values != snapshot.values or refreshed_overrides != snapshot.overrides:
                        # The provisional snapshot remains immutable for diagnostics and recovery.
                        snapshot = create_snapshot(folder, app, env, release, refreshed_values,
                                                   refreshed_overrides, params)
                        candidate = DeploymentRef(release['version'], snapshot.id, snapshot.sha256, binding).as_dict()
                        environment = self.docker_environment(folder, binding, snapshot)
                        state['transaction']['to'] = candidate
                        self.save(home, state, 'configuration_prepared')
                verify_snapshot(snapshot)
                phase = 'start'
                step(phase)
                replacement_started = True
                self.driver.up(directory, project, environment)
                phase = 'health-check'
                step(phase)
                self.driver.probe(directory, project, environment, release, binding)
                if 'post_install' in release.get('hooks', {}):
                    phase = 'post_install'
                    step(phase)
                    self.hook_runner.run(phase, release['hooks'][phase], directory, snapshot, context)
                phase = 'final-health-check'
                step(phase)
                self.driver.probe(directory, project, environment, release, binding)
                verify_snapshot(snapshot)
            except Exception as original:
                reason = str(original)
                try:
                    self.capture_failure(home, directory, project, environment, release, phase, reason)
                except Exception:
                    reason += ' (diagnostic snapshot could not be written)'
                try:
                    if replacement_started:
                        if before['current']:
                            old_dir, old_release, _, old_env = self.reference_environment(home, folder, before['current'], app)
                            self.driver.up(old_dir, project, old_env)
                            self.driver.probe(old_dir, project, old_env, old_release, before['current']['binding'])
                        else:
                            self.driver.down(directory, project, environment)
                    restored = before if replacement_started else unchanged_before
                    self.save(home, restored, 'deployment_failed_old_restored' if before['current'] else 'install_failed_candidate_stopped')
                except Exception as recovery:
                    # The already persisted transaction remains authoritative if writing diagnostics also fails.
                    try:
                        self.save(home, state, 'deployment_and_recovery_failed')
                    except OSError:
                        pass
                    raise RuntimeError(f'deployment failed ({phase}: {reason}); recovery failed; pending transaction retained; run rollback') from recovery
                message = ('old version and configuration restored' if before['current'] else 'candidate stopped') if replacement_started else 'existing container unchanged'
                raise RuntimeError(f'deployment failed ({phase}: {reason}); {message}; inspect last-failure.log') from original
            unchanged = bool(before['current'] and before['current']['version'] == release['version']
                             and old_snapshot and old_snapshot.values == snapshot.values
                             and old_snapshot.overrides == snapshot.overrides and before['binding'] == binding)
            state = promote_state(before, candidate, preserve_previous=unchanged)
            self.save(home, state, 'deployment_succeeded')
            return state

    def rollback(self, app, env):
        home, folder = self.paths(app, env)
        with service_lock(home / '.lock'):
            state = self.state(home, app, env)
            pending = state['transaction']
            target = pending['from'] if pending else state['previous']
            project = project_name(app, env)
            self.driver.check()
            if not target:
                if pending and not state['current']:
                    directory, _, _, environment = self.reference_environment(home, folder, pending['to'], app)
                    self.driver.down(directory, project, environment)
                    state['transaction'] = None
                    self.save(home, state, 'failed_install_cleaned')
                    return state
                raise ValueError('no previous successful version to roll back to')
            directory, release, snapshot, environment = self.reference_environment(home, folder, target, app)
            self.driver.pull(directory, project, environment)
            previous = state['current']
            state['transaction'] = {'from': target, 'to': target, 'phase': 'rollback'}
            self.save(home, state, 'rollback_started')
            try:
                self.driver.up(directory, project, environment)
                self.driver.probe(directory, project, environment, release, target['binding'])
                if snapshot: verify_snapshot(snapshot)
            except Exception as exc:
                self.save(home, state, 'rollback_failed')
                raise RuntimeError('rollback failed; pending transaction retained') from exc
            state.update({'current': target, 'previous': previous if previous != target else state['previous'],
                          'binding': target['binding'], 'transaction': None})
            self.save(home, state, 'rollback_succeeded')
            return state

    def operate(self, app, env, action, tail=100):
        home, folder = self.paths(app, env)
        with service_lock(home / '.lock'):
            state = self.state(home, app, env)
            if state['transaction'] and action not in ('status', 'logs'):
                raise RuntimeError('pending deployment; use rollback first')
            active = state['transaction']['to'] if state['transaction'] else state['current']
            if not active:
                if action == 'status':
                    return json.dumps(state, indent=2)
                if action == 'logs' and (home / 'last-failure.log').exists():
                    return (home / 'last-failure.log').read_text(encoding='utf-8')
                raise ValueError('application has no deployed version')
            directory, release, _, environment = self.reference_environment(home, folder, active, app)
            self.driver.check()
            output = self.driver.operate(directory, project_name(app, env), environment, action, tail)
            if action == 'restart':
                self.driver.probe(directory, project_name(app, env), environment, release, active['binding'])
            if action == 'status':
                return json.dumps(state, indent=2) + '\n' + output
            return output
