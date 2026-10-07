"""Standalone stdlib-only SSH helper. Parameters are argv data, never shell source."""

import json
from pathlib import Path
import re
import subprocess
import sys

MAX_PAYLOAD = 512 * 1024
NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,127}')


def validate_map(values, runtime):
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError('payload maps must contain at most 128 string values')
    size = 0
    for key, value in values.items():
        if not isinstance(key, str) or not NAME.fullmatch(key):
            raise ValueError('invalid payload variable name')
        if runtime and (key == 'APP_VERSION' or key.startswith('DEPLOYCTL_')):
            raise ValueError('reserved runtime variable in payload')
        if (not isinstance(value, str) or len(value) > 4096
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in value)):
            raise ValueError('invalid single-line payload string')
        size += len(f'{key}={value}\n'.encode('utf-8'))
    if size > 64 * 1024:
        raise ValueError('payload parameter group exceeds 64 KiB UTF-8')


def build_command(payload, common_args):
    if not isinstance(payload, dict) or set(payload) != {'runtime_env', 'install_params'}:
        raise ValueError('invalid deployment payload shape')
    validate_map(payload['runtime_env'], True)
    validate_map(payload['install_params'], False)
    if not common_args or common_args[0] not in ('install', 'upgrade'):
        raise ValueError('deployment command must be install or upgrade')
    command = ['deployctl', *common_args]
    for key, value in payload['runtime_env'].items():
        command.extend(['--env-var', f'{key}={value}'])
    for key, value in payload['install_params'].items():
        command.extend(['--set', f'{key}={value}'])
    return command


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key in deployment payload')
        result[key] = value
    return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) < 2:
            raise ValueError('usage: deploy_remote.py parameters.json install|upgrade <application> ...')
        path = Path(args[0])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PAYLOAD:
            raise ValueError('payload must be a bounded normal file')
        with path.open('rb') as handle:
            raw = handle.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError('payload exceeds size limit')
        payload = json.loads(raw, object_pairs_hook=unique_object)
        command = build_command(payload, args[1:])
        if payload['runtime_env'] or payload['install_params']:
            version = subprocess.run(['deployctl', '--version'], capture_output=True, text=True, timeout=30)
            match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version.stdout.strip())
            if version.returncode or not match or tuple(map(int, match.groups())) < (1, 5, 0):
                raise RuntimeError('runtime/hook parameters require server ctl >=1.5.0; update the tool first')
        return subprocess.run(command).returncode
    except (ValueError, UnicodeError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Do not stringify subprocess exceptions: their argv can contain private parameters.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) and not isinstance(exc, UnicodeError) else 'cannot read payload or execute server ctl'
        print(f'ERROR: {message}', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
