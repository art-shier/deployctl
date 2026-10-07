"""Real Docker + registry + HTTP integration test. Run on a disposable Linux runner."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl.contract import read_yaml
from deployctl.release import build_release
from deployctl.runtime import project_name


def run(*args, input=None, check=True):
    result = subprocess.run(list(map(str, args)), input=input, capture_output=True, text=True, cwd=ROOT)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed:\n{result.stdout}\n{result.stderr}')
    return result


def free_port():
    with socket.socket() as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


def main():
    if sys.platform != 'linux':
        raise SystemExit('Real Docker integration requires Linux; do not count a skip as passing')
    registry_port, app_port = free_port(), free_port()
    while app_port == registry_port:
        app_port = free_port()
    suffix = uuid.uuid4().hex[:10]
    registry = 'deployctl-test-registry-' + suffix
    app = 'integration-' + suffix
    project = project_name(app, 'test')
    image = f'127.0.0.1:{registry_port}/example:good'
    bad_image = f'127.0.0.1:{registry_port}/example:bad'
    try:
        run('docker', 'run', '-d', '--name', registry, '-p', f'127.0.0.1:{registry_port}:5000', 'registry:2')
        for attempt in range(30):
            try:
                with urlopen(f'http://127.0.0.1:{registry_port}/v2/', timeout=1):
                    break
            except OSError:
                time.sleep(1)
        run('docker', 'build', '-t', image, ROOT / 'examples/project-a')
        run('docker', 'push', image)
        good_digest = run('docker', 'image', 'inspect', image, '--format', '{{index .RepoDigests 0}}').stdout.strip()
        run('docker', 'build', '-t', bad_image, '-f', '-', ROOT / 'examples/project-a',
            input=f'FROM {image}\nENV FAIL_READINESS=1\n')
        run('docker', 'push', bad_image)
        bad_digest = run('docker', 'image', 'inspect', bad_image, '--format', '{{index .RepoDigests 0}}').stdout.strip()
        with tempfile.TemporaryDirectory(prefix='deployctl-integration-') as tmp:
            base = Path(tmp)
            config = read_yaml(ROOT / 'examples/project-a/deploy/deployment.yaml')
            config['application'] = app
            config['health']['startup_timeout_seconds'] = 5
            releases = [build_release(config, digest, version, base / 'packages') for digest, version in
                        [(good_digest, 'v1.0.0'), (good_digest, 'v1.1.0'), (bad_digest, 'v1.2.0')]]
            config_dir = base / 'config' / app / 'test'
            config_dir.mkdir(parents=True)
            (config_dir / 'config.env').write_text('RAW_VALUE=literal$secret#value\n')
            (config_dir / 'secrets.env').write_text('')
            (config_dir / 'secrets.env').chmod(0o600)
            def cli(command, *extra, check=True):
                return run(sys.executable, '-m', 'deployctl', command, app, '--env', 'test',
                           '--root', base / 'apps', '--config-root', base / 'config', *extra, check=check)
            def actual_version():
                with urlopen(f'http://127.0.0.1:{app_port}/version', timeout=3) as response:
                    return json.load(response)['version']
            cli('install', '--release', releases[0], '--port', app_port)
            assert actual_version() == 'v1.0.0'
            cli('upgrade', '--release', releases[1])
            assert actual_version() == 'v1.1.0'
            failure = cli('upgrade', '--release', releases[2], check=False)
            assert failure.returncode != 0 and 'restored' in failure.stderr
            assert actual_version() == 'v1.1.0'
            cli('rollback')
            assert actual_version() == 'v1.0.0'
            assert 'v1.0.0' in cli('status').stdout
            assert 'started' in cli('logs').stdout
            container = run('docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}').stdout.strip()
            values = json.loads(run('docker', 'inspect', '--format', '{{json .Config.Env}}', container).stdout)
            assert 'RAW_VALUE=literal$secret#value' in values
            cli('stop')
            cli('restart')
            assert actual_version() == 'v1.0.0'
        print('PASS: real install, upgrade, failed-upgrade recovery, rollback, raw env, status/logs and restart')
    finally:
        containers = run('docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}', check=False).stdout.split()
        if containers:
            run('docker', 'rm', '-f', *containers, check=False)
        run('docker', 'network', 'rm', project + '_default', check=False)
        run('docker', 'rm', '-f', registry, check=False)
        run('docker', 'image', 'rm', image, bad_image, check=False)


if __name__ == '__main__':
    main()
