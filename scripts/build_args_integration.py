"""Prepare and verify the actual Docker action's build argument transport."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'deployctl-build-args:test'


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'Dockerfile').write_text('''FROM busybox:1.37.0
ARG BUILD_PROFILE=from-dockerfile
ARG KEEP=from-dockerfile
ARG FLAGS=default
ARG EMPTY=not-empty
ARG DOCKER_ONLY=kept-from-dockerfile
RUN printf '%s\\n' "$BUILD_PROFILE" "$KEEP" "$FLAGS" "$EMPTY" "$DOCKER_ONLY" > /build-values
CMD ["cat", "/build-values"]
''', encoding='utf-8')
    # JSON is a YAML subset accepted by the real deployment contract reader.
    config = {'schema_version': 1, 'application': 'build-args-fixture',
              'build': {'dockerfile': 'Dockerfile', 'context': '.',
                        'args': {'BUILD_PROFILE': 'from-config', 'KEEP': 'kept-from-config',
                                 'FLAGS': 'default', 'EMPTY': 'not-empty'}},
              'container': {'port': 8080}, 'health': {'readiness_path': '/ready'}}
    (directory / 'deployment.yaml').write_text(json.dumps(config), encoding='utf-8')
    env = dict(os.environ, DEPLOYMENT_FILE='deployment.yaml', RELEASE_VERSION='v0.0.1',
               REGISTRY='ghcr.io', IMAGE_NAME='', GITHUB_REPOSITORY='fixture/example',
               BUILD_ARGS='BUILD_PROFILE=from-workflow\nFLAGS=a,b "quoted" $literal $(printf executed)\nEMPTY=')
    subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_build.py')],
                   cwd=directory, env=env, check=True)


def verify():
    if sys.platform != 'linux':
        raise RuntimeError('real Docker build argument verification requires Linux')
    try:
        result = subprocess.run(['docker', 'run', '--rm', IMAGE], check=True,
                                capture_output=True, text=True)
        expected = 'from-workflow\nkept-from-config\na,b "quoted" $literal $(printf executed)\n\nkept-from-dockerfile\n'
        if result.stdout != expected:
            raise RuntimeError('actual Docker build arguments do not match defaults, overrides or literal values')
        print('PASS: actual Docker action, project defaults, workflow overrides, Dockerfile defaults, empty and literal values')
    finally:
        subprocess.run(['docker', 'image', 'rm', IMAGE], check=False, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_command = commands.add_parser('prepare')
    prepare_command.add_argument('--directory', required=True)
    commands.add_parser('verify')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.directory)
    else:
        verify()


if __name__ == '__main__':
    main()
