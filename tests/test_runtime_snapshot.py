import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deployctl.contract import validate_deployment
from deployctl.release import render_compose
from test_release import CONFIG, IMAGE


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='runtime $ space ')
        self.folder = Path(self.tmp.name) / 'config'
        self.release = {'application': 'project-a', 'version': 'v1.0.0',
                        'image': IMAGE, 'deployment': validate_deployment(CONFIG)}
        self.values = {'TEXT': ' a,b "quoted" $literal #tag = ', 'EMPTY': '', 'APP_VERSION': 'v1.0.0'}

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        from deployctl.runtime_snapshot import create_snapshot
        return create_snapshot(self.folder, 'project-a', 'production', self.release,
                               self.values, {'TEXT': self.values['TEXT']}, {'TEXT': 'hook only'})

    def ref(self, snapshot):
        return {'version': 'v1.0.0', 'configuration': snapshot.id,
                'configuration_sha256': snapshot.sha256}

    def test_snapshot_uses_private_directory_and_nonroot_readable_json(self):
        from deployctl.runtime_snapshot import load_snapshot
        snapshot = self.snapshot()
        self.assertEqual(json.loads((snapshot.directory / '.env.json').read_text()), self.values)
        self.assertIn('TEXT=' + self.values['TEXT'] + '\n',
                      (snapshot.directory / 'effective.env').read_text())
        self.assertEqual(load_snapshot(self.folder, self.ref(snapshot), self.release).values, self.values)
        self.assertEqual(set(p.name for p in snapshot.directory.iterdir()),
                         {'.env.json', 'effective.env', 'overrides.json', '.install-params.json', 'compose.yaml'})
        if os.name != 'nt':
            self.assertEqual(snapshot.directory.stat().st_mode & 0o777, 0o700)
            for path in snapshot.directory.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o644 if path.name == '.env.json' else 0o600)

    def test_runtime_compose_has_only_platform_mount_and_final_env(self):
        from deployctl.runtime_snapshot import render_runtime_compose
        snapshot = self.snapshot()
        app = render_runtime_compose(self.release, snapshot.id)['services']['app']
        self.assertEqual(app['env_file'], [{'path': '${DEPLOYCTL_EFFECTIVE_ENV_FILE}', 'format': 'raw'}])
        self.assertEqual(app['environment'], {'DEPLOYCTL_ENV_FILE': '/run/deployctl/.env.json'})
        self.assertEqual(app['volumes'], [{'type': 'bind', 'source': '${DEPLOYCTL_ENV_SOURCE}',
                         'target': '/run/deployctl/.env.json', 'read_only': True,
                         'bind': {'create_host_path': False}}])
        self.assertEqual(app['labels']['io.team-deploy.configuration'], snapshot.id)
        self.assertNotIn('volumes', render_compose(self.release)['services']['app'])

    def test_changed_json_raw_env_or_compose_rejected(self):
        from deployctl.runtime_snapshot import verify_snapshot, load_snapshot
        for name in ('.env.json', 'effective.env', 'compose.yaml', 'overrides.json', '.install-params.json'):
            with self.subTest(name=name):
                snapshot = self.snapshot()
                with (snapshot.directory / name).open('a') as handle:
                    handle.write('changed')
                with self.assertRaises(ValueError): verify_snapshot(snapshot)
                with self.assertRaises(ValueError): load_snapshot(self.folder, self.ref(snapshot), self.release)

    def test_invalid_id_or_linked_directory_cannot_escape(self):
        from deployctl.runtime_snapshot import load_snapshot
        snapshot = self.snapshot()
        for identifier in ('../outside', 'A' * 32, 'a' * 31, '/tmp/x'):
            ref = dict(self.ref(snapshot), configuration=identifier)
            with self.assertRaises(ValueError): load_snapshot(self.folder, ref, self.release)
        if os.name != 'nt':
            link = self.folder / 'runtime' / ('a' * 32)
            link.symlink_to(snapshot.directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                load_snapshot(self.folder, dict(self.ref(snapshot), configuration='a' * 32), self.release)

    def test_unknown_member_and_noncanonical_compose_rejected(self):
        from deployctl.runtime_snapshot import verify_snapshot, load_snapshot
        snapshot = self.snapshot()
        (snapshot.directory / 'extra').write_text('x')
        with self.assertRaises(ValueError): verify_snapshot(snapshot)
        other = copy.deepcopy(self.release)
        other['image'] = 'ghcr.io/other/app@sha256:' + 'b' * 64
        with self.assertRaises(ValueError): load_snapshot(self.folder, self.ref(self.snapshot()), other)

    def test_host_path_with_spaces_and_dollar_is_data(self):
        from deployctl.runtime import DockerDriver, Manager
        snapshot = self.snapshot()
        manager = Manager(driver=DockerDriver())
        env = manager.docker_environment(self.folder, {'address': '127.0.0.1', 'port': 8080}, snapshot)
        self.assertEqual(env['DEPLOYCTL_ENV_SOURCE'], str(snapshot.directory / '.env.json'))
        with patch.object(manager.driver, '_run', return_value='') as run:
            manager.driver.compose(Path('release'), 'test', env, 'config')
        self.assertIn(str(snapshot.directory / 'compose.yaml'), run.call_args.args[0])

    def test_parent_compose_controls_cannot_select_another_runtime_file(self):
        from deployctl.runtime import Manager
        with patch.dict(os.environ, {'COMPOSE_FILE': 'bad', 'COMPOSE_FOO': 'bad',
                                     'DEPLOYCTL_COMPOSE_FILE': 'bad', 'DEPLOYCTL_ENV_SOURCE': 'bad'}):
            env = Manager().docker_environment(self.folder, {'address': '127.0.0.1', 'port': 8080})
        for key in ('COMPOSE_FILE', 'COMPOSE_FOO', 'DEPLOYCTL_COMPOSE_FILE', 'DEPLOYCTL_ENV_SOURCE'):
            self.assertFalse(key in env, 'inherited control variable was not removed')

    def test_probe_checks_configuration_label_and_inspection_includes_stopped(self):
        from deployctl.runtime import DockerDriver
        driver = DockerDriver()
        info = {'image': IMAGE, 'running': True, 'labels': {
            'io.team-deploy.application': 'project-a', 'io.team-deploy.version': 'v1.0.0',
            'io.team-deploy.configuration': 'wrong'}}
        release = copy.deepcopy(self.release)
        release['deployment']['health']['startup_timeout_seconds'] = 1
        with patch.object(driver, 'inspect_container', return_value=info), patch('deployctl.runtime.http_ready', return_value=True):
            with self.assertRaises(RuntimeError):
                driver.probe(Path('x'), 'p', {'DEPLOYCTL_CONFIGURATION': 'a' * 32}, release,
                             {'address': '127.0.0.1', 'port': 8080})
        with patch.object(driver, 'compose', return_value='container') as compose, \
                patch.object(driver, '_run', return_value=json.dumps(info)):
            driver.inspect_container(Path('x'), 'p', {})
        self.assertIn('--all', compose.call_args.args)


if __name__ == '__main__':
    unittest.main()
