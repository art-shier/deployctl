"""User-facing commands; runtime is imported only when needed."""

import argparse
import json
import os
import sys
import tempfile

from . import __version__
from .contract import read_yaml, validate_deployment
from .release import build_release
from .runtime_config import parse_assignments, validate_unset


def parser():
    result = argparse.ArgumentParser(prog='deployctl')
    result.add_argument('--version', action='version', version=__version__)
    sub = result.add_subparsers(dest='command', required=True)
    server = sub.add_parser('server', help='install or upgrade the independent ctl platform from a release bundle')
    operations = server.add_subparsers(dest='server_command', required=True)
    for name in ('install', 'upgrade'):
        operation = operations.add_parser(name)
        operation.add_argument('--release', required=True, help='server bundle HTTPS URL or local archive')
        operation.add_argument('--sha256', help='expected checksum; default: adjacent .sha256')
        operation.add_argument('--home', default='/opt/ctl-platform')
        operation.add_argument('--origin', help='initial public API origin; default: https://ctl.shier.art')
        operation.add_argument('--registry-host', help='initial Registry host; default: ctl.shier.art')
        operation.add_argument('--api-port', type=int)
        operation.add_argument('--registry-port', type=int)
        operation.add_argument('--database-url-file', help='private file containing an external PostgreSQL URL')
    login = sub.add_parser('login', help='save private platform credentials')
    login.add_argument('--server', help='override saved server for this login; default: saved server or https://ctl.shier.art')
    login.add_argument('--token-file', help='private file containing a scoped token; otherwise prompt')
    login.add_argument('--client-config', help='private client configuration; default: ~/.ctl/client.json')
    config = sub.add_parser('config', help='view or change the default management server')
    config_commands = config.add_subparsers(dest='config_command', required=True)
    for name in ('get', 'set'):
        setting = config_commands.add_parser(name)
        setting.add_argument('key', choices=['server'])
        if name == 'set':
            setting.add_argument('value', help='HTTPS origin or hostname; hostname defaults to HTTPS')
        setting.add_argument('--client-config', help='private client configuration; default: ~/.ctl/client.json')
    for name,help_text in [('whoami','show the current platform role and project/group scope'),('projects','list projects accessible to the current login')]:
        discovery=sub.add_parser(name,help=help_text)
        discovery.add_argument('--client-config',help='private client configuration; default: ~/.ctl/client.json')
    publish = sub.add_parser('publish', help='publish a standard package to the platform')
    publish.add_argument('application')
    publish.add_argument('--version', required=True)
    publish.add_argument('--package', required=True)
    publish.add_argument('--channel', choices=['stable'])
    publish.add_argument('--client-config', help='private client configuration; default: ~/.ctl/client.json')
    publish.add_argument('--registry-token-file', help='private short-lived pull verification token for an external private Registry')
    self_update = sub.add_parser('self-update', help='update the installed CLI itself; leaves deployed services unchanged')
    self_update.add_argument('--version', default='latest', help='tool Release tag; default: latest published version')
    self_update.add_argument('--sha256', help='optional independently obtained CLI artifact checksum')
    self_update.add_argument('--release-id', help=argparse.SUPPRESS)
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
        command.add_argument('--env')
        command.add_argument('--prod', action='store_true', help='alias for --env prod')
        command.add_argument('--root', default=os.environ.get('DEPLOY_ROOT', '/opt/deployments'))
        command.add_argument('--config-root', default=os.environ.get('DEPLOY_CONFIG_ROOT', '/etc/deployctl'))
        if name in ('install', 'upgrade'):
            command.add_argument('--quiet', action='store_true', help='hide progress; preserve result and errors')
            command.add_argument('--release', help='legacy local archive or HTTPS URL')
            command.add_argument('--version', help='managed release version; default: environment target')
            command.add_argument('--with-platform-config', action='store_true', help='overlay management configuration on an explicit release')
            command.add_argument('--client-config', help='private client configuration; default: ~/.ctl/client.json')
            command.add_argument('--sha256', help='expected checksum; default: adjacent .sha256')
            command.add_argument('--env-var', action='append', default=[], metavar='KEY=value',
                                 help='runtime value persisted after success; repeat for each key')
            command.add_argument('--set', action='append', default=[], metavar='KEY=value',
                                 help='invocation-local installation hook parameter; repeat for each key')
        if name == 'upgrade':
            command.add_argument('--unset-env', action='append', default=[], metavar='KEY',
                                 help='remove a persisted runtime override; repeat for each key')
        if name in ('install','upgrade'):
            command.add_argument('--port', type=int, help='override host port; preserved across upgrades')
            command.add_argument('--bind', help='override IPv4 bind address; default 127.0.0.1')
        if name == 'logs':
            command.add_argument('--tail', type=int, default=100)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'server':
            from .server_bundle import deploy_server
            state = deploy_server(args.release, home=args.home, expected_sha256=args.sha256,
                upgrade=args.server_command == 'upgrade', origin=args.origin, registry_host=args.registry_host,
                api_port=args.api_port, registry_port=args.registry_port, database_url_file=args.database_url_file)
            print(f"OK: ctl server running {state['current']['version']}; instance: {args.home}")
        elif args.command == 'config':
            from .platform_credentials import Credentials
            if args.config_command == 'get':
                print(Credentials.get_server(args.client_config))
            else:
                server, logged_out = Credentials.set_server(args.client_config, args.value)
                print(f'OK: default server set to {server}')
                if logged_out:
                    print('Previous login cleared; run ctl login for this server')
        elif args.command in ('login','publish','whoami','projects'):
            from .platform_credentials import Credentials, read_token_file
            from .platform_client import PlatformClient
            if args.command == 'login':
                import getpass
                from .platform_client import validate_origin
                server = validate_origin(args.server) if args.server else Credentials.get_server(args.client_config)
                print(f'Connecting to {server}')
                token = read_token_file(args.token_file) if args.token_file else getpass.getpass('Platform token: ')
                credentials = Credentials(server, token)
                identity = PlatformClient(credentials).json('GET','/api/v1/me')
                if identity.get('schema_version') != 1: raise ValueError('unsupported platform')
                summary=identity_summary(identity,server)
                Credentials.save(args.client_config, server, token)
                print('OK: platform credentials saved privately')
                print(json.dumps(summary,ensure_ascii=False))
            elif args.command == 'whoami':
                credentials=Credentials.load(args.client_config)
                identity=PlatformClient(credentials).json('GET','/api/v1/me')
                print(json.dumps(identity_summary(identity,credentials.server),ensure_ascii=False,indent=2))
            elif args.command == 'projects':
                credentials=Credentials.load(args.client_config)
                projects=PlatformClient(credentials).json('GET','/api/v1/projects')
                if not isinstance(projects,list) or any(not isinstance(p,dict) for p in projects): raise ValueError('invalid project listing')
                print(json.dumps([{key:p.get(key,'default' if key=='group' else '') for key in ('slug','name','group','default_environment')} for p in projects],ensure_ascii=False,indent=2))
            else:
                from .platform_credentials import read_registry_token_file
                proof=read_registry_token_file(args.registry_token_file) if args.registry_token_file else None
                release = PlatformClient(Credentials.load(args.client_config)).publish(args.application,args.version,args.package,args.channel,proof)
                print(f"OK: published {args.application}/{release['version']}")
        elif args.command == 'self-update':
            from .self_update import update_tool
            state = update_tool(version=args.version, expected_sha256=args.sha256, release_id=args.release_id)
            print(f'Updated tool to {state["version"]} in {state["directory"]}')
            print('Commands: ' + ', '.join(state['commands']))
        elif args.command == 'init':
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
            if args.prod:
                if args.env and args.env != 'prod': raise ValueError('--prod conflicts with --env')
                args.env = 'prod'
            if args.env: validate_name(args.env, 'environment', 32)
            manager = Manager(args.root, args.config_root)
            if args.command in ('install', 'upgrade'):
                from .progress import Progress
                progress = Progress(enabled=not args.quiet)
                manager.progress = progress
                if args.release and args.version: raise ValueError('--release conflicts with --version')
                if args.with_platform_config and not args.release: raise ValueError('--with-platform-config requires --release')
                if args.release and not args.env: raise ValueError('legacy --release requires --env or --prod')
                if not args.release and args.sha256: raise ValueError('--sha256 is only used with --release')
                runtime_env = parse_assignments(args.env_var, 'env-var')
                install_params = parse_assignments(args.set, 'installation parameters', reserve_platform=False)
                unset_env = getattr(args, 'unset_env', [])
                if validate_unset(unset_env).intersection(runtime_env):
                    raise ValueError('a variable cannot be both env-var and unset-env')
                with tempfile.TemporaryDirectory(prefix='deployctl-download-') as cache:
                    client, resolution = None, None
                    managed = {}
                    package = None
                    if args.release:
                        with progress.stage('Loading release package'):
                            package = acquire_release(args.release, cache, args.sha256, progress=progress)
                    if not args.release or args.with_platform_config:
                        from .platform_credentials import Credentials
                        from .platform_client import PlatformClient
                        client = PlatformClient(Credentials.load(args.client_config))
                        requested_version = args.version
                        if package is not None:
                            from .release import unpack_release
                            from pathlib import Path
                            manifest = unpack_release(package, Path(cache)/'inspect')
                            if manifest['application'] != args.application: raise ValueError('release application mismatch')
                            requested_version = manifest['version']
                        with progress.stage(f'Resolving {args.application} release and configuration'):
                            resolution = client.resolve(args.application,args.env,requested_version)
                        args.env = resolution['environment']
                        if package is None:
                            with progress.stage('Downloading and verifying release package'):
                                package = client.download_release(resolution,cache,progress=progress)
                        else:
                            import hashlib
                            if manifest['image'] != resolution['release']['image'] or hashlib.sha256(package.read_bytes()).hexdigest()!=resolution['release']['sha256']:
                                raise ValueError('explicit package differs from the registered platform release')
                        cfg = resolution['configuration']
                        managed = {'managed_runtime':cfg['runtime_env'], 'managed_params':cfg['install_params'],
                                   'deployment_defaults':cfg['deployment_defaults'], 'management_source':{
                                   'origin':client.server,'project':args.application,'environment':args.env,
                                   'release_id':resolution['release']['id'],'revision_id':cfg['id']}}
                    from contextlib import nullcontext
                    registry_context = client.registry_config(resolution['release']['image']) if client else nullcontext(None)
                    from contextlib import ExitStack
                    with ExitStack() as stack:
                        with progress.stage('Preparing Registry authentication'):
                            docker_config = stack.enter_context(registry_context)
                        manager.driver.docker_config = docker_config
                        try:
                            state = manager.deploy(args.application, args.env, package,
                                           upgrade=args.command == 'upgrade',
                                           port=getattr(args, 'port', None), bind=getattr(args, 'bind', None),
                                           runtime_env=runtime_env, unset_env=unset_env, install_params=install_params, **managed)
                        except (ValueError,OSError,RuntimeError):
                            if client:
                                with progress.stage('Submitting failure receipt (best effort)'):
                                    report_receipt(client,resolution,args.config_root,False)
                            raise
                        finally: manager.driver.docker_config = None
                    if client:
                        with progress.stage('Submitting deployment receipt (best effort)'):
                            report_receipt(client,resolution,args.config_root,True)
                print(f"OK: {args.application}/{args.env} running {state['current']['version']}")
            elif args.command == 'rollback':
                args.env = local_environment(args.root,args.application,args.env)
                state = manager.rollback(args.application, args.env)
                current = state['current']['version'] if state['current'] else None
                print(f"OK: recovered {args.application}/{args.env}; current={current}")
            else:
                args.env = local_environment(args.root,args.application,args.env)
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


