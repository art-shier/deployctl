"""User-facing commands; runtime is imported only when needed."""

import argparse
import json
import os
import sys
import tempfile

from . import __version__
from .contract import read_yaml, validate_deployment
from .release import build_release


def parser():
    result = argparse.ArgumentParser(prog='deployctl')
    result.add_argument('--version', action='version', version=__version__)
    sub = result.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='initialize project deployment YAML and GitHub workflow')
    init.add_argument('application')
    init.add_argument('--directory', default='.', help='existing business project directory')
    init.add_argument('--platform-repository', required=True, help='GitHub owner/repository for team-deploy')
    init.add_argument('--platform-ref', required=True, help='same tag or SHA for workflow and platform source')
    init.add_argument('--port', type=int, default=8080, help='actual container HTTP port')
    init.add_argument('--health-path', default='/health/ready')
    init.add_argument('--required-config', action='append', default=[], help='required variable name; repeatable')
    init.add_argument('--dockerfile', default='Dockerfile')
    init.add_argument('--context', default='.')
    init.add_argument('--deployment-file', default='deploy/deployment.yaml')
    init.add_argument('--workflow-file', default='.github/workflows/release.yml')
    init.add_argument('--with-deploy-workflow', action='store_true', help='also create opt-in SSH deploy workflow')
    init.add_argument('--private-platform', action='store_true', help='map PLATFORM_READ_TOKEN for private platform checkout')
    init.add_argument('--deploy-workflow-file', default='.github/workflows/deploy.yml')
    init.add_argument('--test-command', default='')
    init.add_argument('--dry-run', action='store_true', help='show generated YAML without writing files')
    validate = sub.add_parser('validate', help='validate project deployment.yaml')
    validate.add_argument('config')
    package = sub.add_parser('package', help='generate standard release archive and checksum')
    package.add_argument('--config', required=True)
    package.add_argument('--image', required=True)
    package.add_argument('--version', required=True)
    package.add_argument('--output', default='dist')
    package.add_argument('--commit', default='')
    for name in ('install', 'upgrade', 'rollback', 'status', 'logs', 'restart', 'stop'):
        command = sub.add_parser(name)
        command.add_argument('application')
        command.add_argument('--env', required=True)
        command.add_argument('--root', default=os.environ.get('DEPLOY_ROOT', '/opt/deployments'))
        command.add_argument('--config-root', default=os.environ.get('DEPLOY_CONFIG_ROOT', '/etc/deployctl'))
        if name in ('install', 'upgrade'):
            command.add_argument('--release', required=True, help='local archive or HTTPS URL')
            command.add_argument('--sha256', help='expected checksum; default: adjacent .sha256')
        if name == 'install':
            command.add_argument('--port', type=int, help='override host port; preserved across upgrades')
            command.add_argument('--bind', help='override IPv4 bind address; default 127.0.0.1')
        if name == 'logs':
            command.add_argument('--tail', type=int, default=100)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'init':
            from .initialize import initialize_project
            inputs = vars(args).copy()
            inputs.pop('command')
            initialized = initialize_project(**inputs)
            if args.dry_run:
                print(json.dumps(initialized, indent=2, ensure_ascii=False))
            else:
                print(f'OK: initialized {args.application} in {initialized["directory"]}')
                for file in initialized['files']:
                    print(f'  created {file["path"]}')
                print('Verify actual health endpoint, required config and platform ref before publishing.')
        elif args.command == 'validate':
            print(json.dumps(validate_deployment(read_yaml(args.config)), indent=2))
        elif args.command == 'package':
            print(build_release(read_yaml(args.config), args.image, args.version, args.output, args.commit))
        else:
            from .contract import validate_name
            from .download import acquire_release
            from .runtime import Manager
            validate_name(args.application)
            validate_name(args.env, 'environment', 32)
            manager = Manager(args.root, args.config_root)
            if args.command in ('install', 'upgrade'):
                with tempfile.TemporaryDirectory(prefix='deployctl-download-') as cache:
                    package = acquire_release(args.release, cache, args.sha256)
                    state = manager.deploy(args.application, args.env, package,
                                           upgrade=args.command == 'upgrade',
                                           port=getattr(args, 'port', None), bind=getattr(args, 'bind', None))
                print(f"OK: {args.application}/{args.env} running {state['current']}")
            elif args.command == 'rollback':
                state = manager.rollback(args.application, args.env)
                print(f"OK: recovered {args.application}/{args.env}; current={state['current']}")
            else:
                if args.command == 'logs' and not 1 <= args.tail <= 10000:
                    raise ValueError('--tail must be 1..10000')
                print(manager.operate(args.application, args.env, args.command, getattr(args, 'tail', 100)))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted; inspect status and use rollback if a transaction is pending', file=sys.stderr)
        return 130
