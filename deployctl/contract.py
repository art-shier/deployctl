"""Strict, versioned deployment contract. No arbitrary Compose extensions."""

import ipaddress
from pathlib import Path, PurePosixPath
import re

import yaml

NAME = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')
VERSION = re.compile(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?\Z')
DIGEST_IMAGE = re.compile(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}\Z')
ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]*\Z')
BUILD_ARG_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}\Z')
HOOK_PATHS = {'pre_install': 'hooks/pre-install.sh', 'post_install': 'hooks/post-install.sh'}


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError('YAML mapping keys must be unique strings')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_yaml(text):
    if len(text.encode('utf-8')) > 65536:
        raise ValueError('YAML exceeds 64 KiB')
    try:
        return yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError('Invalid YAML') from exc


def read_yaml(path):
    return load_yaml(Path(path).read_text(encoding='utf-8'))


def mapping(value, allowed, required, field):
    if not isinstance(value, dict):
        raise ValueError(f'{field} must be a mapping')
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f'{field}: unknown fields: {", ".join(sorted(unknown))}')
    if missing:
        raise ValueError(f'{field}: missing fields: {", ".join(sorted(missing))}')
    return value


def validate_name(value, field='application', maximum=48):
    if not isinstance(value, str) or len(value) > maximum or not NAME.fullmatch(value):
        raise ValueError(f'{field} must be a lowercase name with hyphens (max {maximum})')
    return value


def integer(value, minimum, maximum, field):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{field} must be an integer in {minimum}..{maximum}')
    return value


def version(value):
    if not isinstance(value, str) or len(value) > 96 or not VERSION.fullmatch(value):
        raise ValueError('version must be semantic version, e.g. v1.0.0')
    return value


def relative_path(value, field):
    if not isinstance(value, str) or not value or '\\' in value or '\n' in value:
        raise ValueError(f'{field} must be a repository-relative POSIX path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value:
        raise ValueError(f'{field} must stay inside the repository')
    return value


def validate_build_args(value, field='build.args'):
    if not isinstance(value, dict) or len(value) > 128:
        raise ValueError(f'{field} must be a mapping of at most 128 arguments')
    for name, item in value.items():
        if not isinstance(name, str) or not BUILD_ARG_NAME.fullmatch(name):
            raise ValueError(f'{field}: argument names must be identifiers of at most 128 characters')
        # Docker action uses ECMAScript trim(), which also trims edge BOMs.
        if (not isinstance(item, str) or len(item) > 4096 or item != item.strip()
                or item != item.strip('\ufeff')
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in item)):
            raise ValueError(f'{field}.{name} must be a string of at most 4096 characters without controls or surrounding whitespace')
    return dict(value)


def validate_deployment(data):
    mapping(data, {'schema_version', 'application', 'build', 'container', 'health',
                   'resources', 'required_config', 'hooks'},
            {'schema_version', 'application', 'container', 'health'}, 'deployment')
    integer(data['schema_version'], 1, 1, 'schema_version')
    app = validate_name(data['application'])
    build = mapping(data.get('build', {}), {'dockerfile', 'context', 'args'}, set(), 'build')
    normalized_build = {
        'dockerfile': relative_path(build.get('dockerfile', 'Dockerfile'), 'build.dockerfile'),
        'context': relative_path(build.get('context', '.'), 'build.context'),
    }
    if 'args' in build:
        normalized_build['args'] = validate_build_args(build['args'])
    container = mapping(data['container'], {'port', 'host_port', 'bind_address'}, {'port'}, 'container')
    port = integer(container['port'], 1, 65535, 'container.port')
    host_port = integer(container.get('host_port', port), 1, 65535, 'container.host_port')
    bind = container.get('bind_address', '127.0.0.1')
    try:
        ipaddress.IPv4Address(bind)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise ValueError('container.bind_address must be an IPv4 address') from exc
    health = mapping(data['health'], {'readiness_path', 'startup_timeout_seconds'},
                     {'readiness_path'}, 'health')
    path = health['readiness_path']
    if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
            or any(c in path for c in ('\r', '\n', '#', '?', '\\')) or len(path) > 256):
        raise ValueError('health.readiness_path must be an absolute HTTP path')
    timeout = integer(health.get('startup_timeout_seconds', 120), 1, 600,
                      'health.startup_timeout_seconds')
    resources = mapping(data.get('resources', {}), {'memory_limit', 'cpus'}, set(), 'resources')
    memory = resources.get('memory_limit', '512m')
    if not isinstance(memory, str) or not re.fullmatch(r'[1-9][0-9]*[kKmMgG]', memory):
        raise ValueError('resources.memory_limit must be e.g. 512m or 2g')
    cpus = resources.get('cpus', 1.0)
    if type(cpus) not in (int, float) or not 0.1 <= cpus <= 128:
        raise ValueError('resources.cpus must be 0.1..128')
    config = data.get('required_config', [])
    if (not isinstance(config, list) or len(config) > 128
            or any(not isinstance(k, str) or not ENV_NAME.fullmatch(k) for k in config)
            or len(set(config)) != len(config) or 'APP_VERSION' in config):
        raise ValueError('required_config must contain unique uppercase names; APP_VERSION is reserved')
    normalized = {
        'schema_version': 1, 'application': app,
        'build': normalized_build,
        'container': {'port': port, 'host_port': host_port, 'bind_address': bind},
        'health': {'readiness_path': path, 'startup_timeout_seconds': timeout},
        'resources': {'memory_limit': memory, 'cpus': float(cpus)},
        'required_config': config,
    }
    if 'hooks' in data:
        hooks = validate_project_hooks(data['hooks'])
        if hooks:
            normalized['hooks'] = hooks
    return normalized


