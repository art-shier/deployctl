"""Update an installer-managed CLI in its existing directory."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from . import bootstrap


def update_tool(version='latest', expected_sha256=None, executable=None, release_id=None):
    command = Path(executable or sys.argv[0]).expanduser().resolve()
    directory = command.parent
    marker = directory / '.deployctl-install.json'
    if not marker.is_file():
        raise ValueError('self-update requires an installer-managed CLI; rerun install.sh in the intended command directory')
    if marker.stat().st_size > 65536:
        raise ValueError('installer metadata exceeds size limit')
    metadata = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(metadata, dict) or metadata.get('schema_version') != 1
            or not isinstance(metadata.get('files'), dict)
            or not isinstance(metadata.get('repository'), str)):
        raise ValueError('invalid installer metadata; inspect installation before updating')
    files = metadata['files']
    if (command.name not in files or not command.is_file()
            or command.stat().st_size > bootstrap.MAX_DOWNLOAD
            or hashlib.sha256(command.read_bytes()).hexdigest() != files[command.name]):
        raise ValueError('invoking command is modified or not managed by this installer')
    primary = metadata.get('primary_name')
    commands = metadata.get('commands')
    if primary is None and commands is None:
        # v1.2.0 installation records contain only the file map.
        candidates = [name for name in files if name != 'ctl']
        if len(candidates) > 1:
            raise ValueError('ambiguous legacy installer metadata; rerun install.sh with the original destination')
        primary = candidates[0] if candidates else 'ctl'
        commands = list(files)
    if (not isinstance(primary, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', primary)
            or primary in ('.', '..') or not isinstance(commands, list)
            or not commands or any(not isinstance(name, str) for name in commands)
            or set(commands) != {primary, *(['ctl'] if 'ctl' in commands else [])}
            or command.name not in commands or any(name not in files for name in commands)):
        raise ValueError('invalid installer command metadata; rerun install.sh with the original destination')
    try:
        return bootstrap.install(
            install_dir=directory, repository=metadata['repository'], version=version,
            token=bootstrap.resolve_token(), no_alias='ctl' not in commands,
            expected_sha256=expected_sha256, primary_name=primary, release_id=release_id,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('CLI verification timed out; existing installation is unchanged') from exc
