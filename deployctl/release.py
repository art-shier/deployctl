"""Standard archive construction and strict extraction."""

import hashlib
import gzip
import io
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

import yaml

from .contract import HOOK_PATHS, load_yaml, minimum_hook_version, validate_deployment, validate_release

FILES = {'release.yaml', 'compose.yaml', '.env.example', 'README.md'}
MAX_PACKAGE = 10 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
MAX_EXPANDED = 5 * 1024 * 1024
MAX_SCRIPT = 256 * 1024


def read_hook_script(root, name):
    root = Path(root).resolve()
    source = root
    for part in PurePosixPath(name).parts:
        source = source / part
        if source.is_symlink() or getattr(source, 'is_junction', lambda: False)():
            raise ValueError('hook script and its parent directories must not be links')
    if not source.resolve().is_relative_to(root) or not source.is_file():
        raise ValueError('hook script must be a normal file inside the project')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        with os.fdopen(os.open(source, flags), 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SCRIPT:
                raise ValueError('hook script must be a normal file of at most 256 KiB')
            raw = handle.read(MAX_SCRIPT + 1)
    except OSError as exc:
        raise ValueError('cannot read hook script') from exc
    if len(raw) > MAX_SCRIPT:
        raise ValueError('hook script exceeds 256 KiB')
    return raw


def render_compose(release):
    config = release['deployment']
    return {'services': {'app': {
        'image': release['image'], 'restart': 'unless-stopped',
        'ports': [f"${{DEPLOY_BIND_ADDRESS}}:${{DEPLOY_PORT}}:{config['container']['port']}"],
        'env_file': [
            {'path': '${DEPLOY_CONFIG_FILE}', 'format': 'raw'},
            {'path': '${DEPLOY_SECRETS_FILE}', 'format': 'raw'},
        ],
        'environment': {'APP_VERSION': release['version']},
        'labels': {'io.team-deploy.application': release['application'],
                   'io.team-deploy.version': release['version']},
        'mem_limit': config['resources']['memory_limit'],
        'cpus': config['resources']['cpus'],
        'pids_limit': 256, 'stop_grace_period': '30s',
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
    }}}


def build_release(config, image, version, output, commit='', project_root=None):
    config = validate_deployment(config)
    # Build inputs are consumed before packaging; keep the server's v1 contract.
    config['build'].pop('args', None)
    scripts, hooks = {}, {}
    for phase, descriptor in config.pop('hooks', {}).items():
        raw = read_hook_script(project_root or Path.cwd(), descriptor['script'])
        name = HOOK_PATHS[phase]
        scripts[name] = raw
        hooks[phase] = {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'timeout_seconds': descriptor['timeout_seconds']}
        if 'refresh_config' in descriptor:
            hooks[phase]['refresh_config'] = descriptor['refresh_config']
    manifest = {'schema_version': 2 if hooks else 1, 'application': config['application'],
                                'version': version, 'image': image, 'commit': commit,
                'minimum_deployctl_version': minimum_hook_version(hooks) if hooks else '1.0.0', 'deployment': config}
    if hooks:
        manifest['hooks'] = hooks
    release = validate_release(manifest)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"{release['application']}-{release['version']}.tar.gz"
    if package.exists():
        raise ValueError(f'Refusing to overwrite existing release: {package.name}')
    content = {
        'release.yaml': yaml.safe_dump(release, sort_keys=False),
        'compose.yaml': yaml.safe_dump(render_compose(release), sort_keys=False),
        '.env.example': '# Raw KEY=value format; do not add shell quotes.\n' +
                        ''.join(f'{key}=\n' for key in config['required_config']),
        'README.md': f"# {release['application']} {release['version']}\n\n"
                     'Install using deployctl and the external SHA256 checksum.\n'
                     'Keep production config outside this archive. No database migration is performed.\n',
    }
    content = {name: text.encode('utf-8') for name, text in content.items()}
    content.update(scripts)
    with tarfile.open(package, 'w:gz') as archive:
        for name, raw in content.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mode = 0o644
            item.mtime = 0
            archive.addfile(item, io.BytesIO(raw))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_name(package.name + '.sha256').write_text(f'{digest}  {package.name}\n', encoding='utf-8')
    return package


def unpack_release(path, destination):
    path, destination = Path(path), Path(destination)
    if path.stat().st_size > MAX_PACKAGE:
        raise ValueError('release package is too large')
    content = {}
    try:
        # tarfile consumes PAX/GNU metadata before yielding entries. Bound the
        # complete decompressed stream first, including all invisible metadata.
        with gzip.open(path, 'rb') as compressed:
            expanded = compressed.read(MAX_EXPANDED + 1)
        if len(expanded) > MAX_EXPANDED:
            raise ValueError('expanded release archive exceeds size limit')
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
            for item in archive:
                if (item.name not in FILES | set(HOOK_PATHS.values()) or item.name in content or not item.isfile()
                        or not 0 <= item.size <= MAX_MEMBER):
                    raise ValueError('unsafe, duplicate or oversized release archive entry')
                content[item.name] = archive.extractfile(item).read(MAX_MEMBER + 1)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError('invalid release archive') from exc
    if not FILES.issubset(content):
        raise ValueError('release archive must contain the four standard files')
    release = validate_release(load_yaml(content['release.yaml'].decode('utf-8')))
    expected = FILES | {hook['path'] for hook in release.get('hooks', {}).values()}
    if set(content) != expected:
        raise ValueError('release archive must contain exactly the declared standard files and hooks')
    for hook in release.get('hooks', {}).values():
        raw = content[hook['path']]
        if len(raw) > MAX_SCRIPT or hashlib.sha256(raw).hexdigest() != hook['sha256']:
            raise ValueError('hook script size or SHA256 does not match its manifest')
    compose = load_yaml(content['compose.yaml'].decode('utf-8'))
    if compose != render_compose(release):
        raise ValueError('compose.yaml does not match the standard release template')
    destination.mkdir(parents=True, exist_ok=False)
    for name, raw in content.items():
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        (destination / name).write_bytes(raw)
    return release
