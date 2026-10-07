"""Runner-side input validation, artifact verification and private SSH payload."""

import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deployctl.contract import validate_name
from deployctl.download import acquire_release
from deployctl.runtime_config import parse_assignments


def prepare_parameters(runtime_env, install_params):
    def lines(value):
        if not isinstance(value, str) or len(value.encode('utf-8')) > 512 * 1024:
            raise ValueError('parameter input exceeds size limit')
        return [line for line in value.replace('\r\n', '\n').split('\n') if line.strip()]
    return {'runtime_env': parse_assignments(lines(runtime_env), 'runtime-env'),
            'install_params': parse_assignments(lines(install_params), 'install-params', reserve_platform=False)}


def main():
    try:
        validate_name(os.environ['APPLICATION'])
        validate_name(os.environ['TARGET_ENVIRONMENT'], 'environment', 32)
        if os.environ['MODE'] not in ('install', 'upgrade'):
            raise ValueError('mode must be install or upgrade')
        if not re.fullmatch(r'[A-Za-z0-9.-]+', os.environ['SSH_HOST']):
            raise ValueError('SSH_HOST must be a hostname or IPv4 address')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*', os.environ['SSH_USER']):
            raise ValueError('Invalid SSH_USER')
        if not 1 <= int(os.environ['SSH_PORT']) <= 65535 or not 0 <= int(os.environ['HOST_PORT']) <= 65535:
            raise ValueError('Invalid port')
        payload = prepare_parameters(os.environ.get('RUNTIME_ENV', ''), os.environ.get('INSTALL_PARAMS', ''))
        path = Path('parameters.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
        archive = acquire_release(os.environ['RELEASE_URL'], Path('download'), os.environ['RELEASE_SHA256'])
        shutil.copyfile(archive, 'release.tar.gz')
        Path('release.tar.gz.sha256').write_text(os.environ['RELEASE_SHA256'] + '  release.tar.gz\n')
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
