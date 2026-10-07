"""Locked deployment transactions, persistent state and Docker boundary."""

from contextlib import contextmanager
from datetime import datetime, timezone
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
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'{path.name}: use raw KEY=value format')
        key, value = line.split('=', 1)
        if not ENV_NAME.fullmatch(key) or key in result:
            raise ValueError(f'{path.name}: invalid or duplicate variable name')
        result[key] = value
    return result


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
    def __init__(self, root='/opt/deployments', config_root='/etc/deployctl', driver=None):
        self.root = Path(root).resolve()
        self.config_root = Path(config_root).resolve()
        self.driver = driver or DockerDriver()

    def paths(self, app, env):
        validate_name(app)
        validate_name(env, 'environment', 32)
        home = self.root / app / env
        home.mkdir(parents=True, exist_ok=True)
        (home / 'releases').mkdir(exist_ok=True)
        return home, self.config_root / app / env

    def state(self, home, app, env):
        if not (home / 'state.json').exists():
            return {'schema_version': 1, 'application': app, 'environment': env,
                    'current': None, 'previous': None, 'transaction': None, 'binding': None}
        value = json.loads((home / 'state.json').read_text(encoding='utf-8'))
        if value.get('application') != app or value.get('environment') != env:
            raise ValueError('state belongs to a different application/environment')
        for field in ('current', 'previous'):
            if value.get(field) is not None:
                version(value[field])
        if value.get('transaction'):
            transaction = value['transaction']
            if transaction.get('from'):
                version(transaction['from'])
            version(transaction['to'])
        return value

    def save(self, home, state, event):
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        atomic_json(home / 'state.json', state)
        # state.json is authoritative; this is only a human-readable pointer.
        (home / 'current').write_text((state['current'] or '') + '\n', encoding='utf-8')
        with (home / 'events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': state['updated_at'], 'event': event,
                                     'current': state['current'], 'pending': bool(state['transaction'])}) + '\n')

    def configuration(self, folder, release):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        values = {}
        for name in ('config.env', 'secrets.env'):
            path = folder / name
            if not path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            if name == 'secrets.env' and os.name != 'nt' and path.stat().st_mode & 0o077:
                raise ValueError('secrets.env must have permissions 600 (chmod 600)')
            values.update(parse_env(path))
        missing = [key for key in release['deployment']['required_config'] if not values.get(key)]
        if missing:
            raise ValueError(f'Missing required configuration: {", ".join(missing)}; fill {folder}')

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
        release = validate_release(read_yaml(directory / 'release.yaml'))
        if release['application'] != app or release['version'] != release_version:
            raise ValueError('stored release application/version mismatch')
        # Recheck the standard Compose contract even on later rollback/operations.
        from .release import render_compose
        if read_yaml(directory / 'compose.yaml') != render_compose(release):
            raise ValueError('stored compose.yaml has been modified')
        return directory, release

    def stage(self, home, package, app):
        import uuid
        stage = home / 'releases' / ('.stage-' + uuid.uuid4().hex)
        try:
            release = unpack_release(package, stage)
            if release['application'] != app:
                raise ValueError('release application does not match requested application')
            target = home / 'releases' / release['version']
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

    def deploy(self, app, env, package, upgrade=False, port=None, bind=None):
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
            self.configuration(folder, release)
            binding = self.binding(release, state['binding'], port, bind)
            environment = self.docker_environment(folder, binding)
            project = project_name(app, env)
            self.driver.check()
            self.driver.pull(directory, project, environment)
            before = dict(state)
            state['transaction'] = {'from': state['current'], 'to': release['version']}
            self.save(home, state, 'deployment_started')
            phase = 'start'
            try:
                self.driver.up(directory, project, environment)
                phase = 'health-check'
                self.driver.probe(directory, project, environment, release, binding)
            except Exception as original:
                reason = str(original)
                try:
                    self.capture_failure(home, directory, project, environment, release, phase, reason)
                except OSError:
                    # Diagnostics must never prevent container recovery/cleanup.
                    reason += ' (diagnostic snapshot could not be written)'
                try:
                    if before['current']:
                        old_dir, old_release = self.stored_release(home, before['current'], app)
                        old_env = self.docker_environment(folder, before['binding'])
                        self.driver.up(old_dir, project, old_env)
                        self.driver.probe(old_dir, project, old_env, old_release, before['binding'])
                    else:
                        self.driver.down(directory, project, environment)
                except Exception as recovery:
                    self.save(home, state, 'deployment_and_recovery_failed')
                    raise RuntimeError(f'deployment failed ({phase}: {reason}); recovery failed; pending transaction retained; run rollback') from recovery
                self.save(home, before, 'deployment_failed_old_restored' if before['current'] else 'install_failed_candidate_stopped')
                message = 'old version restored' if before['current'] else 'candidate stopped'
                raise RuntimeError(f'deployment failed ({phase}: {reason}); {message}; inspect last-failure.log') from original
            previous = before['previous'] if before['current'] == release['version'] else before['current']
            state.update({'current': release['version'], 'previous': previous,
                          'binding': binding, 'transaction': None})
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
                    directory, release = self.stored_release(home, pending['to'], app)
                    binding = self.binding(release)
                    self.driver.down(directory, project, self.docker_environment(folder, binding))
                    state['transaction'] = None
                    self.save(home, state, 'failed_install_cleaned')
                    return state
                raise ValueError('no previous successful version to roll back to')
            directory, release = self.stored_release(home, target, app)
            self.configuration(folder, release)
            binding = self.binding(release, state['binding'])
            environment = self.docker_environment(folder, binding)
            self.driver.pull(directory, project, environment)
            previous = state['current']
            state['transaction'] = {'from': target, 'to': target}
            self.save(home, state, 'rollback_started')
            try:
                self.driver.up(directory, project, environment)
                self.driver.probe(directory, project, environment, release, binding)
            except Exception as exc:
                self.save(home, state, 'rollback_failed')
                raise RuntimeError('rollback failed; pending transaction retained') from exc
            state.update({'current': target, 'previous': previous if previous != target else state['previous'],
                          'binding': binding, 'transaction': None})
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
            directory, release = self.stored_release(home, active, app)
            binding = self.binding(release, state['binding'])
            environment = self.docker_environment(folder, binding)
            self.driver.check()
            output = self.driver.operate(directory, project_name(app, env), environment, action, tail)
            if action == 'restart':
                self.driver.probe(directory, project_name(app, env), environment, release, binding)
            if action == 'status':
                return json.dumps(state, indent=2) + '\n' + output
            return output
