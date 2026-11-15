"""Pure service planning. No Docker, filesystem or network side effects."""
import hashlib
import json

from .multiservice_contract import dependency_order


def service_values(spec, values):
    environment, files = {}, {}
    for name, binding in spec.get('environment', {}).items():
        kind, reference = next(iter(binding.items()))
        if kind == 'literal':
            value = reference
        else:
            pool = values.get('secrets' if kind == 'secret' else 'config', {})
            if reference not in pool:
                raise ValueError(f'missing {kind} configuration: {reference}')
            value = pool[reference]
        if not isinstance(value, str) or '\x00' in value:
            raise ValueError(f'invalid configuration value for {name}')
        environment[name] = value
    for reference in spec.get('secret_files', {}):
        if reference not in values.get('secrets', {}):
            raise ValueError(f'missing secret configuration: {reference}')
        value = values['secrets'][reference]
        if not isinstance(value, str) or '\x00' in value:
            raise ValueError(f'invalid secret configuration: {reference}')
        files[reference] = value
    return environment, files


def service_fingerprint(spec, values):
    environment, files = service_values(spec, values)
    # Stored only in protected node state, never include raw secret values in a plan.
    payload = {'spec': spec, 'environment': environment, 'secret_files': files}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def plan_services(release, observed, configurations):
    if release.get('schema_version') != 3:
        if observed.get('multi_service'):
            raise ValueError('legacy release cannot replace a managed multi-service installation')
        raise ValueError('multi-service planner requires release schema 3')
    config = release['deployment']
    services, volumes, fingerprints = {}, {}, {}
    prior = observed.get('services', {})
    for name, spec in sorted(config['services'].items()):
        fingerprint = service_fingerprint(spec, configurations)
        fingerprints[name] = fingerprint
        old = prior.get(name)
        if old is None:
            action = 'create'
        elif old.get('lifecycle') != spec['lifecycle']:
            action = 'maintenance-required'
        elif old.get('fingerprint') != fingerprint:
            action = 'maintenance-required' if spec['lifecycle'] == 'persistent' else 'update'
        elif not old.get('running', False):
            action = 'start'
        elif not old.get('healthy', False):
            action = 'unhealthy'
        else:
            action = 'unchanged'
        services[name] = action
    for name in sorted(set(prior) - set(services)):
        services[name] = 'removal-required'
    prior_volumes = observed.get('volumes', {})
    for name in sorted(config['volumes']):
        old = prior_volumes.get(name)
        if old is None:
            volumes[name] = 'create'
        elif not old.get('present'):
            volumes[name] = 'missing'
        elif not old.get('identity') or old.get('identity_changed'):
            volumes[name] = 'unexpected'
        else:
            volumes[name] = 'reuse'
    for name in sorted(set(prior_volumes) - set(volumes)):
        volumes[name] = 'retained-unreferenced'
    applicable = (all(action in {'create', 'update', 'start', 'unchanged'} for action in services.values()) and
                  all(action in {'create', 'reuse', 'retained-unreferenced'} for action in volumes.values()))
    return {'services': services, 'volumes': volumes, 'fingerprints': fingerprints,
            'order': dependency_order(config['services'], config.get('jobs', {})), 'applicable': applicable}
