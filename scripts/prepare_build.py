"""Validate the actual project and emit inputs for the reusable build workflow."""

import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployctl.build import render_build_args, resolve_build_args
from deployctl.contract import read_yaml, relative_path, validate_deployment, version


def main():
    try:
        relative_path(os.environ['DEPLOYMENT_FILE'], 'deployment-file')
        version(os.environ['RELEASE_VERSION'])
        config = validate_deployment(read_yaml(os.environ['DEPLOYMENT_FILE']))
        registry = os.environ['REGISTRY'].lower()
        image_name = (os.environ.get('IMAGE_NAME') or os.environ['GITHUB_REPOSITORY']).lower()
        if not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]+)?', registry):
            raise ValueError('Invalid registry host')
        if not re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*', image_name):
            raise ValueError('Invalid image repository path')
        if config['schema_version'] == 2:
            from deployctl.multiservice_build import delivery_plan, component_build_args
            if os.environ.get('BUILD_ARGS','').strip():
                raise ValueError('multi-service build-args must be declared per service in build.args')
            if os.environ.get('PLATFORMS',config['platform']) != config['platform']:
                raise ValueError('workflow platforms differs from deployment platform')
            config = component_build_args(config, os.environ.get('SERVICE_BUILD_ARGS',''))
            plan = delivery_plan(config,Path.cwd(),registry+'/'+image_name,os.environ['RELEASE_VERSION'])
            Path('normalized.json').write_text(json.dumps(config),encoding='utf-8')
            Path('image-plan.json').write_text(json.dumps(plan),encoding='utf-8')
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8',newline='\n') as out:
                for key,value in {'schema_version':2,'application':config['application'],'image_name':plan['repository']}.items():
                    print(f'{key}={value}',file=out)
            return 0
        if os.environ.get('SERVICE_BUILD_ARGS','').strip():
            raise ValueError('service-build-args requires a multi-service declaration')
        for key in ('dockerfile', 'context'):
            candidate = Path(config['build'][key]).resolve()
            if not candidate.is_relative_to(Path.cwd().resolve()) or not candidate.exists():
                raise ValueError('Build paths must exist inside the project')
        arguments = resolve_build_args(config['build'].get('args', {}), os.environ.get('BUILD_ARGS', ''))
        encoded = render_build_args(arguments)
        delimiter = 'BUILD_ARGS_' + uuid.uuid4().hex
        outputs = {
            'schema_version': str(config['schema_version']),
            'application': config['application'],
            'context': 'source/' + config['build']['context'],
            'dockerfile': 'source/' + config['build']['dockerfile'],
            'image_name': registry + '/' + image_name,
        }
        Path('normalized.json').write_text(json.dumps(config), encoding='utf-8')
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8', newline='\n') as out:
            for name, value in outputs.items():
                print(name + '=' + value, file=out)
            print('build_args<<' + delimiter, file=out)
            print(encoded, file=out)
            print(delimiter, file=out)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
