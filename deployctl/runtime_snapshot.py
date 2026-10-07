"""Integrity-checked configuration bundles and platform-only Compose mounts."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

import yaml

from .contract import load_yaml, validate_name
from .release import render_compose
from .runtime_config import render_json, render_raw_env, validate_values

CONFIGURATION_ID = re.compile(r'[a-f0-9]{32}')
DIGEST = re.compile(r'[a-f0-9]{64}')
MEMBERS = {'.env.json', 'effective.env', 'overrides.json', '.install-params.json', 'compose.yaml'}
MAX_FILE = 256 * 1024


@dataclass
class ConfigurationSnapshot:
    id: str
    directory: Path
    values: dict
    overrides: dict
    install_params: dict
    sha256: str


def reject_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('managed configuration paths must not contain links')


def validate_id(identifier):
    if not isinstance(identifier, str) or not CONFIGURATION_ID.fullmatch(identifier):
        raise ValueError('invalid configuration snapshot ID')


def render_runtime_compose(release, configuration_id):
    validate_id(configuration_id)
    compose = render_compose(release)
    app = compose['services']['app']
    app['env_file'] = [{'path': '${DEPLOYCTL_EFFECTIVE_ENV_FILE}', 'format': 'raw'}]
    app['environment'] = {'DEPLOYCTL_ENV_FILE': '/run/deployctl/.env.json'}
    app['volumes'] = [{'type': 'bind', 'source': '${DEPLOYCTL_ENV_SOURCE}',
                       'target': '/run/deployctl/.env.json', 'read_only': True,
                       'bind': {'create_host_path': False}}]
    app['labels']['io.team-deploy.configuration'] = configuration_id
    return compose


def bundle(directory):
    reject_links(directory)
    if not directory.is_dir() or {p.name for p in directory.iterdir()} != MEMBERS:
        raise ValueError('configuration snapshot must contain exactly the five managed files')
    result = {}
    for name in sorted(MEMBERS):
        path = directory / name
        reject_links(path)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise ValueError('snapshot member is not a bounded normal file')
            raw = handle.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise ValueError('snapshot member exceeds size limit')
        result[name] = raw
    return result


def bundle_hash(content):
    digest = hashlib.sha256()
    for name in sorted(content):
        raw = content[name]
        digest.update(name.encode('ascii') + b'\0' + str(len(raw)).encode('ascii') + b'\0' + raw + b'\0')
    return digest.hexdigest()


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def create_snapshot(folder, app, env, release, values, overrides, install_params):
    validate_name(app)
    validate_name(env, 'environment', 32)
    if release['application'] != app or values.get('APP_VERSION') != release['version']:
        raise ValueError('snapshot application/version mismatch')
    business = dict(values)
    business.pop('APP_VERSION', None)
    validate_values(business)
    overrides = validate_values(overrides, 'persisted overrides')
    install_params = validate_values(install_params, 'installation parameters', reserve_platform=False)
    folder = Path(folder).absolute()
    reject_links(folder)
    runtime = folder / 'runtime'
    reject_links(runtime)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    runtime.chmod(0o700)
    identifier = uuid.uuid4().hex
    directory = runtime / identifier
    directory.mkdir(mode=0o700)
    content = {'.env.json': render_json(values), 'effective.env': render_raw_env(values),
               'overrides.json': render_json(overrides), '.install-params.json': render_json(install_params),
               'compose.yaml': yaml.safe_dump(render_runtime_compose(release, identifier), sort_keys=False)}
    try:
        for name, value in content.items():
            raw = value.encode('utf-8')
            if len(raw) > MAX_FILE:
                raise ValueError('generated snapshot member exceeds size limit')
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o644 if name == '.env.json' else 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            (directory / name).chmod(0o644 if name == '.env.json' else 0o600)
        sync_directory(directory)
        sync_directory(runtime)
        return ConfigurationSnapshot(identifier, directory, dict(values), overrides, install_params,
                                     bundle_hash(bundle(directory)))
    except BaseException:
        reject_links(directory)
        if directory.parent != runtime or not directory.resolve().is_relative_to(runtime.resolve()):
            raise ValueError('cannot clean snapshot outside its runtime directory')
        shutil.rmtree(directory)
        raise


def verify_snapshot(snapshot):
    validate_id(snapshot.id)
    if snapshot.directory.name != snapshot.id or bundle_hash(bundle(snapshot.directory)) != snapshot.sha256:
        raise ValueError('configuration snapshot integrity check failed')


def load_snapshot(folder, ref, release):
    identifier = ref.get('configuration')
    validate_id(identifier)
    digest = ref.get('configuration_sha256')
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError('invalid configuration snapshot digest')
    directory = Path(folder).absolute() / 'runtime' / identifier
    content = bundle(directory)
    if bundle_hash(content) != digest:
        raise ValueError('configuration snapshot integrity check failed')
    values = json.loads(content['.env.json'])
    overrides = validate_values(json.loads(content['overrides.json']), 'persisted overrides')
    params = validate_values(json.loads(content['.install-params.json']), 'installation parameters', False)
    if not isinstance(values, dict) or values.get('APP_VERSION') != release['version'] or ref['version'] != release['version']:
        raise ValueError('configuration snapshot version mismatch')
    business = dict(values)
    business.pop('APP_VERSION')
    validate_values(business)
    if (content['effective.env'] != render_raw_env(values).encode('utf-8')
            or load_yaml(content['compose.yaml'].decode('utf-8')) != render_runtime_compose(release, identifier)):
        raise ValueError('runtime Compose/environment does not match the platform template')
    return ConfigurationSnapshot(identifier, directory, values, overrides, params, digest)
