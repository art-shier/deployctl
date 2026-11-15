"""Private, immutable service bundles and durable deployment inventory."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .contract import validate_name
from .runtime_snapshot import reject_links
from .service_plan import service_fingerprint, service_values

MAX_STATE = 4 * 1024 * 1024


def _directory(path):
    path = Path(path).absolute(); reject_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _write(path, raw):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _check_bundle(path, content):
    reject_links(path)
    actual = set()
    for item in path.rglob('*'):
        reject_links(item)
        if item.is_file(): actual.add(item.relative_to(path).as_posix())
        elif not item.is_dir(): raise ValueError('service bundle integrity failure')
    if actual != set(content):
        raise ValueError('service bundle integrity failure')
    for name, expected in content.items():
        item = path / name
        if item.stat().st_size != len(expected) or item.read_bytes() != expected:
            raise ValueError('service bundle integrity failure')
        if item.stat().st_mode & 0o077:
            raise ValueError('service bundle secret permissions are too broad')


def prepare_service_bundle(root, name, spec, values):
    validate_name(name, 'service')
    root = _directory(root)
    home = _directory(root / 'services' / name)
    environment, files = service_values(spec, values)
    for key, value in environment.items():
        if '\n' in value or '\r' in value:
            raise ValueError(f'environment value must be single-line: {key}')
    content = {'environment.env': ''.join(f'{key}={value}\n' for key, value in sorted(environment.items())).encode()}
    for key, value in files.items(): content['secrets/' + key] = value.encode()
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()}
    content['manifest.json'] = json.dumps(manifest, sort_keys=True).encode()
    target = home / service_fingerprint(spec, values)
    reject_links(target)
    if target.exists():
        _check_bundle(target, content)
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=home))
    try:
        _directory(temporary / 'secrets')
        for name, raw in content.items(): _write(temporary / name, raw)
        _sync_directory(temporary / 'secrets'); _sync_directory(temporary)
        # Caller holds application lock; never overwrite an existing bundle.
        if target.exists():
            _check_bundle(target, content)
            return target
        temporary.rename(target); _sync_directory(home)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    return target


def read_inventory(path):
    path = Path(path).absolute(); reject_links(path)
    if not path.exists(): return {}
    if not path.is_file() or path.stat().st_size > MAX_STATE:
        raise ValueError('invalid inventory file')
    try: result = json.loads(path.read_text())
    except (UnicodeError, json.JSONDecodeError) as exc: raise ValueError('invalid inventory JSON') from exc
    if not isinstance(result, dict) or result.get('schema_version') != 1 or result.get('multi_service') is not True:
        raise ValueError('unsupported service inventory')
    if not isinstance(result.get('services'), dict) or not isinstance(result.get('volumes'), dict):
        raise ValueError('invalid service inventory')
    return result


def write_inventory(path, value):
    path = Path(path).absolute(); reject_links(path); _directory(path.parent)
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if len(raw) > MAX_STATE: raise ValueError('inventory exceeds size limit')
    fd, name = tempfile.mkstemp(prefix='.inventory-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path); _sync_directory(path.parent)
    finally:
        if temporary.exists(): temporary.unlink()
