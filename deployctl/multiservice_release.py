"""Immutable multi-image packages; no host configuration or credentials inside."""
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import yaml

from .contract import DIGEST_IMAGE, mapping, validate_name, version
from .multiservice_contract import validate_multiservice_deployment

# Planned capability floor, not an assertion that this version is published.
MINIMUM_VERSION = '1.20.0'


def resolve_services(config, images):
    config = validate_multiservice_deployment(config)
    nodes = {**config['services'], **config['jobs']}
    if not isinstance(images, dict) or set(images) != set(nodes):
        raise ValueError('images must resolve every service/job exactly once')
    for name, node in nodes.items():
        image = images[name]
        if not isinstance(image, str) or not DIGEST_IMAGE.fullmatch(image):
            raise ValueError('resolved image must be pinned to sha256 digest')
        if 'image' in node:
            if node['image'].split('@')[1] != image.split('@')[1]:
                raise ValueError('mirrored external image digest changed')
            if node.get('mirror') != 'project' and node['image'] != image:
                raise ValueError('external image repository changed without mirror policy')
        node.pop('build', None); node.pop('mirror', None); node['image'] = image
    return config


def validate_multiservice_release(data):
    fields = {'schema_version', 'application', 'version', 'commit', 'minimum_deployctl_version', 'capabilities', 'deployment'}
    mapping(data, fields, fields - {'commit'}, 'release')
    if type(data['schema_version']) is not int or data['schema_version'] != 3:
        raise ValueError('multi-service release requires schema 3')
    validate_name(data['application']); version(data['version'])
    if data['minimum_deployctl_version'] != MINIMUM_VERSION or data['capabilities'] != ['multi_service_v1']:
        raise ValueError('unsupported multi-service capability/version floor')
    config = validate_multiservice_deployment(data['deployment'])
    if config['application'] != data['application']:
        raise ValueError('release/deployment application mismatch')
    for node in [*config['services'].values(), *config['jobs'].values()]:
        if 'build' in node or 'mirror' in node:
            raise ValueError('release must contain only resolved images')
    commit = data.get('commit', '')
    if not isinstance(commit, str) or (commit and not re.fullmatch(r'[a-f0-9]{40,64}', commit)):
        raise ValueError('commit must be a Git hash or empty')
    return dict(data, deployment=config, commit=commit)


def _variable(kind, name):
    return 'DEPLOYCTL_' + kind + '_' + name.replace('-', '_').upper()


def render_multiservice_compose(release):
    release = validate_multiservice_release(release)
    config = release['deployment']
    compose = {'services': {}, 'volumes': {}, 'networks': {'app': {'external': True, 'name': '${DEPLOYCTL_NETWORK}'}}}
    for name in config['volumes']:
        compose['volumes'][name] = {'external': True, 'name': '${' + _variable('VOLUME', name) + '}'}
    for name, node in {**config['services'], **config['jobs']}.items():
        job = name in config['jobs']
        service = {
            'image': node['image'], 'platform': config['platform'],
            'restart': 'no' if job else 'unless-stopped',
            'networks': {'app': {'aliases': [name]}},
            'labels': {'io.team-deploy.application': release['application'], 'io.team-deploy.service': name,
                       'io.team-deploy.lifecycle': 'job' if job else node['lifecycle']},
            'env_file': [{'path': '${' + _variable('ENV', name) + '}', 'format': 'raw'}],
            'mem_limit': node['resources']['memory_limit'], 'cpus': node['resources']['cpus'],
            'pids_limit': 256, 'stop_grace_period': '30s',
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
        if node['permission_profile'] == 'database-init':
            service['cap_add'] = ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETGID', 'SETUID']
        if 'command' in node: service['command'] = [arg.replace('$', '$$') for arg in node['command']]
        mounts = [{'type': 'volume', 'source': key, 'target': target} for key, target in sorted(node['volumes'].items())]
        for key, target in sorted(node['secret_files'].items()):
            mounts.append({'type': 'bind', 'source': '${' + _variable('SECRET', name) + '}/' + key,
                           'target': target, 'read_only': True, 'bind': {'create_host_path': False}})
        if mounts: service['volumes'] = mounts
        if job:
            service['profiles'] = ['jobs']
        else:
            if node['endpoints']:
                service['ports'] = [f"{e['bind_address']}:{e['host_port']}:{e['container_port']}" for _, e in sorted(node['endpoints'].items())]
            if 'exec' in node['health']:
                service['healthcheck'] = {'test': ['CMD', *[arg.replace('$', '$$') for arg in node['health']['exec']]], 'interval': '5s', 'timeout': '5s', 'retries': 12}
        compose['services'][name] = service
    return compose


def build_multiservice_release(config, images, release_version, output, commit=''):
    config = resolve_services(config, images)
    release = validate_multiservice_release({'schema_version': 3, 'application': config['application'],
        'version': release_version, 'commit': commit, 'minimum_deployctl_version': MINIMUM_VERSION,
        'capabilities': ['multi_service_v1'], 'deployment': config})
    content = {'release.yaml': yaml.safe_dump(release, sort_keys=True),
               'compose.yaml': yaml.safe_dump(render_multiservice_compose(release), sort_keys=True),
               '.env.example': '# Configure explicit service bindings using ctl; secrets are not packaged.\n',
               'README.md': '# Managed multi-service release\nInstall using ctl with multi_service_v1 support.\n'}
    from .release import MAX_EXPANDED, MAX_MEMBER, MAX_PACKAGE
    raw = {name: value.encode('utf-8') for name, value in content.items()}
    if any(len(value) > MAX_MEMBER for value in raw.values()):
        raise ValueError('release manifest exceeds package member limit')
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0, filename='') as gz:
        with tarfile.open(fileobj=gz, mode='w:') as archive:
            for name, value in sorted(raw.items()):
                item = tarfile.TarInfo(name); item.size = len(value); item.mode = 0o644
                archive.addfile(item, io.BytesIO(value))
    payload = buffer.getvalue()
    if len(payload) > MAX_PACKAGE or len(gzip.decompress(payload)) > MAX_EXPANDED:
        raise ValueError('release exceeds archive limits')
    # Same manifest size ceiling used by the shared parser, before writing a file.
    if len(raw['release.yaml']) > 65536 or len(raw['compose.yaml']) > 65536:
        raise ValueError('release manifest exceeds 64 KiB')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    path = output / f"{release['application']}-{release['version']}.tar.gz"
    with path.open('xb') as stream: stream.write(payload)
    path.with_name(path.name + '.sha256').write_text(hashlib.sha256(payload).hexdigest() + '  ' + path.name + '\n')
    return path
