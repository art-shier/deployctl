"""Closed source contract for single-node, managed multi-service applications."""
import copy
import ipaddress
import math
import re
from pathlib import PurePosixPath

from .contract import (DIGEST_IMAGE, ENV_NAME, integer, mapping, relative_path,
                       validate_build_args, validate_name)


def text(value, field, maximum=4096):
    if (not isinstance(value, str) or len(value) > maximum or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise ValueError(f'{field} must be bounded text without controls')
    return value


def names(value, field, maximum=32):
    if not isinstance(value, dict) or len(value) > maximum:
        raise ValueError(f'{field} must contain at most {maximum} named entries')
    for name in value:
        validate_name(name, field, 48)
    return value


def config_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', value):
        raise ValueError('configuration reference must be an identifier')
    return value


def argv(value, field):
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ValueError(f'{field} must be a nonempty argument array')
    result = [text(v, field) for v in value]
    if not result[0]:
        raise ValueError(f'{field} executable must not be empty')
    return result


def mount_path(value):
    text(value, 'mount path', 256)
    path = PurePosixPath(value)
    if (not path.is_absolute() or str(path) != value or '..' in path.parts or
            value == '/' or '\\' in value or '$' in value or ':' in value or
            any(path == PurePosixPath(p) or PurePosixPath(p) in path.parents
                for p in ('/proc', '/sys', '/dev', '/run/deployctl'))):
        raise ValueError('unsafe container mount path')
    return value


def dependency_order(services, jobs):
    """Validate typed dependency edges and return deterministic topological order."""
    if set(services) & set(jobs):
        raise ValueError('service and job names must be distinct')
    nodes = {**services, **jobs}
    result, visiting, visited = [], set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError('dependency cycle')
        if name in visited:
            return
        visiting.add(name)
        deps = nodes[name].get('depends_on', {})
        names(deps, 'depends_on', 64)
        for dep, condition in sorted(deps.items()):
            if dep not in nodes:
                raise ValueError('unknown dependency')
            expected = 'healthy' if dep in services else 'succeeded'
            if condition != expected:
                raise ValueError('dependency condition must match service/job kind')
            visit(dep)
        visiting.remove(name); visited.add(name); result.append(name)

    for name in sorted(nodes):
        visit(name)
    return result


def _resources(value):
    mapping(value, {'memory_limit', 'cpus'}, set(), 'resources')
    memory, cpus = value.get('memory_limit', '512m'), value.get('cpus', 1.0)
    if not isinstance(memory, str) or not re.fullmatch(r'[1-9][0-9]{0,8}[kKmMgG]', memory):
        raise ValueError('invalid memory_limit')
    if type(cpus) not in (int, float) or not math.isfinite(cpus) or not 0.1 <= cpus <= 128:
        raise ValueError('cpus must be 0.1..128')
    return {'memory_limit': memory, 'cpus': float(cpus)}


def _health(value):
    mapping(value, {'http', 'exec', 'startup_timeout_seconds'}, set(), 'health')
    if ('http' in value) == ('exec' in value):
        raise ValueError('health requires exactly one of http/exec')
    result = {'startup_timeout_seconds': integer(value.get('startup_timeout_seconds', 120), 1, 600, 'startup timeout')}
    if 'http' in value:
        path = text(value['http'], 'health.http', 256)
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in '#?\\$'):
            raise ValueError('invalid HTTP health path')
        result['http'] = path
    else:
        result['exec'] = argv(value['exec'], 'health.exec')
    return result


