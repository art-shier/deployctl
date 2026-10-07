import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {'schema_version': 1, 'application': 'project-a',
          'build': {'dockerfile': 'Dockerfile', 'context': '.'},
          'container': {'port': 8080}, 'health': {'readiness_path': '/ready'}}


def github_outputs(path):
    outputs = {}
    lines = iter(path.read_text(encoding='utf-8').splitlines())
    for line in lines:
        if '<<' in line:
            name, delimiter = line.split('<<', 1)
            body = []
            for item in lines:
                if item == delimiter:
                    break
                body.append(item)
            outputs[name] = '\n'.join(body)
        else:
            name, value = line.split('=', 1)
            outputs[name] = value
    return outputs


class BuildArgsTests(unittest.TestCase):
    def config(self, args):
        config = copy.deepcopy(CONFIG)
        config['build']['args'] = args
        return config

    def test_project_args_are_validated_and_literal_strings_preserved(self):
        from deployctl.contract import validate_deployment
        args = {'BUILD_PROFILE': 'production', 'http_proxy': 'http://proxy:8080',
                'OPTIONS': 'a,b "quoted" $literal $(no-execution)', 'EMPTY': ''}
        self.assertEqual(validate_deployment(self.config(args))['build']['args'], args)

    def test_old_config_does_not_gain_build_only_fields(self):
        from deployctl.contract import validate_deployment
        self.assertEqual(validate_deployment(CONFIG)['build'], {'dockerfile': 'Dockerfile', 'context': '.'})

    def test_args_reject_invalid_names_types_controls_and_surrounding_spaces(self):
        from deployctl.contract import validate_deployment
        cases = [None, [], {'bad-key': 'value'}, {'1BAD': 'value'}, {1: 'value'},
                 {'VALUE': 1}, {'VALUE': True}, {'VALUE': None}, {'VALUE': ['a']},
                 {'VALUE': 'line\nOTHER=injected'}, {'VALUE': '\r'}, {'VALUE': '\0'},
                 {'VALUE': 'trailing '}, {'VALUE': ' leading'},
                 {'VALUE': 'trailing\ufeff'}, {'VALUE': '\ufeffleading'}, {'VALUE': 'a' * 4097},
                 {f'ARG_{n}': '' for n in range(129)}]
        for args in cases:
            with self.subTest(args=repr(args)[:80]):
                with self.assertRaisesRegex(ValueError, 'build.args'):
                    validate_deployment(self.config(args))

    def test_workflow_overrides_defaults_and_can_explicitly_clear_value(self):
        from deployctl.build import resolve_build_args
        defaults = {'PROFILE': 'default', 'KEEP': 'kept', 'CLEAR': 'old'}
        result = resolve_build_args(defaults, 'PROFILE=production\nCLEAR=\nNEW=a=b\n\n')
        self.assertEqual(result, {'PROFILE': 'production', 'KEEP': 'kept', 'CLEAR': '', 'NEW': 'a=b'})
        self.assertEqual(defaults, {'PROFILE': 'default', 'KEEP': 'kept', 'CLEAR': 'old'})

    def test_workflow_duplicates_bare_keys_and_control_injection_are_rejected(self):
        from deployctl.build import resolve_build_args
        for text in ['ARG=a\nARG=b', 'BARE', 'ARG =value', 'ARG=value\rOTHER=x',
                     'ARG=value\0', 'ARG=value ', 'ARG=' + 'a' * 4097]:
            with self.subTest(text=text[:80]):
                with self.assertRaisesRegex(ValueError, 'build-args'):
                    resolve_build_args({}, text)

    def test_merged_argument_limit_applies_after_override(self):
        from deployctl.build import resolve_build_args
        with self.assertRaisesRegex(ValueError, '128'):
            resolve_build_args({f'ARG_{n}': '' for n in range(128)}, 'EXTRA=value')

    def test_action_csv_preserves_commas_quotes_dollars_equals_and_empty_values(self):
        from deployctl.build import render_build_args
        rendered = render_build_args({'FLAGS': 'a,b "quoted" $literal $(no-execution)',
                                      'EMPTY': '', 'EQ': 'a=b'})
        decoded = list(csv.reader(io.StringIO(rendered)))
        self.assertEqual(decoded, [['FLAGS=a,b "quoted" $literal $(no-execution)'], ['EMPTY='], ['EQ=a=b']])

    def test_release_omits_build_args_and_keeps_v1_server_contract(self):
        from deployctl.release import build_release, unpack_release
        config = self.config({'BUILD_PROFILE': 'production'})
        with tempfile.TemporaryDirectory() as tmp:
            package = build_release(config, 'ghcr.io/example/app@sha256:' + 'a' * 64,
                                    'v1.0.0', tmp)
            with tarfile.open(package) as archive:
                release = yaml.safe_load(archive.extractfile('release.yaml'))
            self.assertEqual(release['deployment']['build'], {'dockerfile': 'Dockerfile', 'context': '.'})
            self.assertEqual(release['minimum_deployctl_version'], '1.0.0')
            self.assertEqual(unpack_release(package, Path(tmp) / 'unpacked')['version'], 'v1.0.0')
        self.assertEqual(config['build']['args'], {'BUILD_PROFILE': 'production'})

    def prepare(self, directory, overrides=''):
        config = self.config({'BUILD_PROFILE': 'from-config', 'KEEP': 'default'})
        (directory / 'deployment.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
        (directory / 'Dockerfile').write_text('FROM scratch\n', encoding='utf-8')
        output = directory / 'outputs'
        env = dict(os.environ, DEPLOYMENT_FILE='deployment.yaml', RELEASE_VERSION='v1.0.0',
                   REGISTRY='ghcr.io', IMAGE_NAME='', GITHUB_REPOSITORY='Example/App',
                   GITHUB_OUTPUT=str(output), BUILD_ARGS=overrides)
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_build.py')],
                                cwd=directory, env=env, capture_output=True, text=True)
        return result, output

    def test_real_workflow_preparation_emits_effective_action_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result, output = self.prepare(directory, 'BUILD_PROFILE=from-workflow\nFLAGS=a,b "q" $literal\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            data = github_outputs(output)
            self.assertEqual(data['image_name'], 'ghcr.io/example/app')
            self.assertEqual(data['context'], 'source/.')
            self.assertEqual(list(csv.reader(io.StringIO(data['build_args']))),
                             [['BUILD_PROFILE=from-workflow'], ['KEEP=default'], ['FLAGS=a,b "q" $literal']])

    def test_invalid_workflow_override_emits_no_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.prepare(Path(tmp), 'BAD-KEY=value')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('build-args', result.stderr)
            self.assertFalse(output.exists())

    def test_project_can_add_args_after_init_without_regenerating_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'Dockerfile').write_text('FROM scratch\n', encoding='utf-8')
            init = subprocess.run([sys.executable, '-m', 'deployctl', 'init', 'project-a',
                                   '--directory', str(project), '--platform-repository', 'example/platform',
                                   '--platform-ref', 'v1.4.0'], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(init.returncode, 0, init.stderr)
            file = project / 'deploy/deployment.yaml'
            config = yaml.safe_load(file.read_text())
            self.assertNotIn('args', config['build'])
            workflow = (project / '.github/workflows/release.yml').read_bytes()
            config['build']['args'] = {'BUILD_PROFILE': 'production'}
            file.write_text(yaml.safe_dump(config), encoding='utf-8')
            check = subprocess.run([sys.executable, '-m', 'deployctl', 'validate', str(file)],
                                   cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(json.loads(check.stdout)['build']['args'], {'BUILD_PROFILE': 'production'})
            self.assertEqual((project / '.github/workflows/release.yml').read_bytes(), workflow)
