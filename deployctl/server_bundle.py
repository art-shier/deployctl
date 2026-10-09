"""Checked release delivery for the independent, multi-service ctl platform."""

import gzip
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import quote
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener
from urllib.error import URLError

from . import __version__
from .contract import DIGEST_IMAGE, load_yaml, version
from .download import acquire_release, fetch
from .runtime_snapshot import reject_links

FILES = {'server-release.json', 'control-deploy/bootstrap.sh',
         'control-deploy/bootstrap_config.py', 'control-deploy/compose.yaml'}
MAX_EXPANDED = 512 * 1024
MAX_MEMBER = 256 * 1024
BOOTSTRAP_TIMEOUT = 1800
LOCK_HOME = Path('/run/ctl-platform-cli')


def resolve_server_release(requested='latest'):
    if requested != 'latest':
        requested = requested if requested.startswith('v') else 'v' + requested
        version(requested)
    route = 'latest' if requested == 'latest' else 'tags/' + quote(requested, safe='')
    headers = {'User-Agent': 'team-deployctl', 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token: headers['Authorization'] = 'Bearer ' + token
    release = json.loads(fetch(Request('https://api.github.com/repos/art-shier/deployctl/releases/' + route,
                                      headers=headers), 1024 * 1024))
    if not isinstance(release, dict): raise ValueError('invalid official server release metadata')
    tag = release.get('tag_name'); version(tag)
    if requested != 'latest' and tag != requested: raise ValueError('server release version does not match requested version')
    if release.get('draft') is not False or (requested == 'latest' and release.get('prerelease') is not False):
        raise ValueError('server release is not a published stable version')
    assets = release.get('assets')
    if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
        raise ValueError('invalid official server release assets')
    name = 'ctl-platform-' + tag + '.tar.gz'
    for expected in (name, name + '.sha256'):
        if sum(item.get('name') == expected for item in assets) != 1:
            raise ValueError('official Release must contain exactly one ' + expected)
    return f'https://github.com/art-shier/deployctl/releases/download/{quote(tag, safe="")}/{name}', tag


def validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'application', 'version',
            'minimum_deployctl_version', 'image', 'files'} or type(value['schema_version']) is not int
            or value['schema_version'] != 1 or value['application'] != 'ctl-platform'):
        raise ValueError('unsupported ctl server release manifest')
    version(value['version'])
    minimum = value['minimum_deployctl_version']
    if not isinstance(minimum, str) or not re.fullmatch(r'\d+\.\d+\.\d+', minimum):
        raise ValueError('invalid minimum CLI version')
    if tuple(map(int, minimum.split('.'))) > tuple(map(int, __version__.split('.'))):
        raise ValueError('ctl server release requires a newer CLI; run ctl self-update')
    if not isinstance(value['image'], str) or not DIGEST_IMAGE.fullmatch(value['image']):
        raise ValueError('server image must be pinned to its SHA256 digest')
    hashes = value['files']
    if (not isinstance(hashes, dict) or set(hashes) != FILES - {'server-release.json'}
            or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in hashes.values())):
        raise ValueError('invalid ctl server file manifest')
    return value


def build_bundle(source, output, image, release_version):
    source, output = Path(source), Path(output)
    content = {name: (source/name).read_bytes() for name in FILES - {'server-release.json'}}
    manifest = validate_manifest({'schema_version': 1, 'application': 'ctl-platform', 'version': release_version,
        'minimum_deployctl_version': '1.7.0', 'image': image,
        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()}})
    content['server-release.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
    if any(len(raw) > MAX_MEMBER for raw in content.values()): raise ValueError('server release member too large')
    output.mkdir(parents=True, exist_ok=True)
    package = output/f'ctl-platform-{release_version}.tar.gz'
    with package.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(content.items()):
                item = tarfile.TarInfo(name); item.size = len(raw); item.mode = 0o600
                archive.addfile(item, io.BytesIO(raw))
    read_bundle(package)
    Path(str(package)+'.sha256').write_text(hashlib.sha256(package.read_bytes()).hexdigest()+'  '+package.name+'\n', encoding='ascii')
    return package


def read_bundle(package):
    path = Path(package)
    if path.stat().st_size > MAX_EXPANDED: raise ValueError('ctl server bundle too large')
    content = {}
    try:
        with gzip.open(path, 'rb') as stream: raw = stream.read(MAX_EXPANDED+1)
        if len(raw) > MAX_EXPANDED: raise ValueError('expanded server bundle too large')
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for item in archive:
                if item.name not in FILES or item.name in content or not item.isfile() or not 0 <= item.size <= MAX_MEMBER:
                    raise ValueError('unsafe ctl server archive entry')
                content[item.name] = archive.extractfile(item).read(MAX_MEMBER+1)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError('invalid ctl server archive') from exc
    if set(content) != FILES: raise ValueError('incomplete ctl server bundle')
    manifest = validate_manifest(load_yaml(content['server-release.json'].decode('utf-8')))
    for name, digest in manifest['files'].items():
        if hashlib.sha256(content[name]).hexdigest() != digest: raise ValueError('ctl server member checksum mismatch')
    return manifest, content


def require_runtime():
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('ctl server commands require Linux root')
    try:
        result = subprocess.run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Docker Compose version check timed out') from exc
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.\d+(?:[-+].*)?', result.stdout.strip())
    if result.returncode or not match or tuple(map(int, match.groups())) < (2, 30):
        raise ValueError('Docker Engine and Docker Compose >=2.30 are required')


def private(path, directory=False):
    path = Path(path); reject_links(path)
    info = path.stat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError('ctl server paths require current ownership and private permissions')
    return path


def trusted_parents(path):
    reject_links(path)
    for parent in Path(path).parents:
        if not parent.exists(): continue
        info = parent.stat()
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or (info.st_mode & 0o022 and not sticky_root)):
            raise ValueError('ctl server ancestors must be trusted and not writable by other users')