def identity_summary(identity, server):
    if not isinstance(identity,dict) or identity.get('schema_version') != 1 or identity.get('role') not in ('owner','publisher','deployer'):
        raise ValueError('unsupported platform identity')
    result={'server':server,'all_projects':identity['role']=='owner',**{key:identity.get(key,'') for key in ('id','role','project')}}
    result.update({key:identity.get(key) or [] for key in ('projects','groups','environments')})
    if result['project'] and result['project'] not in result['projects']: result['projects']=[result['project'],*result['projects']]
    return result


def local_environment(root, app, env):
    if env: return env
    from pathlib import Path
    from .contract import validate_name
    from .runtime_snapshot import reject_links
    directory = Path(root)/app
    reject_links(directory)
    candidates = []
    if directory.is_dir():
        for folder in directory.iterdir():
            if folder.is_dir() and (folder/'state.json').is_file():
                reject_links(folder); validate_name(folder.name,'environment',32); candidates.append(folder.name)
    if len(candidates) != 1: raise ValueError('specify --env: no unique installed environment')
    return candidates[0]


def report_receipt(client, resolution, config_root, success):
    """A failed report never changes a completed deployment transaction."""
    from pathlib import Path
    import uuid
    from .runtime_snapshot import reject_links
    from .runtime import atomic_json
    folder=Path(config_root)/'receipts'
    try:
        reject_links(folder); folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700)
        host_path=folder/'host.json';reject_links(host_path)
        if not host_path.exists(): atomic_json(host_path,{'id':uuid.uuid4().hex})
        host=json.loads(host_path.read_text())['id']
        receipt={'id':uuid.uuid4().hex,'host_id':host,'project':resolution['project'],'environment':resolution['environment'],
                 'release_id':resolution['release']['id'],'configuration_revision':resolution['configuration']['id'],
                 'success':success,'cli_version':__version__}
        path=folder/(receipt['id']+'.json')
        atomic_json(path,{'origin':client.server,'receipt':receipt})
        for item in folder.glob('*.json'):
            if item.name=='host.json': continue
            reject_links(item)
            entry=json.loads(item.read_text())
            if entry.get('origin')!=client.server: continue
            r=entry['receipt']
            if r.get('project')!=resolution['project'] or r.get('environment')!=resolution['environment']:continue
            client.json('POST',f"/api/v1/projects/{r['project']}/receipts",r)
            item.unlink()
    except (ValueError,OSError,RuntimeError):
        print('Installation result could not be reported; retry on the next managed install/upgrade',file=sys.stderr)