def validate_project_hooks(value):
    mapping(value, HOOK_PATHS, set(), 'hooks')
    result = {}
    for phase, hook in value.items():
        field = f'hooks.{phase}'
        mapping(hook, {'script', 'timeout_seconds', 'refresh_config'}, {'script'}, field)
        script = relative_path(hook['script'], field + '.script')
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in script):
            raise ValueError(f'{field}.script must not contain controls')
        result[phase] = {'script': script, 'timeout_seconds': integer(
            hook.get('timeout_seconds', 300), 1, 3600, field + '.timeout_seconds')}
        if 'refresh_config' in hook:
            if phase != 'pre_install' or type(hook['refresh_config']) is not bool:
                raise ValueError(f'{field}.refresh_config is a boolean available only on pre_install')
            result[phase]['refresh_config'] = hook['refresh_config']
    return result


def validate_hook_manifest(value):
    mapping(value, HOOK_PATHS, set(), 'release.hooks')
    if not value:
        raise ValueError('release schema 2 requires at least one hook')
    result = {}
    for phase, hook in value.items():
        field = f'release.hooks.{phase}'
        mapping(hook, {'path', 'sha256', 'timeout_seconds', 'refresh_config'}, {'path', 'sha256', 'timeout_seconds'}, field)
        if hook['path'] != HOOK_PATHS[phase]:
            raise ValueError(f'{field}.path must be {HOOK_PATHS[phase]}')
        if not isinstance(hook['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', hook['sha256']):
            raise ValueError(f'{field}.sha256 must be lowercase SHA256')
        result[phase] = dict(hook, timeout_seconds=integer(hook['timeout_seconds'], 1, 3600,
                                                         field + '.timeout_seconds'))
        if 'refresh_config' in hook and (phase != 'pre_install' or type(hook['refresh_config']) is not bool):
            raise ValueError(f'{field}.refresh_config is a boolean available only on pre_install')
    return result


def minimum_hook_version(hooks):
    return '1.6.0' if any('refresh_config' in hook for hook in hooks.values()) else '1.5.0'


def validate_release(data):
    mapping(data, {'schema_version', 'application', 'version', 'image', 'commit',
                   'minimum_deployctl_version', 'deployment', 'hooks'},
            {'schema_version', 'application', 'version', 'image',
             'minimum_deployctl_version', 'deployment'}, 'release')
    protocol = integer(data['schema_version'], 1, 2, 'release.schema_version')
    validate_name(data['application'])
    version(data['version'])
    if not isinstance(data['image'], str) or not DIGEST_IMAGE.fullmatch(data['image']):
        raise ValueError('image must be pinned to a lowercase sha256 digest')
    hooks = validate_hook_manifest(data.get('hooks')) if protocol == 2 else None
    minimum = '1.0.0' if protocol == 1 else minimum_hook_version(hooks)
    if data['minimum_deployctl_version'] != minimum:
        raise ValueError('unsupported minimum_deployctl_version; upgrade platform together')
    if protocol == 1 and 'hooks' in data:
        raise ValueError('release schema 1 cannot declare hooks')
    config = validate_deployment(data['deployment'])
    if 'hooks' in data['deployment']:
        raise ValueError('source hooks belong in the release hook manifest, not deployment')
    if config['application'] != data['application']:
        raise ValueError('release.application does not match deployment.application')
    commit = data.get('commit', '')
    if not isinstance(commit, str) or (commit and not re.fullmatch(r'[a-f0-9]{40,64}', commit)):
        raise ValueError('commit must be an empty string or Git commit hash')
    result = dict(data, deployment=config, commit=commit)
    if hooks is not None:
        result['hooks'] = hooks
    return result
