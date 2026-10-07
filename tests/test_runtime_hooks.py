import copy
import json
from pathlib import Path
import tempfile
import unittest

from deployctl.contract import read_yaml
from deployctl.release import build_release
from test_release import CONFIG, IMAGE
from test_runtime import FakeDocker


class SnapshotDocker(FakeDocker):
    def __init__(self):
        super().__init__()
        self.values, self.calls, self.info = {}, [], None
        self.configuration = None

    def pull(self, directory, project, environment):
        self.calls.append(('pull', read_yaml(directory / 'release.yaml')['version']))
        super().pull(directory, project, environment)

    def up(self, directory, project, environment):
        super().up(directory, project, environment)
        self.calls.append(('up', self.active))
        self.configuration = environment.get('DEPLOYCTL_CONFIGURATION')
        if environment.get('DEPLOYCTL_ENV_SOURCE'):
            self.values = json.loads(Path(environment['DEPLOYCTL_ENV_SOURCE']).read_text())

    def inspect_container(self, *args): return self.info
    def inspect_image_environment(self, image): return ['PATH=/usr/bin', 'IMAGE_DEFAULT=base']


class FakeHookRunner:
    def __init__(self): self.calls, self.fail, self.interrupt = [], None, None
    def check(self): pass
    def run(self, phase, descriptor, directory, snapshot, context):
        self.calls.append((phase, snapshot.values, snapshot.install_params, context))
        if phase == self.interrupt: raise KeyboardInterrupt()
        if phase == self.fail: raise RuntimeError('hook failed')


