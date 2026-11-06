import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from deployctl import node_agent as agent


NODE = 'a' * 32
TASK = 'b' * 32
INSTANCE = 'c' * 32


def task(status='leased'):
    return {'id': TASK, 'node_id': NODE, 'instance_id': INSTANCE,
            'project': 'notes', 'environment': 'prod', 'action': 'install',
            'status': status, 'version': 'v1.0.0',
            'resolution': {'schema_version': 1, 'minimum_client_version': '1.7.0',
                'project': 'notes', 'environment': 'prod',
                'release': {'id': 'd' * 32, 'version': 'v1.0.0',
                    'image': 'registry.example/notes@sha256:' + 'e' * 64,
                    'package_path': f'/api/v1/node/tasks/{TASK}/package', 'sha256': 'f' * 64},
                'configuration': {'id': '1' * 32, 'revision': 2,
                    'runtime_env': {'SECRET': 'never-journal-this'},
                    'install_params': {}, 'deployment_defaults': {}}}}


class FakeClient:
    server = 'https://ctl.example'

    def __init__(self):
        self.started = 0
        self.reports = []
        self.fail_start = False
        self.fail_report = False

    def json(self, method, path, body=None):
        if path.endswith('/start'):
            self.started += 1
            if self.fail_start:
                raise RuntimeError('connection lost')
            return task('running')
        if path.endswith('/report'):
            if self.fail_report:
                raise RuntimeError('connection lost')
            self.reports.append(body)
            return {'status': body['status']}
        if path.endswith('/heartbeat'):
            return {'enabled': True, 'instances': []}
        if path.endswith('/poll'):
            return {'task': None}
        raise AssertionError(path)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'agent'
        self.client = FakeClient()
        self.executor = Mock()
        self.executor.execute.return_value = {'id': INSTANCE, 'project': 'notes', 'environment': 'prod',
            'actual_version': 'v1.0.0', 'health': 'healthy', 'binding': None, 'pending_transaction': False}
        self.executor.observe.return_value = self.executor.execute.return_value
        self.worker = agent.Agent(self.home, NODE, self.client, self.executor)

    def test_rejects_foreign_task_and_package_path_before_any_execution(self):
        for key, value in [('node_id', '9' * 32), ('id', '../bad'), ('action', 'shell')]:
            candidate = task()
            candidate[key] = value
            with self.assertRaises(ValueError):
                self.worker.execute_task(candidate)
        candidate = task()
        candidate['resolution']['release']['package_path'] = '/api/v1/projects/notes/artifacts/' + 'd' * 32
        with self.assertRaises(ValueError):
            self.worker.execute_task(candidate)
        self.executor.execute.assert_not_called()

    def test_success_is_journaled_without_secrets_and_replayed_without_execution(self):
        self.worker.execute_task(task())
        self.assertEqual(self.executor.execute.call_count, 1)
        self.worker.flush_reports()
        again = agent.Agent(self.home, NODE, self.client, self.executor)
        again.execute_task(task('running'))
        again.flush_reports()
        self.assertEqual(self.executor.execute.call_count, 1)
        self.assertEqual(self.client.reports[-1]['status'], 'succeeded')
        self.assertNotIn('never-journal-this', ''.join(p.read_text() for p in self.home.rglob('*.json')))

    def test_lost_report_is_retried_without_executing_again(self):
        self.worker.execute_task(task())
        self.client.fail_report = True
        self.worker.flush_reports()
        self.client.fail_report = False
        restarted = agent.Agent(self.home, NODE, self.client, self.executor)
        restarted.flush_reports()
        self.assertEqual(self.client.reports[-1]['status'], 'succeeded')
        self.assertEqual(self.executor.execute.call_count, 1)

    def test_lost_start_response_does_not_execute_and_requires_attention(self):
        self.client.fail_start = True
        self.worker.execute_task(task())
        self.executor.execute.assert_not_called()
        restarted = agent.Agent(self.home, NODE, self.client, self.executor)
        restarted.execute_task(task('running'))
        restarted.flush_reports()
        self.executor.execute.assert_not_called()
        self.assertEqual(self.client.reports[-1]['status'], 'needs_attention')

    def test_running_without_local_record_never_executes(self):
        self.worker.execute_task(task('running'))
        self.worker.flush_reports()
        self.executor.execute.assert_not_called()
        self.assertEqual(self.client.reports[-1]['status'], 'needs_attention')

    def test_starting_journal_survives_crash_without_replay(self):
        self.executor.execute.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.worker.execute_task(task())
        restarted = agent.Agent(self.home, NODE, self.client, self.executor)
        restarted.flush_reports()
        self.assertEqual(self.client.reports[-1]['status'], 'needs_attention')
        self.assertEqual(self.executor.execute.call_count, 1)

    def test_failure_report_does_not_contain_exception_text(self):
        self.executor.execute.side_effect = RuntimeError('password=super-secret')
        self.worker.execute_task(task())
        self.worker.flush_reports()
        self.assertEqual(self.client.reports[-1]['status'], 'failed')
        self.assertNotIn('super-secret', json.dumps(self.client.reports))

    def test_changed_task_payload_cannot_reuse_completed_id(self):
        self.worker.execute_task(task())
        altered = task()
        altered['environment'] = 'test'
        altered['resolution']['environment'] = 'test'
        with self.assertRaises(ValueError):
            self.worker.execute_task(altered)
        self.assertEqual(self.executor.execute.call_count, 1)

    def test_runtime_uses_frozen_resolution_without_resolve(self):
        manager = Mock()
        manager.deploy.return_value = {}
        runtime = agent.RuntimeExecutor(manager, self.client)
        candidate = task()
        candidate.update(host_port=8088, bind_address='0.0.0.0')
        with patch.object(runtime, 'observe', return_value={'health': 'healthy'}), \
             patch.object(runtime, 'registry_config') as registry, \
             patch.object(self.client, 'download_release', create=True, return_value=Path('package.tgz')):
            registry.return_value.__enter__.return_value = '/private/docker'
            runtime.execute(candidate, lambda stage: None)
        args = manager.deploy.call_args.kwargs
        self.assertEqual(args['managed_runtime'], {'SECRET': 'never-journal-this'})
        self.assertEqual(args['management_source']['revision_id'], '1' * 32)
        self.assertEqual(args['port'], 8088)
        self.assertEqual(args['bind'], '0.0.0.0')
        self.assertFalse(args['upgrade'])
        self.assertIsNone(manager.driver.docker_config)

    def test_deployment_waits_for_its_own_observer_instead_of_failing_file_lock(self):
        from deployctl.runtime import Manager
        manager = Manager(self.home / 'apps', self.home / 'config', driver=Mock())
        executor = agent.RuntimeExecutor(manager, self.client)
        entered, release, rolled = threading.Event(), threading.Event(), threading.Event()
        original_state = manager.state
        def slow_state(home, app, env):
            entered.set()
            if not release.wait(3):
                raise AssertionError('observer not released')
            return original_state(home, app, env)
        errors = []
        def rollback(app, env):
            home, _ = manager.paths(app, env)
            with agent.service_lock(home / '.lock'):
                rolled.set()
        candidate = task()
        candidate.update(action='rollback', resolution=None, version='')
        def execute():
            try:
                executor.execute(candidate, lambda _: None)
            except Exception as error:
                errors.append(error)
        with patch.object(manager, 'state', side_effect=slow_state), patch.object(manager, 'rollback', side_effect=rollback):
            observer = threading.Thread(target=executor.observe, args=({'id': INSTANCE, 'project': 'notes', 'environment': 'prod'},))
            observer.start()
            self.assertTrue(entered.wait(2))
            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.assertFalse(rolled.wait(.1))
            finally:
                release.set()
                observer.join(3)
                worker.join(3)
        self.assertEqual(errors, [])
        self.assertTrue(rolled.is_set())

    def test_unknown_observation_on_docker_error(self):
        manager = Mock()
        manager.paths.side_effect = RuntimeError('broken')
        runtime = agent.RuntimeExecutor(manager, self.client)
        observed = runtime.observe({'id': INSTANCE, 'project': 'notes', 'environment': 'prod'})
        self.assertEqual(observed['health'], 'unknown')
        self.assertNotIn('broken', json.dumps(observed))

    def test_heartbeat_reports_unknown_failure_but_skips_busy_instance(self):
        self.worker.instances = [{'id': INSTANCE, 'project': 'notes', 'environment': 'prod'}]
        self.executor.observe.return_value = dict(self.executor.execute.return_value, health='unknown', actual_version='')
        self.executor.docker_available.return_value = False
        with patch.object(self.client, 'json', return_value={'enabled': True, 'instances': self.worker.instances}) as send:
            self.worker.heartbeat()
            self.assertEqual(send.call_args.args[2]['instances'][0]['health'], 'unknown')
            self.worker.worker = Mock()
            self.worker.worker.is_alive.return_value = True
            self.worker.active_instance = INSTANCE
            self.worker.heartbeat()
            self.assertEqual(send.call_args.args[2]['instances'], [])

    def test_heartbeat_does_not_wait_for_docker_sampling(self):
        self.executor.observe.side_effect = AssertionError('sampling blocked the heartbeat')
        self.executor.docker_available.side_effect = AssertionError('docker check blocked the heartbeat')
        self.worker.heartbeat(sample=False)

    def test_terminal_report_invalidates_old_cached_observation(self):
        self.worker.instances = [{'id': INSTANCE, 'project': 'notes', 'environment': 'prod'}]
        self.executor.observe.return_value = dict(self.executor.execute.return_value, actual_version='v0.9.0')
        self.worker.sample_observations()
        self.worker.execute_task(task())
        self.worker.flush_reports()
        with patch.object(self.client, 'json', return_value={'enabled': True, 'instances': []}) as send:
            self.worker.heartbeat(sample=False)
        self.assertEqual(send.call_args.args[2]['instances'], [])

    def test_busy_failure_observation_does_not_leak_internal_marker(self):
        self.executor.execute.side_effect = RuntimeError('another deployment operation is running')
        self.executor.observe.return_value = dict(self.executor.execute.return_value, health='unknown', _busy=True)
        self.worker.execute_task(task())
        self.worker.flush_reports()
        self.assertEqual(self.client.reports[-1]['status'], 'failed')
        self.assertNotIn('_busy', json.dumps(self.client.reports))

    def test_node_heartbeat_still_sends_when_result_upload_holds_observation_lock(self):
        self.worker.observation_lock.acquire()
        try:
            with patch.object(self.client, 'json', return_value={'enabled': True, 'instances': []}) as send:
                self.worker.heartbeat(sample=False)
            self.assertEqual(send.call_args.args[2]['instances'], [])
        finally:
            self.worker.observation_lock.release()

    def test_inflight_sample_finishes_before_terminal_report(self):
        self.worker.execute_task(task())
        heartbeat_entered, release_heartbeat, report_entered = threading.Event(), threading.Event(), threading.Event()
        original = self.client.json
        def send(method, path, body=None):
            if path.endswith('/heartbeat'):
                heartbeat_entered.set()
                if not release_heartbeat.wait(3):
                    raise AssertionError('heartbeat was never released')
                return {'enabled': True, 'instances': []}
            if path.endswith('/report'):
                report_entered.set()
            return original(method, path, body)
        with patch.object(self.client, 'json', side_effect=send):
            heartbeat = threading.Thread(target=self.worker.heartbeat, kwargs={'sample': False})
            heartbeat.start()
            self.assertTrue(heartbeat_entered.wait(2))
            report = threading.Thread(target=self.worker.flush_reports)
            report.start()
            try:
                self.assertFalse(report_entered.wait(.1))
            finally:
                release_heartbeat.set()
                heartbeat.join(3)
                report.join(3)
            self.assertTrue(report_entered.is_set())

    def test_sample_started_before_deploy_cannot_publish_after_completion(self):
        self.worker.instances = [{'id': INSTANCE, 'project': 'notes', 'environment': 'prod'}]
        def stale(instance):
            self.worker.invalidate_sample(INSTANCE)
            return dict(self.executor.execute.return_value, actual_version='v0.9.0')
        self.executor.observe.side_effect = stale
        self.worker.sample_observations()
        self.assertEqual(self.worker.samples, {})

    def test_corrupt_journal_blocks_execution(self):
        (self.home / 'tasks' / (TASK + '.json')).write_text('{broken')
        with self.assertRaises(ValueError):
            self.worker.execute_task(task())
        self.executor.execute.assert_not_called()

    def test_systemd_unit_quotes_paths_and_never_embeds_token(self):
        unit = agent.service_unit('/usr/local/bin/ctl', '/var/lib/ctl-agent', '/opt/deployments', '/etc/deployctl')
        self.assertIn('agent run', unit)
        self.assertIn('Restart=always', unit)
        self.assertIn('KillMode=mixed', unit)
        self.assertNotIn('--token', unit)
        with self.assertRaises(ValueError):
            agent.service_unit('/usr/bin/ctl\nExecStart=bad', '/tmp/a', '/opt/a', '/etc/a')

    def test_cli_agent_commands_parse(self):
        from deployctl.cli import parser
        args = parser().parse_args(['agent', 'enroll', '--server', 'https://ctl.example'])
        self.assertEqual(args.agent_command, 'enroll')
        args = parser().parse_args(['agent', 'run'])
        self.assertEqual(args.home, '/var/lib/ctl-agent')

    def test_enrollment_saves_only_private_node_identity_and_preserves_journals(self):
        self.worker.execute_task(task())
        credential = {'node_id': NODE, 'token': 'ctl_' + 'x' * 40}
        with patch('deployctl.node_agent.PlatformClient.json', return_value=credential) as request:
            node = agent.enroll('https://ctl.example', 'ctl_' + 'y' * 40, self.home)
        self.assertEqual(request.call_args.args[2]['name'], agent.platform.node())
        self.assertEqual(node, NODE)
        saved = agent.read_json(self.home / 'node.json')
        self.assertEqual(saved['token'], credential['token'])
        self.assertTrue((self.home / 'tasks' / (TASK + '.json')).exists())
        self.assertNotIn('y' * 40, (self.home / 'node.json').read_text())

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership/modes')
    def test_agent_private_files_and_links_rejected(self):
        path = self.home / 'private.json'
        agent.write_json(path, {'token': 'fixture'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            agent.read_private(path)
        link = self.home / 'linked'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            agent.write_json(link, {'value': 'must-not-write'})


if __name__ == '__main__':
    unittest.main()
