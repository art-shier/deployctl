"""Content-addressed stack snapshots with stable, per-service secret mounts."""
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import yaml

from .contract import mapping, version
from .multiservice_contract import config_key
from .multiservice_release import validate_multiservice_release, render_multiservice_compose, _variable
from .runtime_snapshot import reject_links
from .service_state import (_directory, _write, _sync_directory, prepare_service_bundle,
                            verify_service_bundle, MAX_STATE)


def validate_stack_values(values):
    mapping(values, {'config', 'secrets'}, {'config', 'secrets'}, 'service values')
    for kind, pool in values.items():
        if not isinstance(pool, dict) or len(pool) > 128:
            raise ValueError('service values must be bounded mappings')
        for key, value in pool.items():
            config_key(key)
            if not isinstance(value, str) or '\x00' in value or len(value.encode('utf-8')) > 65536:
                raise ValueError('invalid service configuration value')
    if set(values['config']) & set(values['secrets']):
        raise ValueError('configuration and secret names overlap')
    if len(json.dumps(values).encode()) > 256 * 1024:
        raise ValueError('service configuration exceeds size limit')
    return copy.deepcopy(values)


def validate_generation(generation):
    if generation is not None and (not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation)):
        raise ValueError('invalid stack generation')
    return generation


def snapshot_root(root, generation=None):
    validate_generation(generation)
    root = Path(root).absolute()
    return root / 'generations' / generation if generation is not None else root


def validate_stack_ref(ref):
    mapping(ref, {'version', 'snapshot', 'generation'}, {'version', 'snapshot'}, 'stack reference')
    validate_generation(ref.get('generation'))
    version(ref['version'])
    if not isinstance(ref['snapshot'], str) or not re.fullmatch('[a-f0-9]{64}', ref['snapshot']):
        raise ValueError('invalid stack snapshot reference')
    return dict(ref)


@dataclass
class StackSnapshot:
    ref: dict
    directory: Path
    release: dict
    values: dict
    bundles: dict
    overrides: dict
    management: dict | None

    def environment(self, project):
        result = {k: v for k, v in os.environ.items() if not k.startswith(('COMPOSE_', 'DEPLOYCTL_'))}
        result.update(COMPOSE_DISABLE_ENV_FILE='1', DEPLOYCTL_NETWORK=project+'-internal')
        for name, folder in self.bundles.items():
            result[_variable('ENV', name)] = str(folder / 'environment.env')
            result[_variable('SECRET', name)] = str(folder / 'secrets')
        for name in self.release['deployment']['volumes']:
            result[_variable('VOLUME', name)] = project+'-'+name
        return result


def _read(path):
    reject_links(path)
    if not path.is_file() or path.stat().st_size > MAX_STATE or path.stat().st_mode & 0o077:
        raise ValueError('invalid or unprotected stack snapshot')
    return path.read_bytes()


def _nodes(release, active_jobs):
    jobs = release['deployment']['jobs']
    if not isinstance(active_jobs, list) or len(set(active_jobs)) != len(active_jobs) or set(active_jobs) - set(jobs):
        raise ValueError('invalid active snapshot jobs')
    return {**release['deployment']['services'], **{name:jobs[name] for name in active_jobs}}


def _compose(release, active_jobs):
    compose = render_multiservice_compose(release)
    for name in release['deployment']['jobs']:
        if name not in active_jobs: compose['services'].pop(name)
    return yaml.safe_dump(compose, sort_keys=True).encode()


def load_stack_snapshot(root, ref, application):
    ref = validate_stack_ref(ref)
    root = snapshot_root(root, ref.get('generation'))
    directory = root / 'stacks' / ref['snapshot']
    raw = _read(directory / 'snapshot.json')
    if hashlib.sha256(raw).hexdigest() != ref['snapshot']:
        raise ValueError('stack snapshot integrity failure')
    try: data = json.loads(raw)
    except (UnicodeError, ValueError) as exc: raise ValueError('invalid stack snapshot JSON') from exc
    mapping(data, {'release', 'values', 'overrides', 'management', 'active_jobs', 'generation'}, {'release', 'values', 'overrides', 'management', 'active_jobs'}, 'stack snapshot')
    if data.get('generation') != ref.get('generation'):
        raise ValueError('stack generation identity mismatch')
    release = validate_multiservice_release(data['release'])
    if release['application'] != application or release['version'] != ref['version']:
        raise ValueError('stack snapshot identity mismatch')
    values = validate_stack_values(data['values'])
    from .runtime_config import validate_values
    overrides = validate_values(data['overrides'], 'env-var')
    if data['management'] is not None and not isinstance(data['management'], dict):
        raise ValueError('invalid stack management provenance')
    nodes = _nodes(release, data['active_jobs'])
    expected = _compose(release, data['active_jobs'])
    if _read(directory / 'compose.yaml') != expected:
        raise ValueError('stack Compose integrity failure')
    config = release['deployment']
    bundles = {name: verify_service_bundle(root, name, spec, values)
               for name, spec in nodes.items()}
    return StackSnapshot(ref, directory, release, values, bundles, overrides, data['management'])


def create_stack_snapshot(root, release, values, overrides=None, management=None, active_jobs=None, generation=None):
    validate_generation(generation)
    release = validate_multiservice_release(release)
    values = validate_stack_values(values)
    from .runtime_config import validate_values
    overrides = validate_values(overrides or {}, 'env-var')
    active_jobs = list(release['deployment']['jobs']) if active_jobs is None else list(active_jobs)
    nodes = _nodes(release, active_jobs)
    data = {'release': release, 'values': values, 'overrides': overrides, 'management': management, 'active_jobs': active_jobs}
    if generation is not None: data['generation'] = generation
    raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_STATE: raise ValueError('stack snapshot exceeds size limit')
    digest = hashlib.sha256(raw).hexdigest()
    ref = {'version': release['version'], 'snapshot': digest}
    if generation is not None: ref['generation'] = generation
    base_root = root
    root = _directory(snapshot_root(root, generation))
    home = _directory(root / 'stacks')
    target = home / digest
    reject_links(target)
    if target.exists(): return load_stack_snapshot(base_root, ref, release['application'])
    config = release['deployment']
    for name, spec in nodes.items():
        prepare_service_bundle(root, name, spec, values)
    pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=home))
    try:
        _write(pending / 'snapshot.json', raw)
        _write(pending / 'compose.yaml', _compose(release, active_jobs))
        _sync_directory(pending)
        if not target.exists(): pending.rename(target); _sync_directory(home)
    finally:
        if pending.exists(): shutil.rmtree(pending)
    return load_stack_snapshot(base_root, ref, release['application'])