class TransactionHooksTests(unittest.TestCase):
    def setUp(self):
        from deployctl.runtime import Manager
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.driver, self.hooks = SnapshotDocker(), FakeHookRunner()
        self.manager = Manager(self.base / 'apps', self.base / 'config', self.driver, self.hooks)
        self.folder = self.base / 'config/project-a/production'
        self.home = self.base / 'apps/project-a/production'
        self.folder.mkdir(parents=True)
        (self.folder / 'config.env').write_text('DATABASE_URL=base\nTEXT=server\n')
        (self.folder / 'secrets.env').write_text('TEXT=secret\n')
        (self.folder / 'secrets.env').chmod(0o600)
        self.source = self.base / 'source'
        self.source.mkdir()
        (self.source / 'pre.sh').write_text('true\n')
        (self.source / 'post.sh').write_text('true\n')

    def tearDown(self): self.tmp.cleanup()

    def package(self, version, hooks=False):
        config = copy.deepcopy(CONFIG)
        if hooks:
            config['hooks'] = {'pre_install': {'script': 'pre.sh'}, 'post_install': {'script': 'post.sh'}}
        return build_release(config, IMAGE, version, self.base / 'packages', project_root=self.source)

    def install(self, **kwargs):
        return self.manager.deploy('project-a', 'production', self.package('v1.0.0'), **kwargs)

    def upgrade(self, package, **kwargs):
        return self.manager.deploy('project-a', 'production', package, upgrade=True, **kwargs)

    def state(self): return json.loads((self.home / 'state.json').read_text())

    def test_pre_failure_never_replaces_container(self):
        self.install()
        before = self.state()['current']
        self.driver.calls.clear()
        self.hooks.fail = 'pre_install'
        with self.assertRaisesRegex(RuntimeError, 'existing container unchanged'):
            self.upgrade(self.package('v1.1.0', True), runtime_env={'TEXT': 'new'})
        self.assertFalse(any(call[0] == 'up' for call in self.driver.calls))
        self.assertEqual(self.state()['current'], before)
        self.assertIsNone(self.state()['transaction'])

    def test_post_failure_restores_old_snapshot_and_exits_nonzero(self):
        self.install(runtime_env={'TEXT': 'old override'})
        before = self.state()['current']
        self.hooks.fail = 'post_install'
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.upgrade(self.package('v1.1.0', True), runtime_env={'TEXT': 'failed override'})
        self.assertEqual(self.state()['current'], before)
        self.assertEqual(self.driver.configuration, before['configuration'])
        self.assertEqual(self.driver.values['TEXT'], 'old override')
        self.assertEqual([c[0] for c in self.hooks.calls], ['pre_install', 'post_install'])

    def test_set_without_hooks_rejected_before_pull(self):
        with self.assertRaisesRegex(ValueError, 'hooks'):
            self.install(install_params={'DATA_DIR': '/data'})
        self.assertEqual(self.driver.calls, [])

    def test_env_var_satisfies_required_config_and_persists(self):
        (self.folder / 'config.env').write_text('')
        self.install(runtime_env={'DATABASE_URL': 'runtime', 'TEXT': 'literal $ = '})
        self.upgrade(self.package('v1.1.0', True), install_params={'TEXT': 'hook only'})
        self.assertEqual(self.driver.values['DATABASE_URL'], 'runtime')
        self.assertEqual(self.driver.values['TEXT'], 'literal $ = ')
        self.assertEqual(self.hooks.calls[0][2], {'TEXT': 'hook only'})
        self.assertEqual(self.hooks.calls[0][3]['DEPLOYCTL_PREVIOUS_VERSION'], 'v1.0.0')
        self.assertNotIn('DEPLOYCTL_PARAM_TEXT', self.driver.values)
        self.hooks.calls.clear()
        self.upgrade(self.package('v1.2.0', True))
        self.assertEqual(self.hooks.calls[0][2], {})

    def test_unset_env_restores_server_value(self):
        self.install(runtime_env={'TEXT': 'override'})
        self.upgrade(self.package('v1.1.0'), unset_env=['TEXT'])
        self.assertEqual(self.driver.values['TEXT'], 'secret')
        ref = self.state()['current']
        self.assertEqual(json.loads((self.folder / 'runtime' / ref['configuration'] / 'overrides.json').read_text()), {})

    def test_same_version_config_change_can_roll_back(self):
        archive = self.package('v1.0.0')
        self.manager.deploy('project-a', 'production', archive, runtime_env={'TEXT': 'old'})
        old = self.state()['current']
        self.upgrade(archive, runtime_env={'TEXT': 'new'})
        self.assertEqual(self.state()['previous'], old)
        self.manager.rollback('project-a', 'production')
        self.assertEqual(self.driver.values['TEXT'], 'old')
        self.assertEqual(self.state()['current'], old)

    def test_unchanged_retry_preserves_previous_but_override_layer_change_does_not(self):
        self.install()
        archive = self.package('v1.1.0')
        self.upgrade(archive)
        previous = self.state()['previous']
        self.upgrade(archive)
        self.assertEqual(self.state()['previous'], previous)
        current = self.state()['current']
        self.upgrade(archive, runtime_env={'TEXT': 'secret'})
        self.assertEqual(self.state()['previous'], current)

    def legacy(self):
        self.install()
        state = {'schema_version': 1, 'application': 'project-a', 'environment': 'production',
                 'current': 'v1.0.0', 'previous': None, 'transaction': None,
                 'binding': {'address': '127.0.0.1', 'port': 8080}}
        (self.home / 'state.json').write_text(json.dumps(state))
        self.driver.info = {'image': IMAGE, 'running': True, 'labels': {
            'io.team-deploy.application': 'project-a', 'io.team-deploy.version': 'v1.0.0'},
            'environment': ['APP_VERSION=v1.0.0', 'DATABASE_URL=actual', 'TEXT=actual old',
                            'PATH=/usr/bin', 'IMAGE_DEFAULT=base']}

    def test_legacy_capture_uses_actual_not_edited_server_values(self):
        self.legacy()
        (self.folder / 'config.env').write_text('DATABASE_URL=edited\n')
        self.hooks.fail = 'post_install'
        with self.assertRaises(RuntimeError): self.upgrade(self.package('v1.1.0', True))
        self.assertEqual(self.driver.values['DATABASE_URL'], 'actual')
        self.assertEqual(self.driver.values['TEXT'], 'actual old')
        self.assertFalse(self.state()['current']['legacy'])

    def test_legacy_pre_failure_preserves_reference_of_unchanged_container(self):
        self.legacy()
        self.driver.configuration = None
        self.driver.calls.clear()
        self.hooks.fail = 'pre_install'
        with self.assertRaises(RuntimeError): self.upgrade(self.package('v1.1.0', True))
        self.assertFalse(any(call[0] == 'up' for call in self.driver.calls))
        current = self.state()['current']
        self.assertTrue(current['legacy'])
        self.assertIsNone(current['configuration'])
        self.assertIsNone(self.state()['transaction'])
        self.manager.operate('project-a', 'production', 'restart')

    def test_legacy_missing_or_mismatched_container_refuses_upgrade(self):
        for info in (None, {'image': IMAGE, 'labels': {}, 'environment': []}):
            with self.subTest(info=info is None):
                if not (self.home / 'state.json').exists(): self.legacy()
                self.driver.info = info
                # One immutable package reused across the subcases.
                archive = self.base / 'packages/project-a-v1.1.0.tar.gz'
                if not archive.exists(): archive = self.package('v1.1.0')
                with self.assertRaisesRegex(ValueError, 'legacy'):
                    self.upgrade(archive)
                self.assertEqual(self.state()['schema_version'], 1)

    def test_status_does_not_write_legacy_state(self):
        self.legacy()
        before = (self.home / 'state.json').read_bytes()
        self.manager.operate('project-a', 'production', 'status')
        self.assertEqual((self.home / 'state.json').read_bytes(), before)

    def test_legacy_capture_does_not_require_old_server_file_values(self):
        self.legacy()
        (self.folder / 'config.env').write_text('')
        self.upgrade(self.package('v1.1.0'), runtime_env={'DATABASE_URL': 'new'})
        self.manager.rollback('project-a', 'production')
        self.assertEqual(self.driver.values['DATABASE_URL'], 'actual')

    def test_legacy_pending_first_install_without_binding_can_be_cleaned(self):
        self.legacy()
        state = self.state()
        state.update(current=None, binding=None, transaction={'from': None, 'to': 'v1.0.0'})
        (self.home / 'state.json').write_text(json.dumps(state))
        self.manager.rollback('project-a', 'production')
        self.assertIsNone(self.driver.active)
        self.assertIsNone(self.state()['transaction'])

    def test_interrupted_hook_retains_pending_and_rollback_never_reruns_hooks(self):
        self.install()
        self.hooks.interrupt = 'post_install'
        with self.assertRaises(KeyboardInterrupt): self.upgrade(self.package('v1.1.0', True))
        self.assertIsNotNone(self.state()['transaction'])
        count = len(self.hooks.calls)
        self.manager.rollback('project-a', 'production')
        self.assertEqual(len(self.hooks.calls), count)
        self.assertEqual(self.driver.values['APP_VERSION'], 'v1.0.0')


if __name__ == '__main__': unittest.main()
