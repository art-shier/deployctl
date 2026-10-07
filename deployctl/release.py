"""Standard archive construction and strict extraction."""

import hashlib
import gzip
import io
from pathlib import Path
import tarfile

import yaml

from .contract import load_yaml, validate_deployment, validate_release

FILES = {'release.yaml', 'compose.yaml', '.env.example', 'README.md'}
MAX_PACKAGE = 10 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
MAX_EXPANDED = 5 * 1024 * 1024


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


def build_release(config, image, version, output, commit=''):
    config = validate_deployment(config)
    # Build inputs are consumed before packaging; keep the server's v1 contract.
    config['build'].pop('args', None)
    release = validate_release({'schema_version': 1, 'application': config['application'],
                                'version': version, 'image': image, 'commit': commit,
                                'minimum_deployctl_version': '1.0.0', 'deployment': config})
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
    with tarfile.open(package, 'w:gz') as archive:
        for name, text in content.items():
            raw = text.encode('utf-8')
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
                if (item.name not in FILES or item.name in content or not item.isfile()
                        or not 0 <= item.size <= MAX_MEMBER):
                    raise ValueError('unsafe, duplicate or oversized release archive entry')
                content[item.name] = archive.extractfile(item).read(MAX_MEMBER + 1)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError('invalid release archive') from exc
    if set(content) != FILES:
        raise ValueError('release archive must contain exactly the four standard files')
    release = validate_release(load_yaml(content['release.yaml'].decode('utf-8')))
    compose = load_yaml(content['compose.yaml'].decode('utf-8'))
    if compose != render_compose(release):
        raise ValueError('compose.yaml does not match the standard release template')
    destination.mkdir(parents=True, exist_ok=False)
    for name, raw in content.items():
        (destination / name).write_bytes(raw)
    return release
