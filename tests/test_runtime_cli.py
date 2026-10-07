from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from deployctl.cli import main, parser


class RuntimeCLITests(unittest.TestCase):
    def invoke(self, flags):
        with patch('deployctl.runtime.Manager') as manager, \
                patch('deployctl.download.acquire_release', return_value=Path('archive')):
            manager.return_value.deploy.return_value = {'current': {'version': 'v1.0.0'}}
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                result = main(['install', 'project-a', '--env', 'production', '--release', 'archive', *flags])
        return result, manager.return_value.deploy.call_args, output.getvalue()

    def test_env_keeps_environment_and_new_maps_are_separate(self):
        result, call, text = self.invoke(['--env-var', 'TEXT= runtime $ = ', '--set', 'TEXT=hook only'])
        self.assertEqual(result, 0)
        self.assertEqual(call.args[:2], ('project-a', 'production'))
        self.assertEqual(call.kwargs['runtime_env'], {'TEXT': ' runtime $ = '})
        self.assertEqual(call.kwargs['install_params'], {'TEXT': 'hook only'})
        self.assertIn('running v1.0.0', text)
        self.assertNotIn('hook only', text)
        self.assertNotIn('runtime $', text)

    def test_unset_only_available_for_upgrade(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser().parse_args(['install', 'a', '--env', 'p', '--release', 'x', '--unset-env', 'A'])
        args = parser().parse_args(['upgrade', 'a', '--env', 'p', '--release', 'x', '--unset-env', 'A'])
        self.assertEqual(args.unset_env, ['A'])

    def test_invalid_assignments_rejected_before_downloading(self):
        with patch('deployctl.download.acquire_release') as acquire, redirect_stderr(io.StringIO()):
            result = main(['install', 'a', '--env', 'p', '--release', 'x', '--env-var', 'A=x', '--env-var', 'A=y'])
        self.assertEqual(result, 1)
        acquire.assert_not_called()


if __name__ == '__main__': unittest.main()
