import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from deployctl.contract import validate_deployment
from deployctl.release import build_release, unpack_release
from deployctl.runtime import Manager
from deployctl.runtime_snapshot import verify_snapshot
from test_release import CONFIG, IMAGE
from test_runtime_hooks import SnapshotDocker, FakeHookRunner


class ConfigWritingHook(FakeHookRunner):
    def __init__(self, generated='generated', invalid=False):
        super().__init__()
        self.generated, self.invalid, self.initial = generated, invalid, None

    def run(self, phase, descriptor, directory, snapshot, context):
        super().run(phase, descriptor, directory, snapshot, context)
        if phase == 'pre_install':
            self.initial = snapshot
            folder = Path(context['DEPLOYCTL_CONFIG_DIR'])
            (folder / 'config.env').write_text('TEXT=new source\n')
            (folder / 'secrets.env').write_text(
                'BASH_ENV=sentinel-secret\n' if self.invalid else
                ('DATABASE_URL=' + self.generated + '\n' if self.generated else ''))
            (folder / 'secrets.env').chmod(0o600)


class PreInstallRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'pre.sh').write_text('true\n')
        self.driver = SnapshotDocker()
        self.hooks = ConfigWritingHook()
        self.manager = Manager(self.root / 'apps', self.root / 'config', self.driver, self.hooks)
        self.folder = self.root / 'config/project-a/prod'
        self.home = self.root / 'apps/project-a/prod'

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, refresh=True):
        result = copy.deepcopy(CONFIG)
        result['hooks'] = {'pre_install': {'script': 'pre.sh'}, 'post_install': {'script': 'pre.sh'}}
        if refresh is not None:
            result['hooks']['pre_install']['refresh_config'] = refresh
        return result

    def package(self, version='v1.0.0', refresh=True):
        try:
            return build_release(self.config(refresh), IMAGE, version, self.root / 'packages', project_root=self.source)
        except ValueError as error:
            self.fail('New pre-install configuration refresh contract not accepted: ' + str(error))

    def deploy_success(self, package, **kwargs):
        try:
            return self.manager.deploy('project-a', 'prod', package, **kwargs)
        except (ValueError, RuntimeError) as error:
            self.fail('Configuration generation did not reach successful deployment: ' + str(error))

    def test_refresh_package_requires_new_cli_and_preserves_flag(self):
        release = unpack_release(self.package(), self.root / 'unpacked')
        self.assertEqual(release['minimum_deployctl_version'], '1.6.0')
        self.assertTrue(release['hooks']['pre_install']['refresh_config'])

    def test_flag_is_boolean_and_pre_only(self):
        for bad in ('true', 1, None):
            config = self.config()
            config['hooks']['pre_install']['refresh_config'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_deployment(config)
        config = self.config()
        config['hooks']['post_install']['refresh_config'] = True
        with self.assertRaises(ValueError):
            validate_deployment(config)

    def test_first_install_uses_generated_values_in_new_immutable_snapshot(self):
        state = self.deploy_success(self.package(), install_params={'TOKEN_FILE': '/private/token'})
        self.assertEqual(self.driver.values['DATABASE_URL'], 'generated')
        self.assertNotIn('DATABASE_URL', self.hooks.initial.values)
        verify_snapshot(self.hooks.initial)
        self.assertNotEqual(state['current']['configuration'], self.hooks.initial.id)
        self.assertEqual(self.hooks.calls[-1][1]['DATABASE_URL'], 'generated')
        self.assertEqual(self.hooks.calls[-1][2], {'TOKEN_FILE': '/private/token'})
        self.assertEqual(self.hooks.calls[-1][3]['DEPLOYCTL_IMAGE'], IMAGE)

    def test_override_precedence_and_rotation_with_same_version_rollback(self):
        self.folder.mkdir(parents=True)
        (self.folder / 'config.env').write_text('DATABASE_URL=old\n')
        package = self.package()
        first = self.deploy_success(package, runtime_env={'TEXT': 'literal $ # = override'})
        self.hooks.generated = 'rotated'
        second = self.deploy_success(package, upgrade=True)
        self.assertEqual(self.driver.values['DATABASE_URL'], 'rotated')
        self.assertEqual(self.driver.values['TEXT'], 'literal $ # = override')
        self.assertEqual(second['previous'], first['current'])
        self.hooks.calls.clear()
        self.manager.rollback('project-a', 'prod')
        self.assertEqual(self.driver.values['DATABASE_URL'], 'generated')
        self.assertEqual(self.hooks.calls, [])

    def test_missing_generated_config_or_controls_never_start_container(self):
        for invalid, value in ((False, ''), (True, 'ignored')):
            self.hooks.invalid, self.hooks.generated = invalid, value
            package = self.package('v1.0.' + ('1' if invalid else '0'))
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError) as caught:
                self.manager.deploy('project-a', 'prod', package)
            self.assertNotIn('sentinel-secret', str(caught.exception))
            self.assertFalse(any(call[0] == 'up' for call in self.driver.calls))
            self.assertIsNone(json.loads((self.home / 'state.json').read_text())['transaction'])

    def test_post_failure_restores_true_old_snapshot(self):
        first = self.deploy_success(self.package())
        self.hooks.generated = 'failed rotation'
        self.hooks.fail = 'post_install'
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.manager.deploy('project-a', 'prod', self.package('v1.0.1'), upgrade=True)
        state = json.loads((self.home / 'state.json').read_text())
        self.assertEqual(state['current'], first['current'])
        self.assertEqual(self.driver.values['DATABASE_URL'], 'generated')
        self.assertIsNone(state['transaction'])

    def test_unflagged_or_disabled_pre_retains_required_validation_before_hooks(self):
        for index, refresh in enumerate((None, False)):
            with self.subTest(refresh=refresh), self.assertRaisesRegex(ValueError, 'Missing required'):
                self.manager.deploy('project-a', 'prod', self.package('v1.0.' + str(index), refresh))
            self.assertEqual(self.hooks.calls, [])

    @unittest.skipIf(os.name == 'nt', 'real hooks require Linux Bash')
    def test_real_bash_generates_config_without_mutating_snapshot(self):
        from deployctl.hooks import HookRunner
        self.manager.hook_runner = HookRunner()
        (self.source / 'pre.sh').write_text(
            '#!/bin/bash\nset -euo pipefail\n'
            'if [[ $DEPLOYCTL_ACTION == install && ! -s $DEPLOYCTL_CONFIG_DIR/secrets.env ]]; then\n'
            'printf "DATABASE_URL=real-bash\\n" > "$DEPLOYCTL_CONFIG_DIR/secrets.env"\n'
            'chmod 600 "$DEPLOYCTL_CONFIG_DIR/secrets.env"\nfi\n')
        state = self.deploy_success(self.package())
        self.assertEqual(self.driver.values['DATABASE_URL'], 'real-bash')
        self.assertIsNone(state['transaction'])


if __name__ == '__main__':
    unittest.main()