def _node(value, volume_names, job=False):
    common = {'build', 'image', 'mirror', 'command', 'environment', 'secret_files', 'volumes',
              'resources', 'depends_on', 'permission_profile'}
    allowed = common | ({'trigger', 'timeout_seconds', 'rollback_compatible'} if job else {'lifecycle', 'endpoints', 'health'})
    required = {'trigger', 'command'} if job else {'lifecycle', 'health'}
    mapping(value, allowed, required, 'job' if job else 'service')
    if ('build' in value) == ('image' in value):
        raise ValueError('exactly one build/image source required')
    out = {}
    if 'image' in value:
        image = value['image']
        if not isinstance(image, str) or not DIGEST_IMAGE.fullmatch(image):
            raise ValueError('image must be pinned to sha256')
        out['image'] = image
    else:
        build = mapping(value['build'], {'dockerfile', 'context', 'args'}, set(), 'build')
        out['build'] = {'dockerfile': relative_path(build.get('dockerfile', 'Dockerfile'), 'dockerfile'),
                        'context': relative_path(build.get('context', '.'), 'context'),
                        'args': validate_build_args(build.get('args', {}))}
    if 'mirror' in value:
        if 'image' not in value or value['mirror'] != 'project':
            raise ValueError('mirror project is only available for external images')
        out['mirror'] = 'project'
    if 'command' in value:
        out['command'] = argv(value['command'], 'command')
    profile = value.get('permission_profile', 'restricted')
    if profile not in ('restricted', 'database-init'):
        raise ValueError('unknown permission profile')
    out['permission_profile'] = profile
    if not job:
        if value['lifecycle'] not in ('application', 'persistent'):
            raise ValueError('invalid lifecycle')
        out['lifecycle'] = value['lifecycle']
        if profile == 'database-init' and value['lifecycle'] != 'persistent':
            raise ValueError('database-init profile requires persistent lifecycle')
        out['health'] = _health(value['health'])
        endpoints = names(value.get('endpoints', {}), 'endpoints', 16)
        out['endpoints'] = {}
        for name, endpoint in endpoints.items():
            mapping(endpoint, {'container_port', 'host_port', 'bind_address'}, {'container_port', 'host_port'}, 'endpoint')
            bind = endpoint.get('bind_address', '127.0.0.1')
            if not isinstance(bind, str):
                raise ValueError('bind_address must be IPv4 text')
            try: ipaddress.IPv4Address(bind)
            except ipaddress.AddressValueError as exc: raise ValueError('bind_address must be IPv4') from exc
            out['endpoints'][name] = {'container_port': integer(endpoint['container_port'], 1, 65535, 'container_port'),
                                      'host_port': integer(endpoint['host_port'], 1, 65535, 'host_port'), 'bind_address': bind}
        if 'http' in out['health'] and len(out['endpoints']) != 1:
            raise ValueError('HTTP health requires exactly one endpoint; use exec for internal services')
    else:
        if value['trigger'] not in ('bootstrap', 'explicit'):
            raise ValueError('job trigger must be bootstrap or explicit')
        out['trigger'] = value['trigger']
        out['timeout_seconds'] = integer(value.get('timeout_seconds', 900), 1, 7200, 'job timeout')
        compatible = value.get('rollback_compatible', False)
        if type(compatible) is not bool:
            raise ValueError('rollback_compatible must be boolean')
        out['rollback_compatible'] = compatible
    environment = value.get('environment', {})
    if not isinstance(environment, dict) or len(environment) > 128:
        raise ValueError('environment must be a bounded mapping')
    out['environment'] = {}
    for key, binding in environment.items():
        if not isinstance(key, str) or not ENV_NAME.fullmatch(key) or key.startswith('DEPLOYCTL_'):
            raise ValueError('invalid/reserved environment key')
        mapping(binding, {'literal', 'config', 'secret'}, set(), 'environment binding')
        if len(binding) != 1:
            raise ValueError('environment binding requires exactly one source')
        kind, item = next(iter(binding.items()))
        out['environment'][key] = {kind: text(item, key) if kind == 'literal' else config_key(item)}
    out['secret_files'] = {}
    files = value.get('secret_files', {})
    if not isinstance(files, dict) or len(files) > 128:
        raise ValueError('secret_files must be bounded mapping')
    for key, target in files.items():
        out['secret_files'][config_key(key)] = mount_path(target)
    volumes = names(value.get('volumes', {}), 'volume mounts')
    if set(volumes) - set(volume_names):
        raise ValueError('unknown volume reference')
    out['volumes'] = {key: mount_path(target) for key, target in volumes.items()}
    paths = [PurePosixPath(p) for p in [*out['secret_files'].values(), *out['volumes'].values()]]
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError('overlapping mount targets')
    out['resources'] = _resources(value.get('resources', {}))
    out['depends_on'] = dict(names(value.get('depends_on', {}), 'depends_on', 64))
    return out


def validate_multiservice_deployment(data):
    mapping(data, {'schema_version', 'application', 'platform', 'primary_endpoint', 'services', 'volumes', 'jobs'},
            {'schema_version', 'application', 'platform', 'services'}, 'deployment')
    integer(data['schema_version'], 2, 2, 'schema_version')
    if data['platform'] not in ('linux/amd64', 'linux/arm64'):
        raise ValueError('unsupported single-node Linux platform')
    volumes = names(data.get('volumes', {}), 'volumes')
    for definition in volumes.values():
        mapping(definition, {'retain'}, {'retain'}, 'volume')
        if definition['retain'] is not True:
            raise ValueError('managed volumes must be retained')
    services = names(data['services'], 'services')
    if not services:
        raise ValueError('at least one service required')
    jobs = names(data.get('jobs', {}), 'jobs')
    out = {'schema_version': 2, 'application': validate_name(data['application']), 'platform': data['platform'],
           'services': {name: _node(value, volumes) for name, value in services.items()},
           'jobs': {name: _node(value, volumes, job=True) for name, value in jobs.items()},
           'volumes': copy.deepcopy(volumes)}
    dependency_order(out['services'], out['jobs'])
    endpoints = {f'{name}.{key}': value for name, service in out['services'].items()
                 for key, value in service['endpoints'].items()}
    if 'primary_endpoint' in data:
        if not isinstance(data['primary_endpoint'], str) or data['primary_endpoint'] not in endpoints:
            raise ValueError('primary_endpoint must reference a declared endpoint')
        out['primary_endpoint'] = data['primary_endpoint']
    used = []
    for endpoint in endpoints.values():
        bind, port = endpoint['bind_address'], endpoint['host_port']
        if any(port == p and (bind == b or '0.0.0.0' in (bind, b)) for b, p in used):
            raise ValueError('host endpoint collision')
        used.append((bind, port))
    return out
