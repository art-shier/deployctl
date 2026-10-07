import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import signal
import subprocess
import sys
from unittest.mock import patch

from deployctl.contract import validate_deployment
from test_release import CONFIG, IMAGE


class HookEnvironmentTests(unittest.TestCase):
    def test_parent_download_credentials_not_inherited(self):
        from deployctl.hooks import hook_environment
        from deployctl.runtime_snapshot import ConfigurationSnapshot
        s = ConfigurationSnapshot('a' * 32, Path('/tmp/runtime') / ('a' * 32),
                                  {'APP_VERSION': 'v1.0.0', 'DATA_DIR': 'runtime'}, {}, {'DATA_DIR': 'install'}, 'b' * 64)
        with patch.dict(os.environ, {'GH_TOKEN': 'sentinel', 'GITHUB_TOKEN': 'sentinel', 'BASH_ENV': 'bad'}):
            env = hook_environment(s, {'DEPLOYCTL_ACTION': 'install'})
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'BASH_ENV'):
            self.assertFalse(key in env, 'parent credentials/startup controls leaked')
        self.assertEqual(env['DATA_DIR'], 'runtime')
        self.assertEqual(env['DEPLOYCTL_PARAM_DATA_DIR'], 'install')
        self.assertEqual(env['DEPLOYCTL_ENV_FILE'], str(s.directory / '.env.json'))

    def test_bash_startup_control_variables_rejected(self):
        from deployctl.hooks import validate_hook_values
        for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
            with self.assertRaises(ValueError): validate_hook_values({key: 'bad'})


@unittest.skipIf(os.name == 'nt', 'real host Bash/process groups require POSIX; Linux CI runs these')
class NativeHookTests(unittest.TestCase):
    def setUp(self):
        from deployctl.runtime_snapshot import create_snapshot
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.release_dir = self.base / 'release'
        (self.release_dir / 'hooks').mkdir(parents=True)
        release = {'application': 'project-a', 'version': 'v1.0.0', 'image': IMAGE,
                   'deployment': validate_deployment(CONFIG)}
        self.snapshot = create_snapshot(self.base / 'config', 'project-a', 'production', release,
                                        {'APP_VERSION': 'v1.0.0', 'DATA_DIR': 'runtime $ value'}, {},
                                        {'DATA_DIR': 'installation $ value'})

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, text, timeout=5):
        from deployctl.hooks import HookRunner
        path = self.release_dir / 'hooks/pre-install.sh'
        path.write_text(text, encoding='utf-8')
        descriptor = {'path': 'hooks/pre-install.sh', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'timeout_seconds': timeout}
        return HookRunner().run('pre_install', descriptor, self.release_dir, self.snapshot,
                                {'DEPLOYCTL_RELEASE_DIR': str(self.release_dir), 'DEPLOYCTL_ACTION': 'install'})

    def test_hook_reads_same_runtime_json_and_separate_install_params(self):
        self.run_script('''set -eu
python3 - <<'PY'
import json,os
assert json.load(open(os.environ['DEPLOYCTL_ENV_FILE']))['DATA_DIR'] == os.environ['DATA_DIR'] == 'runtime $ value'
assert json.load(open(os.environ['DEPLOYCTL_PARAMS_FILE']))['DATA_DIR'] == os.environ['DEPLOYCTL_PARAM_DATA_DIR'] == 'installation $ value'
assert 'DEPLOYCTL_PARAM_DATA_DIR' not in json.load(open(os.environ['DEPLOYCTL_ENV_FILE']))
assert 'GH_TOKEN' not in os.environ and 'GITHUB_TOKEN' not in os.environ
PY
''')

    def heartbeat(self):
        return f'while true; do echo x >> "{self.base}/heartbeat"; sleep .05; done &\nwait\n'

    def assert_heartbeat_stopped(self):
        path = self.base / 'heartbeat'
        first = path.read_bytes()
        time.sleep(.25)
        self.assertEqual(path.read_bytes(), first)

    def test_timeout_kills_script_and_grandchild(self):
        from deployctl.hooks import HookFailure
        with self.assertRaises(HookFailure) as error:
            self.run_script(self.heartbeat(), timeout=1)
        self.assertTrue(error.exception.timeout)
        self.assert_heartbeat_stopped()

    def test_keyboard_interrupt_cleans_group(self):
        def interrupted(process, timeout):
            deadline = time.monotonic() + 3
            while not (self.base / 'heartbeat').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            raise KeyboardInterrupt()
        with patch('deployctl.hooks.drain_process', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.run_script(self.heartbeat())
        self.assert_heartbeat_stopped()

    def test_large_output_is_drained_and_bounded(self):
        self.run_script("python3 -c 'print(\"x\" * 200000)'\n")
        logs = list((self.base / 'config/hook-logs').glob('*.log'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].stat().st_size, 65536)
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)

    def test_log_write_failure_is_reported_without_secret_output(self):
        with patch('deployctl.hooks.write_log', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'log') as error:
                self.run_script('echo super-secret\n')
        self.assertNotIn('super-secret', str(error.exception))

    def test_normal_completion_closes_background_children(self):
        self.run_script(self.heartbeat().replace('wait\n', 'sleep .15\nexit 0\n'))
        self.assert_heartbeat_stopped()

    def test_nonzero_failure_does_not_echo_output(self):
        from deployctl.hooks import HookFailure
        with self.assertRaises(HookFailure) as error:
            self.run_script('echo super-secret\nexit 7\n')
        self.assertEqual(error.exception.exit_code, 7)
        self.assertNotIn('super-secret', str(error.exception))
        self.assertIn('super-secret', error.exception.log_path.read_text())

    def test_termination_signals_clean_detached_hook_group(self):
        path = self.release_dir / 'hooks/pre-install.sh'
        pid_file = self.base / 'hookpid'
        heartbeat = self.base / 'heartbeat'
        path.write_text(f'echo $$ > "{pid_file}"\n' + self.heartbeat(), encoding='utf-8')
        descriptor = {'path': 'hooks/pre-install.sh', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'timeout_seconds': 30}
        payload = self.base / 'runner.json'
        payload.write_text(json.dumps({'descriptor': descriptor, 'id': self.snapshot.id,
            'directory': str(self.snapshot.directory), 'values': self.snapshot.values,
            'overrides': {}, 'install_params': self.snapshot.install_params, 'sha256': self.snapshot.sha256}))
        code = '''import json,sys
from pathlib import Path
from deployctl.hooks import HookRunner
from deployctl.runtime_snapshot import ConfigurationSnapshot
d=json.loads(Path(sys.argv[1]).read_text()); descriptor=d.pop('descriptor');d['directory']=Path(d['directory'])
HookRunner().run('pre_install',descriptor,Path(sys.argv[2]),ConfigurationSnapshot(**d),{})
'''
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum):
                heartbeat.unlink(missing_ok=True);pid_file.unlink(missing_ok=True)
                outer = subprocess.Popen([sys.executable, '-c', code, str(payload), str(self.release_dir)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    while not heartbeat.exists() and time.monotonic() < deadline: time.sleep(.02)
                    self.assertTrue(heartbeat.exists(), 'hook did not start')
                    outer.send_signal(signum)
                    outer.communicate(timeout=8)
                    self.assertNotEqual(outer.returncode, 0)
                    self.assert_heartbeat_stopped()
                finally:
                    # The RED reproduction must not leave a live hook on the CI runner.
                    if pid_file.exists():
                        try: os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                        except ProcessLookupError: pass
                    if outer.poll() is None: outer.kill()
                    outer.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
