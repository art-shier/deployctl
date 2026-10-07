"""Literal runtime configuration; no shell expansion or implicit types."""

import json
from pathlib import Path

from .contract import ENV_NAME, version as validate_version

MAX_ENTRIES = 128
MAX_NAME = 128
MAX_VALUE = 4096
MAX_INPUT = 64 * 1024


def validate_key(name, field, reserve_platform=True):
    if not isinstance(name, str) or len(name) > MAX_NAME or not ENV_NAME.fullmatch(name):
        raise ValueError(f'{field}: names must be uppercase identifiers of at most 128 characters')
    if reserve_platform and (name == 'APP_VERSION' or name.startswith('DEPLOYCTL_')):
        raise ValueError(f'{field}: {name} is reserved by the platform')


def validate_values(values, field='runtime configuration', reserve_platform=True):
    if not isinstance(values, dict) or len(values) > MAX_ENTRIES:
        raise ValueError(f'{field}: use at most 128 string values')
    size = 0
    for name, value in values.items():
        validate_key(name, field, reserve_platform)
        if (not isinstance(value, str) or len(value) > MAX_VALUE
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in value)):
            raise ValueError(f'{field}.{name}: use a single-line string of at most 4096 characters without controls')
        try:
            size += len(f'{name}={value}\n'.encode('utf-8'))
        except UnicodeEncodeError as exc:
            raise ValueError(f'{field}.{name}: use valid UTF-8') from exc
    if size > MAX_INPUT:
        raise ValueError(f'{field}: total input exceeds 64 KiB UTF-8')
    return dict(values)


def parse_assignments(items, field, reserve_platform=True):
    if not isinstance(items, list) or len(items) > MAX_ENTRIES:
        raise ValueError(f'{field}: use at most 128 KEY=value entries')
    values = {}
    for item in items:
        if not isinstance(item, str) or '=' not in item:
            raise ValueError(f'{field}: use explicit KEY=value entries')
        name, value = item.split('=', 1)
        validate_key(name, field, reserve_platform)
        if name in values:
            raise ValueError(f'{field}: duplicate variable {name}')
        values[name] = value
    return validate_values(values, field, reserve_platform)


def read_raw_env(path):
    path = Path(path)
    values = {}
    # LF/CRLF delimit records; Unicode line separators are invalid data.
    for line in path.read_text(encoding='utf-8').split('\n'):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'{path.name}: use raw KEY=value format')
        name, value = line.split('=', 1)
        validate_key(name, path.name, reserve_platform=False)
        if name in values:
            raise ValueError(f'{path.name}: duplicate variable {name}')
        values[name] = value
    return validate_values(values, path.name, reserve_platform=False)


def validate_unset(names):
    if not isinstance(names, list) or len(names) > MAX_ENTRIES:
        raise ValueError('unset-env: use at most 128 names')
    seen = set()
    for name in names:
        validate_key(name, 'unset-env')
        if name in seen:
            raise ValueError(f'unset-env: duplicate variable {name}')
        seen.add(name)
    return seen


def merge_runtime_values(config, secrets, previous_overrides, updates, unset, version):
    validate_version(version)
    base = validate_values(config, 'config.env')
    base.update(validate_values(secrets, 'secrets.env'))
    overrides = validate_values(previous_overrides, 'persisted overrides')
    updates = validate_values(updates, 'env-var')
    removed = validate_unset(unset)
    if removed.intersection(updates):
        raise ValueError('a variable cannot be both env-var and unset-env')
    for name in removed:
        overrides.pop(name, None)
    overrides.update(updates)
    overrides = validate_values(overrides, 'persisted overrides')
    base.update(overrides)
    values = validate_values(base)
    values['APP_VERSION'] = version
    return values, overrides


def render_json(values):
    return json.dumps(values, sort_keys=True, indent=2, ensure_ascii=False) + '\n'


def render_raw_env(values):
    return ''.join(f'{name}={values[name]}\n' for name in sorted(values))
