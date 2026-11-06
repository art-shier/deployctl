"""Outbound node control with durable execution journals, never a remote shell."""
from contextlib import contextmanager
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import sys
import tempfile
import threading
import time

from . import __version__
from .contract import validate_name
from .platform_client import PlatformClient, PlatformError, validate_defaults, validate_origin, validate_resolution, validate_token
from .platform_credentials import Credentials
from .progress import Progress
from .runtime import DockerDriver, Manager, http_ready, project_name, service_lock
from .runtime_snapshot import reject_links, validate_id

DEFAULT_HOME = '/var/lib/ctl-agent'
TERMINAL = {'succeeded', 'failed', 'needs_attention'}
STAGES = {'accepted', 'preparing', 'downloading', 'pulling', 'deploying', 'verifying',
          'rollback', 'completed', 'execution_failed', 'interrupted'}


def private_directory(path):
    path = Path(path).absolute()
    reject_links(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise ValueError('agent directory requires current ownership and mode 0700')
    return path


def read_private(path):
    path = Path(path)
    reject_links(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024:
            raise ValueError('agent state must be a bounded regular file')
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('agent state requires current ownership and mode 0600')
        return handle.read(512 * 1024 + 1)


def read_json(path):
    try:
        value = json.loads(read_private(path))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('invalid agent state; inspect private journal before continuing') from None
    if not isinstance(value, dict):
        raise ValueError('invalid agent state')
    return value


def write_json(path, value):
    path = Path(path)
    private_directory(path.parent)
    reject_links(path)
    fd, name = tempfile.mkstemp(prefix='.agent-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.chmod(name, 0o600)
            json.dump(value, handle, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def instance_identity(value):
    if not isinstance(value, dict):
        raise ValueError('invalid instance identity')
    validate_id(value.get('id'))
    validate_name(value.get('project'))
    validate_name(value.get('environment'), 'environment', 32)
    return {k: value[k] for k in ('id', 'project', 'environment')}


def validate_task(value, node_id):
    if not isinstance(value, dict):
        raise ValueError('invalid agent task')
    for key in ('id', 'instance_id', 'node_id'):
        validate_id(value.get(key))
    if value['node_id'] != node_id or value.get('action') not in ('install', 'upgrade', 'rollback'):
        raise ValueError('task is not authorized for this node or operation')
    if value.get('status') not in ('leased', 'running'):
        raise ValueError('task is not executable')
    validate_name(value.get('project'))
    validate_name(value.get('environment'), 'environment', 32)
    overrides = {k: value[k] for k in ('host_port', 'bind_address') if value.get(k) is not None}
    validate_defaults(overrides)
    if value['action'] == 'rollback':
        if value.get('resolution') or overrides:
            raise ValueError('rollback must use local verified snapshot')
    else:
        resolution = copy.deepcopy(value.get('resolution'))
        if not isinstance(resolution, dict) or not isinstance(resolution.get('release'), dict):
            raise ValueError('missing frozen task resolution')
        if resolution['release'].get('package_path') != f"/api/v1/node/tasks/{value['id']}/package":
            raise ValueError('task package path mismatch')
        # Reuse strict existing resolution validation without widening its legacy path contract.
        release = resolution['release']
        release['package_path'] = f"/api/v1/projects/{value['project']}/artifacts/{release.get('id')}"
        validate_resolution(resolution, value['project'], value['environment'], value.get('version'))
    return value


def fingerprint(task):
    fields = ('id', 'node_id', 'instance_id', 'project', 'environment', 'action',
              'resolution', 'host_port', 'bind_address', 'version')
    return hashlib.sha256(json.dumps({k: task.get(k) for k in fields}, sort_keys=True).encode()).hexdigest()


def task_instance(task):
    return {'id': task['instance_id'], 'project': task['project'], 'environment': task['environment']}


class AgentProgress(Progress):
    def __init__(self, callback):
        super().__init__(enabled=False)
        self.callback = callback

    @contextmanager
    def stage(self, label):
        if 'Pulling' in label:
            phase = 'pulling'
        elif 'readiness' in label.lower() or 'health' in label.lower():
            phase = 'verifying'
        elif 'Recovering' in label:
            phase = 'rollback'
        elif 'Starting' in label or 'hook' in label.lower():
            phase = 'deploying'
        else:
            phase = 'preparing'
        self.callback(phase)
        yield


class AgentDockerDriver(DockerDriver):
    def restore_pull(self, directory, project, environment, image):
        # Remote rollback has no global registry credential. Never use ambient auth
        # to fetch an unscoped image; an existing verified digest is sufficient.
        try:
            digests = json.loads(self._run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', image], timeout=30))
        except (ValueError, RuntimeError):
            raise RuntimeError('rollback image is not available in the local cache') from None
        if not isinstance(digests, list) or image not in digests:
            raise RuntimeError('rollback image is not available in the local cache')


class ObservationDockerDriver(DockerDriver):
    """Bound read-only queries without shortening deployment command timeouts."""
    def _run(self, command, environment=None, timeout=10):
        return super()._run(command, environment, timeout)


class RuntimeExecutor:
    def __init__(self, manager, client):
        self.manager, self.client = manager, client
        self.runtime_lock = threading.RLock()
        self.observer = ObservationDockerDriver() if isinstance(manager.driver, DockerDriver) else manager.driver

    @contextmanager
    def registry_config(self, task):
        credentials = self.client.json('GET', f"/api/v1/node/tasks/{task['id']}/registry")
        if not isinstance(credentials, dict):
            raise ValueError('invalid task registry response')
        token = credentials.get('token')
        if not token:
            yield None
            return
        image_host = task['resolution']['release']['image'].split('/')[0]
        if credentials.get('registry_host') != image_host or not isinstance(token, str) or len(token) > 16384:
            raise ValueError('task registry identity mismatch')
        with tempfile.TemporaryDirectory(prefix='ctl-node-registry-') as folder:
            os.chmod(folder, 0o700)
            write_json(Path(folder) / 'config.json', {'auths': {image_host: {'registrytoken': token}}})
            yield folder

    def execute(self, task, stage):
        # Let a previously started observation finish before taking the runtime's
        # nonblocking process lock. Our own sampler must never fail a deployment.
        with self.runtime_lock:
            return self._execute(task, stage)

    def _execute(self, task, stage):
        manager = self.manager
        old_progress = manager.progress
        manager.progress = AgentProgress(stage)
        try:
            if task['action'] == 'rollback':
                stage('rollback')
                manager.rollback(task['project'], task['environment'])
            else:
                resolution = task['resolution']
                cfg = resolution['configuration']
                stage('downloading')
                with tempfile.TemporaryDirectory(prefix='ctl-node-package-') as cache:
                    package = self.client.download_release(resolution, cache)
                    with self.registry_config(task) as docker_config:
                        manager.driver.docker_config = docker_config
                        try:
                            manager.deploy(task['project'], task['environment'], package,
                                upgrade=task['action'] == 'upgrade', port=task.get('host_port'),
                                bind=task.get('bind_address'), managed_runtime=cfg['runtime_env'],
                                managed_params=cfg['install_params'], deployment_defaults=cfg['deployment_defaults'],
                                management_source={'origin': self.client.server, 'project': task['project'],
                                    'environment': task['environment'], 'release_id': resolution['release']['id'],
                                    'revision_id': cfg['id']})
                        finally:
                            manager.driver.docker_config = None
            return self.observe(task_instance(task))
        finally:
            manager.progress = old_progress

    def observe(self, instance):
        if not self.runtime_lock.acquire(blocking=False):
            return dict(instance_identity(instance), actual_version='', health='unknown', binding=None,
                        pending_transaction=False, _busy=True)
        try:
            return self._observe(instance)
        finally:
            self.runtime_lock.release()

    def _observe(self, instance):
        result = dict(instance_identity(instance), actual_version='', health='unknown', binding=None, pending_transaction=False)
        try:
            home, folder = self.manager.paths(instance['project'], instance['environment'])
            with service_lock(home / '.lock'):
                state = self.manager.state(home, instance['project'], instance['environment'])
                result['pending_transaction'] = bool(state['transaction'])
                current = state['current']
                if not current:
                    result['health'] = 'unknown' if state['transaction'] else 'not_installed'
                    return result
                result['actual_version'] = current['version']
                binding = current['binding']
                result['binding'] = {'host_port': binding['port'], 'bind_address': binding['address']}
                if state['transaction']:
                    return result
                directory, release, _, environment = self.manager.reference_environment(home, folder, current, instance['project'])
                info = self.observer.inspect_container(directory, project_name(instance['project'], instance['environment']), environment)
                if not info or not info.get('running'):
                    result['health'] = 'stopped'
                else:
                    host = '127.0.0.1' if binding['address'] == '0.0.0.0' else binding['address']
                    labels = info.get('labels') or {}
                    matches = (info.get('image') == release['image'] and labels.get('io.team-deploy.version') == release['version']
                        and labels.get('io.team-deploy.application') == instance['project']
                        and (not environment.get('DEPLOYCTL_CONFIGURATION') or labels.get('io.team-deploy.configuration') == environment['DEPLOYCTL_CONFIGURATION']))
                    ready = matches and http_ready(f"http://{host}:{binding['port']}{release['deployment']['health']['readiness_path']}")
                    result['health'] = 'healthy' if ready else 'unhealthy'
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
            result['health'] = 'unknown'
            if isinstance(error, RuntimeError) and str(error) == 'another deployment operation is running':
                result['_busy'] = True
        return result

    def docker_available(self):
        try:
            self.observer.check()
            return True
        except (ValueError, RuntimeError, OSError):
            return False


class Agent:
    def __init__(self, home, node_id, client, executor):
        validate_id(node_id)
        self.home = private_directory(home)
        self.journals = private_directory(self.home / 'tasks')
        self.node_id, self.client, self.executor = node_id, client, executor
        self.instances = []
        self.enabled = True
        self.current_id = None
        self.active_instance = None
        self.stage = 'accepted'
        self.worker = None
        self.worker_error = None
        self.samples = {}
        self.samples_lock = threading.Lock()
        self.observation_lock = threading.Lock()
        self.sample_epochs = {}
        self.docker_ok = False
        self.recover()

    def recover(self):
        for path in self.journals.glob('*.json'):
            validate_id(path.stem)
            record = read_json(path)
            self.validate_record(record, path.stem)
            if record['status'] in ('starting', 'running'):
                record.update(status='needs_attention', stage='interrupted', reported=False)
                write_json(path, record)

    def validate_record(self, record, task_id):
        validate_id(record.get('instance_id'))
        if (record.get('id') != task_id or record.get('node_id') != self.node_id
                or record.get('status') not in TERMINAL | {'starting', 'running'}
                or not isinstance(record.get('fingerprint'), str) or len(record['fingerprint']) != 64
                or record.get('stage') not in STAGES or type(record.get('reported')) is not bool):
            raise ValueError('invalid agent execution journal')

    def execute_task(self, task):
        validate_task(task, self.node_id)
        path = self.journals / (task['id'] + '.json')
        digest = fingerprint(task)
        if path.exists():
            record = read_json(path)
            self.validate_record(record, task['id'])
            if record['fingerprint'] != digest:
                raise ValueError('task content changed after acceptance')
            # No operation with this ID may be executed twice, even if center lost an ACK.
            record['reported'] = False
            write_json(path, record)
            return
        self.invalidate_sample(task['instance_id'])
        record = {'id': task['id'], 'node_id': self.node_id, 'instance_id': task['instance_id'], 'fingerprint': digest,
                  'status': 'starting', 'stage': 'accepted', 'reported': False}
        write_json(path, record)
        if task['status'] == 'running':
            record.update(status='needs_attention', stage='interrupted')
            write_json(path, record)
            return
        try:
            started = self.client.json('POST', f"/api/v1/node/tasks/{task['id']}/start", {})
            validate_task(started, self.node_id)
            if started['status'] != 'running' or fingerprint(started) != digest:
                raise ValueError('started task differs from accepted task')
        except (RuntimeError, ValueError, OSError):
            record.update(status='needs_attention', stage='interrupted')
            write_json(path, record)
            self.current_id = None
            return
        record.update(status='running', stage='preparing')
        write_json(path, record)
        self.current_id = task['id']
        try:
            observation = self.executor.execute(task, self.set_stage)
            record.update(status='succeeded', stage='completed')
            if observation and not observation.get('_busy'):
                record['observation'] = observation
        except (RuntimeError, ValueError, OSError):
            record.update(status='failed', stage='execution_failed')
            try:
                observation = self.executor.observe(task_instance(task))
                if not observation.get('_busy'):
                    record['observation'] = observation
            except (RuntimeError, ValueError, OSError):
                pass
        finally:
            self.invalidate_sample(task['instance_id'])
            self.current_id = None
        # On process interruption the durable running journal is deliberately retained.
        write_json(path, record)

    def set_stage(self, stage):
        if stage not in STAGES:
            raise ValueError('invalid deployment phase')
        self.stage = stage

    def flush_reports(self):
        for path in self.journals.glob('*.json'):
            record = read_json(path)
            self.validate_record(record, path.stem)
            if record['status'] not in TERMINAL or record['reported']:
                continue
            body = {k: record[k] for k in ('status', 'stage', 'observation') if k in record}
            try:
                # Sample-bearing heartbeats and terminal observations must reach the
                # center in this order. Node-only heartbeats never wait for this lock.
                with self.observation_lock:
                    self.invalidate_sample(record['instance_id'])
                    self.client.json('POST', f"/api/v1/node/tasks/{record['id']}/report", body)
            except PlatformError as error:
                if error.status != 409:
                    continue
                # Center already holds a different terminal result (e.g. cancelled).
                # Retain journal evidence; never turn a result conflict into a replay.
            except (RuntimeError, OSError):
                continue
            record['reported'] = True
            write_json(path, record)

    def invalidate_sample(self, instance_id):
        with self.samples_lock:
            self.sample_epochs[instance_id] = self.sample_epochs.get(instance_id, 0) + 1
            self.samples.pop(instance_id, None)

    def sample_observations(self):
        # A lock held by ctl is not an observation of missing/stopped containers.
        # Keep the previous sample timestamp until this instance can be inspected.
        busy = self.worker is not None and self.worker.is_alive()
        self.docker_ok = self.executor.docker_available()
        for instance in self.instances:
            if busy and instance['id'] == self.active_instance:
                continue
            with self.samples_lock:
                epoch = self.sample_epochs.get(instance['id'], 0)
            observation = self.executor.observe(instance)
            if not observation.pop('_busy', False):
                with self.samples_lock:
                    if self.sample_epochs.get(instance['id'], 0) == epoch:
                        self.samples[instance['id']] = observation

    def heartbeat(self, sample=True):
        if sample:
            self.sample_observations()
        ordered = self.observation_lock.acquire(blocking=False)
        try:
            self._heartbeat(ordered)
        finally:
            if ordered:
                self.observation_lock.release()

    def _heartbeat(self, allow_samples):
        with self.samples_lock:
            samples = dict(self.samples) if allow_samples else {}
        busy = self.worker is not None and self.worker.is_alive()
        observations = [v for k, v in samples.items() if not (busy and k == self.active_instance)]
        response = self.client.json('POST', '/api/v1/node/heartbeat', {
            'agent_version': __version__, 'platform': platform.system().lower() + '/' + platform.machine(),
            'docker_available': self.docker_ok, 'instances': observations})
        if not isinstance(response, dict) or type(response.get('enabled')) is not bool or not isinstance(response.get('instances'), list):
            raise ValueError('invalid node heartbeat response')
        if len(response['instances']) > 1000:
            raise ValueError('too many instances on one node')
        self.instances = [instance_identity(value) for value in response['instances']]
        self.enabled = response['enabled']
        # Private local evidence is emitted only after a validated center heartbeat.
        # The independent updater binds it to the restarted systemd process.
        write_json(self.home / 'readiness.json', {'node_id': self.node_id,
            'version': 'v' + __version__, 'pid': os.getpid(),
            'heartbeat_ns': time.time_ns(), 'enabled': self.enabled})
        with self.samples_lock:
            for key, value in samples.items():
                if self.samples.get(key) is value:
                    self.samples.pop(key, None)

    def run_once(self):
        self.flush_reports()
        self.heartbeat()
        if self.enabled:
            response = self.client.json('POST', '/api/v1/node/poll', {'wait_seconds': 0})
            if response.get('task'):
                self.execute_task(response['task'])
                self.flush_reports()

    def _execute_background(self, task):
        try:
            self.execute_task(task)
        except BaseException:
            # Fatal journal errors must stop this daemon, not accept more tasks.
            self.worker_error = True

    def run(self, stop=None):
        stop = stop or threading.Event()
        background_stop = threading.Event()
        def background(operation):
            while not background_stop.is_set():
                try:
                    operation()
                except (RuntimeError, OSError):
                    pass  # Never log response/config/credential contents.
                except ValueError:
                    self.worker_error = True
                    return
                background_stop.wait(15)
        # Heartbeats cannot queue behind Docker calls or pending result uploads.
        sampler = threading.Thread(target=background, args=(self.sample_observations,), daemon=True)
        sender = threading.Thread(target=background, args=(lambda: self.heartbeat(sample=False),), daemon=True)
        sampler.start()
        sender.start()
        next_progress = 0
        backoff = 1
        try:
            while not stop.is_set() or (self.worker and self.worker.is_alive()):
                if self.worker_error:
                    raise RuntimeError('agent worker interrupted; restart to recover execution journal')
                try:
                    self.flush_reports()
                    current_id = self.current_id
                    if current_id and time.monotonic() >= next_progress:
                        self.client.json('POST', f'/api/v1/node/tasks/{current_id}/report', {'status': 'running', 'stage': self.stage})
                        next_progress = time.monotonic() + 15
                    busy = self.worker is not None and self.worker.is_alive()
                    if not busy and not stop.is_set() and self.enabled:
                        response = self.client.json('POST', '/api/v1/node/poll', {'wait_seconds': 10})
                        if not isinstance(response, dict):
                            raise ValueError('invalid task poll response')
                        if response.get('task'):
                            validate_task(response['task'], self.node_id)
                            self.stage = 'accepted'
                            self.active_instance = response['task']['instance_id']
                            self.worker = threading.Thread(target=self._execute_background, args=(response['task'],), daemon=False)
                            self.worker.start()
                    else:
                        time.sleep(1)
                    backoff = 1
                except (RuntimeError, OSError):
                    print('Agent connection unavailable; retaining local execution and pending reports.', file=sys.stderr)
                    time.sleep(backoff)
                    backoff = min(30, backoff * 2)
            self.flush_reports()
        finally:
            background_stop.set()
            sender.join(timeout=1)
            sampler.join(timeout=1)


def enroll(server, token, home=DEFAULT_HOME):
    home = private_directory(home)
    client = PlatformClient(Credentials(validate_origin(server), validate_token(token)))
    with service_lock(home / '.lock'):
        response = client.json('POST', '/api/v1/node/enroll', {'agent_version': __version__,
            'platform': platform.system().lower() + '/' + platform.machine(), 'name': platform.node()})
        if not isinstance(response, dict):
            raise ValueError('invalid node enrollment response')
        validate_id(response.get('node_id'))
        validate_token(response.get('token'))
        existing = home / 'node.json'
        if existing.exists() and read_json(existing).get('node_id') != response['node_id']:
            raise ValueError('agent home belongs to a different node; use a new private directory')
        write_json(existing, {'server': client.server, 'node_id': response['node_id'], 'token': response['token']})
    return response['node_id']


def service_unit(executable, home, root, config_root):
    def quote(value):
        value = str(value)
        if not value.startswith('/') or any(ord(c) < 32 for c in value):
            raise ValueError('systemd paths must be absolute Linux paths without control characters')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    command = f'{quote(executable)} agent run --home {quote(home)} --root {quote(root)} --config-root {quote(config_root)}'
    return ('[Unit]\nDescription=ctl deployment node agent\nWants=network-online.target\nAfter=network-online.target docker.service\n\n'
            '[Service]\nType=simple\nUser=root\nUMask=0077\nExecStart=' + command + '\nRestart=always\nRestartSec=5\n'
            'TimeoutStopSec=900\nKillMode=mixed\n\n[Install]\nWantedBy=multi-user.target\n')


def agent_process_matches(host, node_id, version, after_ns=0):
    try:
        info = host.service_info()
        marker = read_json(host.agent_home/'readiness.json')
        heartbeat = marker.get('heartbeat_ns')
        return (info.get('ActiveState') == 'active' and int(info.get('MainPID', '0')) > 0
            and marker.get('node_id') == node_id and marker.get('version') == version
            and marker.get('pid') == int(info['MainPID'])
            and type(heartbeat) is int and after_ns < heartbeat <= time.time_ns()
            and time.time_ns()-heartbeat < 90*1_000_000_000)
    except (ValueError, RuntimeError, OSError):
        return False


def install(args):
    """Install a node and its independent updater using the original identity."""
    import re
    import subprocess
    from types import SimpleNamespace
    from . import updater, bootstrap
    updater.require_root()
    if (not re.fullmatch(r'[A-Za-z0-9_-]+\.service', args.service_name)
        or args.service_name.startswith('ctl-updater')):
        raise ValueError('invalid Agent service name')
    executable = updater.trusted(args.executable)
    updater.installer_identity(executable)
    home = private_directory(updater.trusted(args.home, private=True))
    root = updater.trusted(args.root)
    config_root = updater.trusted(args.config_root)
    content = service_unit(executable, home, root, config_root).encode()
    unit = updater.trusted(Path('/etc/systemd/system')/args.service_name)
    updater.trusted(home/'.install.lock')
    with service_lock(home/'.install.lock'):
        if unit.exists() and (not unit.is_file() or unit.read_bytes() != content):
            raise ValueError('existing Agent unit differs; choose a separate service name')
        identity_path = home/'node.json'
        if identity_path.exists():
            identity = read_json(identity_path)
            validate_id(identity.get('node_id'))
            server = validate_origin(identity.get('server'))
            validate_token(identity.get('token'))
            if args.server and validate_origin(args.server) != server:
                raise ValueError('existing Agent belongs to a different server')
        else:
            if not args.server: raise ValueError('new Agent installation requires --server')
            token = read_private(updater.trusted(args.token_file, private=True)).decode('ascii').strip() if args.token_file else getpass.getpass('Enrollment token: ')
            enroll(args.server, token, home)
        if not unit.exists(): bootstrap.atomic_write(unit, content, 0o644)
        host = updater.Host({'cli_path':str(executable),'kind':'node','agent_service':args.service_name,
            'agent_home':str(home)})
        try:
            updater_home = private_directory(updater.trusted(args.updater_home, private=True))
            # The helper takes this lock for each complete protocol iteration.
            # Never restart the Agent while an updater is executing or recovering.
            with updater.setup_lock(updater_home):
                updater.check_setup_journals(updater_home)
                version = updater.installer_identity(executable)['version']
                node_id = read_json(identity_path)['node_id']
                host.command(['systemctl','daemon-reload'])
                refresh = (host.service_info().get('ActiveState') == 'active'
                    and not agent_process_matches(host, node_id, version))
                if refresh:
                    host.stop_agent()
                after_ns = time.time_ns()
                host.command(['systemctl','enable','--now',args.service_name], timeout=60)
                host.command(['systemctl','is-active','--quiet',args.service_name])
                if refresh:
                    deadline = time.monotonic()+120
                    while not agent_process_matches(host, node_id, version, after_ns):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('new Agent process has no fresh heartbeat')
                        time.sleep(1)
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            raise RuntimeError('Agent installed; Agent service not ready and automatic updates not configured. '
                'Repair by repeating ctl agent install with the same local paths.') from None
        try:
            updater.setup(SimpleNamespace(kind='node', home=args.updater_home, cli_path=str(executable),
                agent_home=str(home), agent_service=args.service_name, server_home='/opt/ctl-platform',
                service_name=args.updater_service))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            raise RuntimeError('Agent installed and started; automatic updates not configured. '
                'Repair by repeating ctl agent install with the same local paths.') from None


def add_commands(sub):
    command = sub.add_parser('agent', help='enroll or run the outbound deployment node agent')
    operations = command.add_subparsers(dest='agent_command', required=True)
    for name in ('enroll', 'run', 'service', 'install'):
        operation = operations.add_parser(name)
        operation.add_argument('--home', default=DEFAULT_HOME)
        if name in ('enroll', 'install'):
            operation.add_argument('--server', required=name == 'enroll')
            operation.add_argument('--token-file', help='private enrollment token file; otherwise hidden prompt')
        if name != 'enroll':
            operation.add_argument('--root', default='/opt/deployments')
            operation.add_argument('--config-root', default='/etc/deployctl')
        if name in ('service', 'install'):
            operation.add_argument('--executable', default='/usr/local/bin/ctl')
        if name == 'install':
            operation.add_argument('--service-name', default='ctl-agent.service')
            operation.add_argument('--updater-home', default='/var/lib/ctl-updater-node')
            operation.add_argument('--updater-service', default='ctl-updater-node.service')


def dispatch(args):
    if args.agent_command == 'install':
        install(args)
        print('OK: Agent and automatic updater installed and started')
        return
    if args.agent_command == 'service':
        print(service_unit(args.executable, args.home, args.root, args.config_root), end='')
        return
    if platform.system() != 'Linux':
        raise RuntimeError('deployment node enrollment and daemon require Linux')
    if args.agent_command == 'enroll':
        token = read_private(args.token_file).decode('ascii').strip() if args.token_file else getpass.getpass('Enrollment token: ')
        node = enroll(args.server, token, args.home)
        print(f'OK: enrolled node {node}; run ctl agent run or install the systemd unit')
        return
    home = private_directory(args.home)
    with service_lock(home / '.lock'):
        identity = read_json(home / 'node.json')
        client = PlatformClient(Credentials(identity['server'], identity['token']))
        manager = Manager(root=args.root, config_root=args.config_root, driver=AgentDockerDriver())
        worker = Agent(home, identity['node_id'], client, RuntimeExecutor(manager, client))
        stop = threading.Event()
        def stopping(signum, frame):
            stop.set()
        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
        worker.run(stop)
