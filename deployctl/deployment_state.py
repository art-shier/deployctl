"""Validated version/configuration references; legacy reads never write state."""

import copy
from dataclasses import asdict, dataclass
import ipaddress

from .contract import integer, version
from .runtime_config import parse_assignments, validate_values
from .runtime_snapshot import DIGEST, validate_id

PHASES = {'prepared', 'pre_install', 'start', 'health-check', 'post_install', 'final-health-check', 'rollback'}


@dataclass
class DeploymentRef:
    version: str
    configuration: str | None
    configuration_sha256: str | None
    binding: dict
    legacy: bool = False

    def as_dict(self):
        return asdict(self)


def validate_binding(value):
    if not isinstance(value, dict) or set(value) != {'port', 'address'}:
        raise ValueError('invalid deployment binding')
    integer(value['port'], 1, 65535, 'host port')
    try:
        ipaddress.IPv4Address(value['address'])
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid deployment bind address') from exc
    return dict(value)


def validate_ref(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'version', 'configuration', 'configuration_sha256', 'binding', 'legacy'}:
        raise ValueError('invalid deployment reference shape')
    version(value['version'])
    validate_binding(value['binding'])
    if not isinstance(value['legacy'], bool):
        raise ValueError('invalid legacy deployment marker')
    if value['legacy']:
        if value['configuration'] is not None or value['configuration_sha256'] is not None:
            raise ValueError('legacy reference cannot include a snapshot')
    else:
        validate_id(value['configuration'])
        if not isinstance(value['configuration_sha256'], str) or not DIGEST.fullmatch(value['configuration_sha256']):
            raise ValueError('invalid configuration snapshot digest')
    return copy.deepcopy(value)


def normalize_state(data, app, env):
    if data is None:
        return {'schema_version': 2, 'application': app, 'environment': env,
                'current': None, 'previous': None, 'transaction': None, 'binding': None}
    if not isinstance(data, dict) or data.get('application') != app or data.get('environment') != env:
        raise ValueError('state belongs to a different application/environment')
    required = {'schema_version', 'application', 'environment', 'current', 'previous', 'transaction', 'binding'}
    if not required.issubset(data) or set(data) - required - {'updated_at'}:
        raise ValueError('invalid deployment state shape')
    state = copy.deepcopy(data)
    if type(data['schema_version']) is not int or data['schema_version'] not in (1, 2):
        raise ValueError('unsupported deployment state schema')
    if data['binding'] is not None:
        validate_binding(data['binding'])
    if data['schema_version'] == 1:
        def legacy(value):
            if value is None: return None
            version(value)
            if data['binding'] is None:
                raise ValueError('legacy deployment binding is missing')
            return DeploymentRef(value, None, None, data['binding'], True).as_dict()
        for field in ('current', 'previous'):
            state[field] = legacy(data[field])
        if data['transaction'] is not None:
            transaction = data['transaction']
            if not isinstance(transaction, dict) or set(transaction) != {'from', 'to'} or not transaction['to']:
                raise ValueError('invalid legacy transaction')
            state['transaction'] = {'from': legacy(transaction['from']), 'to': legacy(transaction['to']), 'phase': 'start'}
        state['schema_version'] = 2
    for field in ('current', 'previous'):
        state[field] = validate_ref(state[field])
    if state['current'] and state['binding'] != state['current']['binding']:
        raise ValueError('current deployment binding does not match state')
    if state['transaction'] is not None:
        transaction = state['transaction']
        if (not isinstance(transaction, dict) or set(transaction) != {'from', 'to', 'phase'}
                or transaction['phase'] not in PHASES or transaction['to'] is None):
            raise ValueError('invalid deployment transaction')
        transaction['from'] = validate_ref(transaction['from'])
        transaction['to'] = validate_ref(transaction['to'])
    return state


def promote_state(state, candidate, preserve_previous=False):
    result = copy.deepcopy(state)
    ref = validate_ref(candidate.as_dict() if isinstance(candidate, DeploymentRef) else candidate)
    if not preserve_previous:
        result['previous'] = result['current']
    result.update({'schema_version': 2, 'current': ref, 'binding': ref['binding'], 'transaction': None})
    return result


def collect_legacy_values(container_env, image_env, configured_names, release_version):
    actual = parse_assignments(container_env, 'actual container environment', False)
    defaults = parse_assignments(image_env, 'image environment', False)
    values = {key: value for key, value in actual.items()
              if key != 'APP_VERSION' and not key.startswith('DEPLOYCTL_')
              and (key in configured_names or defaults.get(key) != value)}
    validate_values(values)
    values['APP_VERSION'] = version(release_version)
    return values
