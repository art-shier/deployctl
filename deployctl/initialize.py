"""Initialize a project's delivery contract without replacing existing files."""

from pathlib import Path, PurePosixPath
import re

import yaml

from .contract import relative_path, validate_deployment


def validate_platform(repository, ref):
    if (not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}', repository)
            or repository.split('/')[-1] in ('.', '..')):
        raise ValueError('platform-repository must be a GitHub owner/repository')
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref)
            or '..' in ref or '//' in ref or ref.endswith(('/', '.'))
            or any(part.startswith('.') or part.endswith('.lock') for part in ref.split('/'))):
        raise ValueError('platform-ref must be a tag, commit SHA or valid branch reference')


def release_workflow(repository, ref, deployment_file, test_command, private_platform=False):
    inputs = {'platform-repository': repository, 'platform-ref': ref,
              'deployment-file': deployment_file, 'version': '${{ github.ref_name }}'}
    if test_command:
        inputs['test-command'] = test_command
    workflow = {
        'name': 'Project release', 'on': {'push': {'tags': ['v*']}},
        'permissions': {'contents': 'write', 'packages': 'write'},
        'jobs': {'release': {
            'uses': f'{repository}/.github/workflows/build-release.yml@{ref}', 'with': inputs,
        }},
    }
    if private_platform:
        workflow['jobs']['release']['secrets'] = {'PLATFORM_READ_TOKEN': '${{ secrets.PLATFORM_READ_TOKEN }}'}
    return workflow


def deploy_workflow(repository, ref, app, private_platform=False):
    workflow = {
        'name': 'Deploy existing project release',
        'on': {'workflow_dispatch': {'inputs': {
            'release_url': {'description': 'Published tar.gz asset URL', 'required': True},
            'sha256': {'description': 'Expected package SHA256', 'required': True},
            'environment': {'type': 'choice', 'options': ['staging', 'production'], 'default': 'staging'},
            'mode': {'type': 'choice', 'options': ['install', 'upgrade'], 'default': 'upgrade'},
        }}},
        'permissions': {'contents': 'read'},
        'jobs': {'deploy': {
            'uses': f'{repository}/.github/workflows/deploy.yml@{ref}',
            'with': {'platform-repository': repository, 'platform-ref': ref, 'application': app,
                     'environment': '${{ inputs.environment }}', 'release-url': '${{ inputs.release_url }}',
                     'sha256': '${{ inputs.sha256 }}', 'mode': '${{ inputs.mode }}'},
            'secrets': {name: '${{ secrets.' + name + ' }}'
                        for name in ('SSH_HOST', 'SSH_USER', 'SSH_PRIVATE_KEY', 'SSH_KNOWN_HOSTS')},
        }},
    }
    if private_platform:
        workflow['jobs']['deploy']['secrets']['PLATFORM_READ_TOKEN'] = '${{ secrets.PLATFORM_READ_TOKEN }}'
    return workflow


def inside_project(project, name, field):
    relative_path(name, field)
    target = project / name
    if not target.resolve().is_relative_to(project):
        raise ValueError(f'{field} must stay inside the project, including symlink targets')
    return target


def output_path(project, name, field, workflow=False):
    target = inside_project(project, name, field)
    if target.suffix not in ('.yaml', '.yml'):
        raise ValueError(f'{field} must name a YAML file')
    if workflow and PurePosixPath(name).parts[:2] != ('.github', 'workflows'):
        raise ValueError(f'{field} must be inside .github/workflows')
    if target.exists() or target.is_symlink():
        raise ValueError(f'{field} already exists: {name}; inspect/merge it or choose another output path')
    for parent in target.parents:
        if parent == project:
            break
        if parent.exists() and not parent.is_dir():
            raise ValueError(f'{field} parent is not a directory: {parent.name}')
    return target


def initialize_project(application, directory, platform_repository, platform_ref,
                       port=8080, health_path='/health/ready', required_config=None,
                       dockerfile='Dockerfile', context='.',
                       deployment_file='deploy/deployment.yaml',
                       workflow_file='.github/workflows/release.yml',
                       with_deploy_workflow=False, deploy_workflow_file='.github/workflows/deploy.yml',
                       test_command='', dry_run=False, private_platform=False):
    project = Path(directory).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('project directory must already exist')
    validate_platform(platform_repository, platform_ref)
    config = validate_deployment({
        'schema_version': 1, 'application': application,
        'build': {'dockerfile': dockerfile, 'context': context},
        'container': {'port': port}, 'health': {'readiness_path': health_path},
        'required_config': required_config or [],
    })
    if not inside_project(project, dockerfile, 'build.dockerfile').is_file():
        raise ValueError(f'Dockerfile not found: {dockerfile}; create it for the project runtime first')
    if not inside_project(project, context, 'build.context').is_dir():
        raise ValueError('build.context must be an existing project directory')
    definitions = [
        (deployment_file, config, False, 'deployment-file'),
        (workflow_file, release_workflow(platform_repository, platform_ref, deployment_file, test_command, private_platform),
         True, 'workflow-file'),
    ]
    if with_deploy_workflow:
        definitions.append((deploy_workflow_file,
                            deploy_workflow(platform_repository, platform_ref, application, private_platform),
                            True, 'deploy-workflow-file'))
    paths, files = [], []
    for name, content, workflow, field in definitions:
        target = output_path(project, name, field, workflow)
        if target.resolve() in paths:
            raise ValueError('output paths must be distinct')
        paths.append(target.resolve())
        files.append({'path': target.relative_to(project).as_posix(),
                      'content': yaml.safe_dump(content, sort_keys=False, allow_unicode=True)})
    result = {'application': application, 'directory': str(project),
              'platform_repository': platform_repository, 'platform_ref': platform_ref,
              'dry_run': dry_run, 'files': files}
    if dry_run:
        return result
    created = []
    try:
        # Create/validate every parent before writing either YAML file.
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
        for path, entry in zip(paths, files):
            # Exclusive create protects files that appeared after preflight.
            with path.open('x', encoding='utf-8', newline='\n') as handle:
                created.append(path)
                handle.write(entry['content'])
    except OSError:
        for path in reversed(created):
            # These exact, project-contained paths were created by this operation.
            if path.is_relative_to(project) and path.is_file():
                path.unlink()
        raise
    return result
