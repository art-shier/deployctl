"""Checked release delivery for the independent, multi-service ctl platform."""

import gzip
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

from . import __version__
from .contract import DIGEST_IMAGE, load_yaml, version
from .download import acquire_release
from .runtime_snapshot import reject_links

FILES = {'server-release.json', 'control-deploy/bootstrap.sh',
         'control-deploy/bootstrap_config.py', 'control-deploy/compose.yaml'}
MAX_EXPANDED = 512 * 1024
MAX_MEMBER = 256 * 1024


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
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('ctl server install/upgrade requires Linux root')
    result = subprocess.run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=30)
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


def write_state(path, value):
    reject_links(path)
    fd, temporary = tempfile.mkstemp(prefix='.server-state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def run_bootstrap(bundle, home, image, options):
    argv = ['bash', str(bundle/'control-deploy/bootstrap.sh'), '--home', str(home), '--image', image]
    for name, value in options.items():
        if value is not None: argv += ['--'+name.replace('_', '-'), str(value)]
    try:
        result = subprocess.run(argv, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('server bootstrap timed out; inspect containers and retry the same release') from exc
    if result.returncode:
        raise RuntimeError('server bootstrap failed; inspect diagnostics and retry the same release')


def deploy_server(source, home='/opt/ctl-platform', expected_sha256=None, upgrade=False, **options):
    require_runtime()
    import fcntl
    home = Path(home).expanduser().absolute(); reject_links(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700); private(home, True)
    lock = home/'.server.lock'; reject_links(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(lock)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise ValueError('another ctl server operation is running') from exc
        with tempfile.TemporaryDirectory(prefix='ctl-server-download-') as cache:
            package = acquire_release(str(source), cache, expected_sha256)
            manifest, content = read_bundle(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            state_path = home/'server-state.json'; reject_links(state_path)
            state = {'schema_version': 1, 'current': None, 'pending': None}
            if state_path.exists():
                private(state_path)
                if state_path.stat().st_size > 65536: raise ValueError('invalid server state')
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if not isinstance(state, dict) or set(state) != {'schema_version', 'current', 'pending'} or state['schema_version'] != 1:
                    raise ValueError('invalid server state')
                for entry in (state['current'], state['pending']):
                    if entry is not None and (not isinstance(entry, dict) or set(entry) != {'sha256','image','version'}
                            or not re.fullmatch('[a-f0-9]{64}', str(entry['sha256']))): raise ValueError('invalid server state')
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
            descriptor = {'version': manifest['version'], 'image': manifest['image'], 'sha256': digest}
            state['pending'] = descriptor; write_state(state_path, state)
            run_bootstrap(bundle, home, manifest['image'], options)
            state['current'] = descriptor; state['pending'] = None; write_state(state_path, state)
            return state
    finally: os.close(fd)
