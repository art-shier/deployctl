import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deployctl.release import build_release
from test_release import CONFIG, IMAGE


class FakeDocker:
    """Docker is external/unavailable locally; filesystem and transaction are real."""
    def __init__(self):
        self.active = None
        self.fail_versions = set()
        self.fail_up = set()
        self.fail_pull = False
        self.interrupt_versions = set()

    def check(self):
        pass

    def pull(self, directory, project, environment):
        if self.fail_pull:
            raise RuntimeError('registry unreachable')

    def up(self, directory, project, environment):
        import yaml
        version = yaml.safe_load((directory / 'release.yaml').read_text())['version']
        if version in self.interrupt_versions:
            raise KeyboardInterrupt()
        if version in self.fail_up:
            raise RuntimeError('container failed to start')
        self.active = version

    def probe(self, directory, project, environment, release, binding):
        if self.active in self.fail_versions:
            raise RuntimeError('readiness failed')

    def down(self, directory, project, environment):
        self.active = None

    def operate(self, directory, project, environment, action, tail=100):
        return 'application startup diagnostic\n'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.driver = FakeDocker()

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self):
        from deployctl.runtime import Manager
        return Manager(self.base / 'apps', self.base / 'config', self.driver)

    def package(self, version, config=None):
        return build_release(config or CONFIG, IMAGE, version, self.base / 'packages')

    def configure(self, text='DATABASE_URL=postgres://test\n'):
        folder = self.base / 'config/project-a/production'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'config.env').write_text(text)
        (folder / 'secrets.env').write_text('APP_SECRET=literal$secret#value\n')
        (folder / 'secrets.env').chmod(0o600)
        return folder

    def state(self):
        return json.loads((self.base / 'apps/project-a/production/state.json').read_text())

    def test_install_commits_version_only_after_health_and_keeps_secrets(self):
        manager = self.manager()
        folder = self.configure()
        secret = (folder / 'secrets.env').read_bytes()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertIsNone(self.state()['transaction'])
        self.assertEqual((folder / 'secrets.env').read_bytes(), secret)
        self.assertEqual(self.driver.active, 'v1.0.0')

    def test_missing_config_never_starts_container(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
            manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.assertIsNone(self.driver.active)

    def test_failed_upgrade_restores_old_version_and_still_fails(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.driver.fail_versions.add('v1.1.0')
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertIsNone(self.state()['transaction'])
        self.assertEqual(self.driver.active, 'v1.0.0')

    def test_pull_failure_does_not_change_running_service(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.driver.fail_pull = True
        with self.assertRaises(RuntimeError):
            manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertEqual(self.driver.active, 'v1.0.0')
        self.assertIsNone(self.state()['transaction'])

    def test_failed_restore_leaves_pending_transaction_and_blocks_upgrade(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.driver.fail_versions.update({'v1.1.0', 'v1.0.0'})
        with self.assertRaisesRegex(RuntimeError, 'recovery failed'):
            manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertIsNotNone(self.state()['transaction'])
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            manager.deploy('project-a', 'production', self.package('v1.2.0'), upgrade=True)
        self.driver.fail_versions.clear()
        manager.rollback('project-a', 'production')
        self.assertIsNone(self.state()['transaction'])
        self.assertEqual(self.driver.active, 'v1.0.0')

    def test_manual_rollback_restores_previous_and_preserves_binding(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'), port=19001)
        manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        manager.rollback('project-a', 'production')
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertEqual(self.state()['previous'], 'v1.1.0')
        self.assertEqual(self.state()['binding']['port'], 19001)

    def test_wrong_application_cannot_touch_other_project(self):
        manager = self.manager()
        self.configure()
        config = dict(CONFIG, application='other-app')
        with self.assertRaisesRegex(ValueError, 'application'):
            manager.deploy('project-a', 'production', self.package('v1.0.0', config))
        self.assertIsNone(self.driver.active)

    def test_install_failure_stops_candidate_without_success_state(self):
        manager = self.manager()
        self.configure()
        self.driver.fail_versions.add('v1.0.0')
        with self.assertRaises(RuntimeError):
            manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.assertIsNone(self.state()['current'])
        self.assertIsNone(self.state()['transaction'])
        self.assertIsNone(self.driver.active)

    def test_first_install_failure_preserves_reason_and_logs_after_cleanup(self):
        manager = self.manager()
        self.configure()
        self.driver.fail_versions.add('v1.0.0')
        with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
            manager.deploy('project-a', 'production', self.package('v1.0.0'))
        logs = manager.operate('project-a', 'production', 'logs')
        self.assertIn('application startup diagnostic', logs)
        self.assertIn('readiness failed', logs)

    def test_interrupted_upgrade_requires_explicit_recovery(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.driver.interrupt_versions.add('v1.1.0')
        with self.assertRaises(KeyboardInterrupt):
            manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        self.assertEqual(self.state()['current'], 'v1.0.0')
        self.assertIsNotNone(self.state()['transaction'])
        manager.rollback('project-a', 'production')
        self.assertIsNone(self.state()['transaction'])
        self.assertEqual(self.driver.active, 'v1.0.0')

    def test_diagnostic_write_failure_cannot_prevent_old_version_recovery(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        self.driver.fail_versions.add('v1.1.0')
        with patch.object(manager, 'capture_failure', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'restored'):
                manager.deploy('project-a', 'production', self.package('v1.1.0'), upgrade=True)
        self.assertEqual(self.driver.active, 'v1.0.0')
        self.assertIsNone(self.state()['transaction'])

    def test_same_version_with_different_content_is_rejected(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        config = copy.deepcopy(CONFIG)
        config['container']['port'] = 8090
        package = build_release(config, IMAGE, 'v1.0.0', self.base / 'other-package')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            manager.deploy('project-a', 'production', package, upgrade=True)
        self.assertEqual(self.driver.active, 'v1.0.0')

    def test_different_services_do_not_collide_in_compose_project_name(self):
        from deployctl.runtime import project_name
        self.assertNotEqual(project_name('project-a', 'b'), project_name('project', 'a-b'))

    def test_same_version_retry_does_not_erase_previous_rollback_target(self):
        manager = self.manager()
        self.configure()
        manager.deploy('project-a', 'production', self.package('v1.0.0'))
        latest = self.package('v1.1.0')
        manager.deploy('project-a', 'production', latest, upgrade=True)
        manager.deploy('project-a', 'production', latest, upgrade=True)
        self.assertEqual(self.state()['previous'], 'v1.0.0')
        manager.rollback('project-a', 'production')
        self.assertEqual(self.driver.active, 'v1.0.0')


if __name__ == '__main__':
    unittest.main()
