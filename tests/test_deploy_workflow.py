from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from deployctl.runtime_config import parse_assignments

ROOT = Path(__file__).resolve().parents[1]


class DeployWorkflowTests(unittest.TestCase):
    def test_remote_values_never_become_shell_source(self):
        from scripts.deploy_remote import build_command
        value = 'a,b "quoted" $literal $(printf executed) = x'
        command = build_command({'runtime_env': {'TEXT': value}, 'install_params': {'TEXT': value}},
                                ['install', 'a', '--env', 'p', '--release', '/tmp/release.tar.gz'])
        self.assertEqual(command[-4:], ['--env-var', 'TEXT=' + value, '--set', 'TEXT=' + value])
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'capture.py'
            capture.write_text('import json,sys; print(json.dumps(sys.argv[1:]))')
            import sys
            result = subprocess.run([sys.executable, str(capture), *command[1:]], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), command[1:])

    def test_empty_payload_preserves_old_deploy_arguments(self):
        from scripts.deploy_remote import build_command
        args = ['upgrade', 'a', '--env', 'p', '--release', '/tmp/release.tar.gz']
        self.assertEqual(build_command({'runtime_env': {}, 'install_params': {}}, args), ['deployctl', *args])

    def test_plain_cli_and_remote_payload_are_equivalent(self):
        from scripts.prepare_deploy import prepare_parameters
        from scripts.deploy_remote import build_command
        inputs = ['TEXT= with $literal = ', 'EMPTY=']
        payload = prepare_parameters('\n' + '\r\n'.join(inputs) + '\n', 'TEXT=param')
        self.assertEqual(payload['runtime_env'], parse_assignments(inputs, 'env-var'))
        command = build_command(payload, ['install', 'a', '--env', 'p', '--release', 'x'])
        self.assertEqual(command[-6:], ['--env-var', inputs[0], '--env-var', inputs[1], '--set', 'TEXT=param'])

    def test_bad_payload_and_duplicate_lines_rejected(self):
        from scripts.prepare_deploy import prepare_parameters
        from scripts.deploy_remote import build_command
        for value in ('A=x\nA=y', 'A', 'A=x\u2028y'):
            with self.assertRaises(ValueError): prepare_parameters(value, '')
        for payload in ({}, {'runtime_env': {'A': 1}, 'install_params': {}},
                        {'runtime_env': {'DEPLOYCTL_X': 'x'}, 'install_params': {}},
                        {'runtime_env': {}, 'install_params': {}, 'extra': 'x'}):
            with self.assertRaises(ValueError): build_command(payload, ['install', 'a'])

    def test_private_values_not_printed_and_failure_exit_propagated(self):
        from scripts.deploy_remote import main
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / 'payload.json'
            payload.write_text(json.dumps({'runtime_env': {'TEXT': 'secret-value'}, 'install_params': {}}))
            responses = [subprocess.CompletedProcess([], 0, '1.5.0\n', ''), subprocess.CompletedProcess([], 7)]
            with patch('scripts.deploy_remote.subprocess.run', side_effect=responses) as run, redirect_stderr(io.StringIO()) as output:
                result = main([str(payload), 'install', 'a', '--env', 'p', '--release', 'x'])
            self.assertEqual(result, 7)
            self.assertNotIn('secret-value', output.getvalue())
            self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_parameters_require_cli_150(self):
        from scripts.deploy_remote import main
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / 'payload.json'
            payload.write_text(json.dumps({'runtime_env': {'A': 'x'}, 'install_params': {}}))
            with patch('scripts.deploy_remote.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '1.4.0', '')) as run, \
                    redirect_stderr(io.StringIO()) as output:
                self.assertEqual(main([str(payload), 'upgrade', 'a', '--env', 'p', '--release', 'x']), 1)
            self.assertEqual(run.call_count, 1)
            self.assertIn('1.5.0', output.getvalue())

    def test_workflow_transfers_payload_without_interpolating_values_in_ssh(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
        inputs = workflow['on']['workflow_call']['inputs'] if 'on' in workflow else workflow[True]['workflow_call']['inputs']
        self.assertIn('runtime-env', inputs)
        self.assertIn('install-params', inputs)
        steps = workflow['jobs']['deploy']['steps']
        remote = steps[-1]['run']
        self.assertIn('parameters.json', remote)
        self.assertIn('deploy_remote.py', remote)
        self.assertNotIn('RUNTIME_ENV', remote)
        self.assertNotIn('INSTALL_PARAMS', remote)


if __name__ == '__main__': unittest.main()