@contextmanager
def locked(path):
    import fcntl
    trusted_parents(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    trusted_parents(path); private(path.parent, True); reject_links(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(path)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise ValueError('another ctl server operation is running') from exc
        yield
    finally: os.close(fd)


def inspect_docker(argv):
    try:
        return subprocess.run(['docker', *argv], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Docker inspection timed out; check daemon availability') from exc


def check_project(home):
    result = inspect_docker(['ps', '-aq', '--filter', 'label=com.docker.compose.project=ctl-platform'])
    if result.returncode: raise RuntimeError('cannot inspect the existing ctl platform')
    expected = {'/run/ctl-keys': home/'keys', '/cert/signing.crt': home/'keys/signing.crt',
        '/var/lib/ctl/artifacts': home/'artifacts', '/var/lib/registry': home/'registry',
        '/var/lib/postgresql/data': home/'database'}
    for identifier in result.stdout.split():
        if not re.fullmatch('[a-f0-9]{12,64}', identifier): raise ValueError('invalid Docker container identifier')
        inspected = inspect_docker(['inspect', '--format', '{{json .Mounts}}', identifier])
        if inspected.returncode: raise RuntimeError('cannot inspect ctl platform instance mounts')
        mounts = json.loads(inspected.stdout)
        bindings = [mount for mount in mounts if mount.get('Destination') in expected]
        if not bindings or any(mount.get('Source') != str(expected[mount['Destination']]) for mount in bindings):
            raise ValueError('another ctl-platform instance uses a different home on this Docker host')


def write_state(path, value):
    reject_links(path)
    fd, temporary = tempfile.mkstemp(prefix='.server-state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def read_state(path):
    private(path)
    if path.stat().st_size > 65536: raise ValueError('invalid server state')
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or set(state) != {'schema_version', 'current', 'pending'} or type(state['schema_version']) is not int or state['schema_version'] != 1:
        raise ValueError('invalid server state')
    for entry in (state['current'], state['pending']):
        if entry is None: continue
        if not isinstance(entry, dict) or set(entry) != {'sha256', 'image', 'version'} or not re.fullmatch('[a-f0-9]{64}', str(entry['sha256'])):
            raise ValueError('invalid server state')
        version(entry['version'])
        if not isinstance(entry['image'], str) or not DIGEST_IMAGE.fullmatch(entry['image']): raise ValueError('invalid server state')
    return state


def run_bootstrap(bundle, home, image, options):
    check_project(home)
    argv = ['/bin/bash', '--noprofile', '--norc', str(bundle/'control-deploy/bootstrap.sh'), '--home', str(home), '--image', image]
    for name, value in options.items():
        if value is not None: argv += ['--'+name.replace('_', '-'), str(value)]
    run_server_command(argv, BOOTSTRAP_TIMEOUT, 'server bootstrap')
    wait_ready(read_instance(home)['api_port'])


def run_server_command(argv, timeout, label):
    if threading.current_thread() is not threading.main_thread(): raise RuntimeError('server commands require the main thread')
    import signal
    from .hooks import cleanup_group
    process, terminating, cleaning = None, False, False
    def interrupt(signum, frame):
        nonlocal terminating
        terminating = True
        if process is not None and not cleaning: raise KeyboardInterrupt()
    signals = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    previous = {s: signal.getsignal(s) for s in signals}
    environment = {k: v for k, v in os.environ.items()
                   if k not in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'GH_TOKEN', 'GITHUB_TOKEN')
                   and not k.startswith(('COMPOSE_', 'CTL_'))}
    try:
        for s in signals: signal.signal(s, interrupt)
        process = subprocess.Popen(argv, env=environment, start_new_session=True)
        if terminating: raise KeyboardInterrupt()
        try: process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(label + ' timed out; inspect containers and retry the same release') from exc
    finally:
        cleaning = True
        try:
            if process is not None: cleanup_group(process)
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)
    if terminating: raise KeyboardInterrupt()
    if process.returncode:
        raise RuntimeError(label + ' failed; inspect diagnostics and retry the same release')


def deploy_server(source=None, home='/opt/ctl-platform', expected_sha256=None, upgrade=False,
                  release_version=None, progress=None, **options):
    if source and release_version: raise ValueError('--release conflicts with --version')
    from .progress import Progress
    progress = progress or Progress(enabled=False)
    require_runtime()
    home = Path(home).expanduser().absolute(); trusted_parents(home)
    if '..' in home.parts or any(c in str(home) for c in '\n\r$# \\'):
        raise ValueError('instance directory must be a plain absolute path without parent traversal')
    home.mkdir(parents=True, exist_ok=True, mode=0o700); trusted_parents(home); private(home, True)
    with locked(LOCK_HOME/'operation.lock'), locked(home/'.server.lock'):
        with tempfile.TemporaryDirectory(prefix='ctl-server-download-') as cache:
            selected_version = None
            if source is None:
                with progress.stage('Resolving official ctl server Release'):
                    source, selected_version = resolve_server_release(release_version or 'latest')
            with progress.stage('Downloading and verifying ctl server bundle'):
                package = acquire_release(str(source), cache, expected_sha256, progress=progress)
            manifest, content = read_bundle(package)
            if selected_version and manifest['version'] != selected_version:
                raise ValueError('server bundle version does not match selected Release')
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            state_path = home/'server-state.json'; reject_links(state_path)
            state = {'schema_version': 1, 'current': None, 'pending': None}
            if state_path.exists():
                state = read_state(state_path)
            if state['pending'] and state['pending']['sha256'] != digest:
                raise ValueError('pending server operation: retry its exact release before changing versions')
            instance = home/'instance.json'; reject_links(instance)
            installed = bool(state['current']) or instance.exists()
            if upgrade and not installed: raise ValueError('ctl server is not installed; use server install')
            if not upgrade and installed and not (state['pending'] and not state['current']):
                raise ValueError('ctl server already exists; use server upgrade')
            releases = home/'server-releases'; reject_links(releases)
            releases.mkdir(mode=0o700, exist_ok=True); private(releases, True)
            bundle = releases/digest; reject_links(bundle)
            if not bundle.exists():
                with tempfile.TemporaryDirectory(prefix='.stage-', dir=releases) as stage:
                    for name, raw in content.items():
                        path = Path(stage)/name; path.parent.mkdir(mode=0o700, exist_ok=True)
                        with path.open('xb') as stream: stream.write(raw)
                        path.chmod(0o600)
                    os.rename(stage, bundle)
            private(bundle, True)
            for name, raw in content.items():
                path = private(bundle/name)
                if path.stat().st_size != len(raw) or path.read_bytes() != raw: raise ValueError('cached server bundle was modified')
            preserve_archive(bundle, package, digest)
            descriptor = {'version': manifest['version'], 'image': manifest['image'], 'sha256': digest}
            state['pending'] = descriptor; write_state(state_path, state)
            with progress.stage('Starting ctl server and checking readiness'):
                run_bootstrap(bundle, home, manifest['image'], options)
            state['current'] = descriptor; state['pending'] = None; write_state(state_path, state)
            return state


def preserve_archive(bundle, package, expected):
    path = bundle/'.verified-release.tar.gz'; reject_links(path)
    if path.exists():
        private(path)
        if path.stat().st_size > MAX_EXPANDED or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('cached verified server archive was modified')
    else:
        from .bootstrap import atomic_write
        raw = package.read_bytes()
        if len(raw) > MAX_EXPANDED or hashlib.sha256(raw).hexdigest() != expected: raise ValueError('server archive checksum mismatch')
        atomic_write(path, raw, 0o600)
    return path


def installed_content(bundle, active):
    archive = bundle/'.verified-release.tar.gz'; reject_links(archive)
    if not archive.exists():
        # Older CLI versions cached only expanded files. Verify the exact recorded
        # archive once; custom bundles can be anchored by upgrading their original URL.
        tag = active['version']
        source = f'https://github.com/art-shier/deployctl/releases/download/{quote(tag, safe="")}/ctl-platform-{tag}.tar.gz'
        from .progress import Progress
        progress = Progress()
        with progress.stage('Verifying legacy ctl server cache against its recorded SHA256'):
            with tempfile.TemporaryDirectory(prefix='ctl-server-legacy-') as cache:
                package = acquire_release(source, cache, expected_sha256=active['sha256'], progress=progress)
                _, original = read_bundle(package)
                for name, raw in original.items():
                    path = private(bundle/name)
                    if path.stat().st_size != len(raw) or path.read_bytes() != raw: raise ValueError('cached server bundle was modified')
                preserve_archive(bundle, package, active['sha256'])
    private(archive)
    if archive.stat().st_size > MAX_EXPANDED or hashlib.sha256(archive.read_bytes()).hexdigest() != active['sha256']:
        raise ValueError('cached verified server archive was modified')
    manifest, content = read_bundle(archive)
    for name, raw in content.items():
        path = private(bundle/name)
        if path.stat().st_size != len(raw) or path.read_bytes() != raw: raise ValueError('cached server bundle was modified')
    return manifest


def read_instance(home):
    path = private(home/'instance.json')
    if path.stat().st_size > 16384: raise ValueError('invalid instance configuration')
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict) or type(cfg.get('external_database')) is not bool or type(cfg.get('api_port')) is not int or not 1 <= cfg['api_port'] <= 65535:
        raise ValueError('invalid instance configuration')
    return cfg


def wait_ready(port, timeout=120):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/v1/health/ready', timeout=min(5, max(.1, deadline-time.monotonic()))) as response:
                body = response.read(1025)
                if response.status == 200 and len(body) <= 1024 and json.loads(body) == {'status': 'ready'}: return
        except (URLError, TimeoutError, OSError, ValueError): pass
        time.sleep(min(1, max(0, deadline-time.monotonic())))
    raise RuntimeError('ctl server readiness timed out; inspect server-status and server-logs')


def operate_server(action, home='/opt/ctl-platform', tail=100):
    if action not in ('restart', 'start', 'stop', 'status', 'logs'): raise ValueError('unsupported server operation')
    if action == 'logs' and (type(tail) is not int or not 1 <= tail <= 10000): raise ValueError('--tail must be 1..10000')
    require_runtime()
    home = Path(home).expanduser().absolute(); trusted_parents(home)
    private(home, True)
    with locked(LOCK_HOME/'operation.lock'), locked(home/'.server.lock'):
        state = read_state(home/'server-state.json')
        if state.get('pending') and action not in ('status', 'logs'):
            raise ValueError('pending server operation: retry its exact release before operating the server')
        active = state.get('current') or state.get('pending')
        if not isinstance(active, dict) or not re.fullmatch('[a-f0-9]{64}', str(active.get('sha256'))):
            raise ValueError('ctl server is not installed; use server-install')
        bundle = private(home/'server-releases'/active['sha256'], True)
        manifest = installed_content(bundle, active)
        if manifest['version'] != active.get('version') or manifest['image'] != active.get('image'):
            raise ValueError('cached server manifest differs from installed state')
        cfg = read_instance(home)
        env_path = private(home/'compose.env')
        if action not in ('status', 'logs'):
            if env_path.stat().st_size > 65536: raise ValueError('invalid Compose environment')
            fields = {}
            for line in env_path.read_text().splitlines():
                if not line or line.startswith('#'): continue
                key, separator, value = line.partition('=')
                if not separator or key in fields: raise ValueError('invalid Compose environment')
                fields[key] = value
            if fields.get('CTL_IMAGE') != manifest['image'] or fields.get('CTL_HOME') != str(home):
                raise ValueError('Compose environment differs from installed server; retry its exact release')
        check_project(home)
        argv = ['docker', 'compose', '--project-name', 'ctl-platform', '--env-file', str(env_path),
                '-f', str(bundle/'control-deploy/compose.yaml')]
        if not cfg['external_database']: argv += ['--profile', 'database']
        commands = {'restart': ['restart', '--timeout', '30'], 'stop': ['stop', '--timeout', '30'],
                    'start': ['up', '-d', '--wait', '--wait-timeout', '120', '--pull', 'never'],
                    'status': ['ps', '--all'], 'logs': ['logs', '--no-color', '--tail', str(tail)]}
        run_server_command(argv + commands[action], 180, 'server ' + action)
        if action in ('restart', 'start'): wait_ready(cfg['api_port'])
        return state
