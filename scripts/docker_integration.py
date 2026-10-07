"""Real Docker + registry + HTTP integration test. Run on a disposable Linux runner."""

import json
import copy
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
from deployctl.runtime import DockerDriver, Manager, atomic_json, project_name


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
        # A normal lowercase image ENV is inherited by both old and new services.
        run('docker', 'build', '-t', image, '-f', '-', ROOT / 'examples/project-a',
            input=f'FROM {image}\nENV http_proxy=http://proxy.invalid:8080\n')
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
            literal = ' a,b "quoted" $literal #tag = '
            cli('install', '--release', releases[0], '--port', app_port,
                '--env-var', 'RAW_VALUE=' + literal, '--env-var', 'EMPTY=')
            assert actual_version() == 'v1.0.0'
            cli('upgrade', '--release', releases[1])
            assert actual_version() == 'v1.1.0'
            failure = cli('upgrade', '--release', releases[2], '--env-var', 'RAW_VALUE=bad candidate', check=False)
            assert failure.returncode != 0 and 'restored' in failure.stderr
            assert actual_version() == 'v1.1.0'
            cli('rollback')
            assert actual_version() == 'v1.0.0'
            assert 'v1.0.0' in cli('status').stdout
            assert 'started' in cli('logs').stdout
            container = run('docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}').stdout.strip()
            def state():
                return json.loads((base / 'apps' / app / 'test/state.json').read_text())

            def container_config():
                identifier = run('docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}').stdout.strip()
                result = run('docker', 'exec', identifier, 'python', '-c',
                    'import json,os; d=json.load(open(os.environ["DEPLOYCTL_ENV_FILE"])); '
                    'assert all(os.environ[k]==v for k,v in d.items()); '
                    'assert not any(k.startswith("DEPLOYCTL_PARAM_") for k in os.environ); '
                    'assert os.getuid()==10001; print(json.dumps(d))')
                return json.loads(result.stdout)

            assert container_config()['RAW_VALUE'] == literal
            assert container_config()['EMPTY'] == ''
            write = run('docker', 'exec', container, 'python', '-c',
                        'import os; open(os.environ["DEPLOYCTL_ENV_FILE"],"w").write("changed")', check=False)
            assert write.returncode != 0
            old_ref = state()['current']
            cli('upgrade', '--release', releases[0], '--env-var', 'RAW_VALUE=changed same version')
            assert state()['previous'] == old_ref
            assert container_config()['RAW_VALUE'] == 'changed same version'
            cli('rollback')
            assert state()['current'] == old_ref and container_config()['RAW_VALUE'] == literal
            cli('upgrade', '--release', releases[1])
            previous = state()['previous']
            cli('upgrade', '--release', releases[1])
            assert state()['previous'] == previous
            cli('upgrade', '--release', releases[1], '--unset-env', 'RAW_VALUE')
            assert container_config()['RAW_VALUE'] == 'literal$secret#value'
            cli('rollback')
            assert container_config()['RAW_VALUE'] == literal

            # Actual host scripts read both JSON maps, verify ordering against HTTP,
            # and control failures without exposing fixture values in logs.
            source = base / 'hook-source'
            source.mkdir()
            script = '''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json,os,time
from pathlib import Path
from urllib.request import urlopen
runtime=json.load(open(os.environ['DEPLOYCTL_ENV_FILE']))
params=json.load(open(os.environ['DEPLOYCTL_PARAMS_FILE']))
assert runtime['RAW_VALUE']==os.environ['RAW_VALUE']
assert params['TEXT']==os.environ['DEPLOYCTL_PARAM_TEXT']==' a,b "quoted" $literal #tag = '
assert 'DEPLOYCTL_PARAM_TEXT' not in runtime
assert 'GH_TOKEN' not in os.environ and 'GITHUB_TOKEN' not in os.environ
phase=PHASE
with urlopen('http://127.0.0.1:'+params['PORT']+'/version') as response:
 actual=json.load(response)['version']
expected=os.environ['DEPLOYCTL_PREVIOUS_VERSION'] if phase=='pre' else os.environ['DEPLOYCTL_VERSION']
assert actual==expected
with open(params['MARKER'],'a') as marker: marker.write(phase+'\\n')
if params.get('FAIL')==phase: raise SystemExit(7)
if params.get('FAIL')=='timeout' and phase=='pre': time.sleep(30)
PY
'''
            for phase in ('pre', 'post'):
                (source / f'{phase}.sh').write_text(script.replace('PHASE', repr(phase)), newline='\n')
            hooked = copy.deepcopy(config)
            hooked['hooks'] = {phase + '_install': {'script': phase + '.sh', 'timeout_seconds': 15}
                               for phase in ('pre', 'post')}
            hooks_archive = build_release(hooked, good_digest, 'v1.3.0', base / 'packages', project_root=source)
            timeout_config = copy.deepcopy(hooked)
            timeout_config['hooks']['pre_install']['timeout_seconds'] = 1
            timeout_archive = build_release(timeout_config, good_digest, 'v1.4.0', base / 'packages', project_root=source)
            marker = base / 'order.txt'
            params = ['--set', f'PORT={app_port}', '--set', f'MARKER={marker}', '--set', 'TEXT=' + literal]
            cli('upgrade', '--release', hooks_archive, '--env-var', 'RAW_VALUE=hook success', *params)
            assert marker.read_text().splitlines() == ['pre', 'post']
            assert container_config()['RAW_VALUE'] == 'hook success'
            for phase, archive in (('pre', hooks_archive), ('post', hooks_archive), ('timeout', timeout_archive)):
                before = state()['current']
                previous = state()['previous']
                failure = cli('upgrade', '--release', archive, '--env-var', 'RAW_VALUE=failed hook',
                              *params, '--set', 'FAIL=' + phase, check=False)
                assert failure.returncode != 0
                assert state()['current'] == before and state()['previous'] == previous
                assert state()['transaction'] is None
                assert actual_version() == 'v1.3.0'
                assert container_config()['RAW_VALUE'] == 'hook success'
            assert all(p.stat().st_size <= 65536 and p.stat().st_mode & 0o777 == 0o600
                       for p in (config_dir / 'hook-logs').iterdir())
            order_before = marker.read_text()
            cli('rollback')
            assert marker.read_text() == order_before
            assert actual_version() == 'v1.1.0' and container_config()['RAW_VALUE'] == literal
            cli('stop')
            cli('restart')
            assert actual_version() == 'v1.1.0'
            # Recreate the v1 server layout/container, including no configuration
            # label, then prove a failed pre does not falsely promote its baseline.
            home = base / 'apps' / app / 'test'
            old_dir = home / 'releases/v1.1.0'
            old_release = read_yaml(old_dir / 'release.yaml')
            binding = {'address': '127.0.0.1', 'port': app_port}
            legacy_env = Manager(base / 'apps', base / 'config').docker_environment(config_dir, binding)
            (config_dir / 'config.env').write_text('RAW_VALUE=legacy actual\n')
            driver = DockerDriver()
            driver.up(old_dir, project, legacy_env)
            driver.probe(old_dir, project, legacy_env, old_release, binding)
            atomic_json(home / 'state.json', {'schema_version': 1, 'application': app, 'environment': 'test',
                        'current': 'v1.1.0', 'previous': None, 'transaction': None, 'binding': binding})
            (config_dir / 'config.env').write_text('RAW_VALUE=edited server\n')
            failure = cli('upgrade', '--release', hooks_archive, *params, '--set', 'FAIL=pre', check=False)
            assert failure.returncode != 0 and state()['current']['legacy']
            assert 'io.team-deploy.configuration' not in driver.inspect_container(old_dir, project, legacy_env)['labels']
            cli('restart')
            failure = cli('upgrade', '--release', hooks_archive, *params, '--set', 'FAIL=post', check=False)
            assert failure.returncode != 0 and not state()['current']['legacy']
            assert container_config()['RAW_VALUE'] == 'legacy actual'
            cli('restart')
        print('PASS: real install/upgrade, JSON/raw env and nonroot read-only mount, pre/post/timeout failures, configuration recovery/unset/same-version rollback, status/logs and restart')
    finally:
        containers = run('docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}', check=False).stdout.split()
        if containers:
            run('docker', 'rm', '-f', *containers, check=False)
        run('docker', 'network', 'rm', project + '_default', check=False)
        run('docker', 'rm', '-f', registry, check=False)
        run('docker', 'image', 'rm', image, bad_image, check=False)


if __name__ == '__main__':
    main()
