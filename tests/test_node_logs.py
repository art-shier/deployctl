import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deployctl import node_logs


class NodeLogsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = node_logs.NodeLogs(self.temp.name)
        self.task = dict(id='a' * 32, project='notes', environment='prod')
        self.request = dict(project='notes', environment='prod', source='deployment', task_id='a' * 32, tail=200)

    def test_events_are_jsonl_and_query_filters(self):
        self.logs.event(self.task, 'preparing', 'Preparing release')
        self.logs.event(self.task, 'failed', 'Deployment failed', 'error')
        result = self.logs.query(dict(self.request, search='failed'))
        row = json.loads(result['text'])
        self.assertEqual(row['level'], 'error')
        self.assertEqual(row['task_id'], self.task['id'])
        self.assertIn('timestamp', row)
        self.assertFalse(result['truncated'])

    def test_query_tail_and_byte_cap(self):
        self.logs.event(self.task, 'preparing', 'one')
        self.logs.event(self.task, 'preparing', 'two')
        result = self.logs.query(dict(self.request, tail=1))
        self.assertEqual(json.loads(result['text'])['message'], 'two')
        self.assertTrue(result['truncated'])
        text, truncated = node_logs.select_text('x' * 300000, {}, 262144)
        self.assertLessEqual(len(text.encode()), 262144)
        self.assertTrue(truncated)

    def test_rejects_traversal_and_symlinks(self):
        for field in ('project', 'environment', 'task_id'):
            with self.assertRaises(ValueError):
                self.logs.query(dict(self.request, **{field: '../escape'}))
        if __import__('os').name == 'nt':
            return  # Creating symlinks requires Windows developer mode/admin.
        folder = Path(self.temp.name) / 'notes' / 'prod' / 'logs'
        folder.parent.mkdir(parents=True)
        folder.symlink_to(self.temp.name, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.logs.query(self.request)

    def test_archives_survive_container_replacement(self):
        container = {'id': 'b' * 64, 'version': 'v1'}
        with patch.object(self.logs, 'containers', return_value=[container]), patch.object(self.logs, 'runtime_text', return_value=('old version output\n', False)):
            self.logs.archive('notes', 'prod')
        listing = self.logs.query(dict(self.request, source='archives'))
        source = listing['sources'][0]
        self.assertEqual(source['version'], 'v1')
        result = self.logs.query(dict(self.request, source='archive', archive_id=source['id']))
        self.assertEqual(result['text'], 'old version output\n')

    def test_retention_and_missing(self):
        for i in range(105):
            self.logs.event(dict(self.task, id=f'{i:032x}'), 'preparing', 'hello')
        folder = Path(self.temp.name) / 'notes' / 'prod' / 'logs'
        self.assertLessEqual(len(list(folder.glob('task-*.jsonl'))), 100)
        result = self.logs.query(self.request)
        self.assertEqual(result['error'], 'log_unavailable')

    def test_invalid_filters(self):
        for extra in ({'tail': 0}, {'tail': True}, {'tail': 2001}, {'since': 'bad'}, {'source': 'shell'}):
            with self.assertRaises(ValueError):
                self.logs.query(dict(self.request, **extra))

    def test_agent_queries_while_deployment_lock_is_held(self):
        from deployctl.node_agent import Agent, RuntimeExecutor
        from deployctl.runtime import Manager
        import threading
        from test_node_agent import FakeClient, NODE
        from unittest.mock import Mock
        client = FakeClient()
        executor = RuntimeExecutor(Manager(Path(self.temp.name)/'runtime', Path(self.temp.name)/'config'), client)
        worker = Agent(Path(self.temp.name) / 'agent', NODE, client, executor)
        worker.logs_supported = True
        worker.logs.event(self.task, 'deploying', 'Starting container')
        received = []
        def request(method, path, body=None):
            if path.endswith('/poll'):
                return {'query': dict(self.request, id='c' * 32)}
            received.append(body)
            return {}
        worker.client.json = request
        with executor.runtime_lock:
            thread = threading.Thread(target=worker.poll_logs, daemon=True)
            thread.start()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive(), 'query blocked on deployment lock')
        self.assertIn('Starting container', received[0]['text'])

    def test_bounded_subprocess_preserves_stderr_and_suffix(self):
        import sys
        text, truncated = node_logs.bounded_command([sys.executable, '-c',
            'import sys; sys.stdout.write("x"*500000); sys.stdout.flush(); sys.stderr.write("failure")'], 1024)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text.encode()), 1024)
        self.assertTrue(text.endswith('failure'))

    def test_corrupt_archive_does_not_hide_valid_history(self):
        with patch.object(self.logs, 'containers', return_value=[{'id':'b'*64,'version':'v1'}]), patch.object(self.logs, 'runtime_text', return_value=('good',False)):
            self.logs.archive('notes','prod')
        folder=Path(self.temp.name)/'notes'/'prod'/'logs'
        (folder/('archive-'+'c'*32+'.json')).write_text('{')
        result=self.logs.query(dict(self.request,source='archives'))
        self.assertEqual(result['error'],'')
        self.assertEqual(len(result['sources']),1)

    def test_stored_archive_is_bounded_after_utf8_encoding(self):
        with patch.object(node_logs,'ARCHIVE_BYTES',8192), patch.object(self.logs,'containers',return_value=[{'id':'b'*64,'version':'v1'}]), patch.object(self.logs,'runtime_text',return_value=('\ufffd\x00'*8192,False)):
            self.logs.archive('notes','prod')
            folder=Path(self.temp.name)/'notes'/'prod'/'logs'
            self.assertLessEqual(sum(p.stat().st_size for p in folder.glob('archive-*')),8192)
            listing=self.logs.query(dict(self.request,source='archives'))
            self.assertTrue(listing['sources'][0]['truncated'])

    def test_archive_listing_does_not_read_bulk_text_under_event_lock(self):
        with patch.object(self.logs,'containers',return_value=[{'id':'b'*64,'version':'v1'}]), patch.object(self.logs,'runtime_text',return_value=('hello',False)):
            self.logs.archive('notes','prod')
        read=self.logs.read
        def checked(path,limit):
            self.assertFalse(node_logs._LOCK._is_owned())
            self.assertLessEqual(limit,4096)
            return read(path,limit)
        with patch.object(self.logs,'read',side_effect=checked):
            self.assertEqual(len(self.logs.query(dict(self.request,source='archives'))['sources']),1)

    def test_full_task_file_reports_truncation(self):
        with patch.object(node_logs,'TASK_BYTES',1024):
            for _ in range(20):
                self.logs.event(self.task,'preparing','x'*100)
            result=self.logs.query(self.request)
            self.assertTrue(result['truncated'])
            path=Path(self.temp.name)/'notes'/'prod'/'logs'/('task-'+self.task['id']+'.jsonl')
            self.assertLessEqual(path.stat().st_size,1024)

    def test_atomic_archive_failure_leaves_existing_history_readable(self):
        import os
        with patch.object(self.logs,'containers',return_value=[{'id':'b'*64,'version':'v1'}]), patch.object(self.logs,'runtime_text',return_value=('good',False)):
            self.logs.archive('notes','prod')
            replace=os.replace
            def fail_metadata(src,dst):
                if str(dst).endswith('.json'):
                    raise OSError('disk full')
                return replace(src,dst)
            with patch.object(node_logs.os,'replace',side_effect=fail_metadata), self.assertRaises(OSError):
                self.logs.archive('notes','prod')
        self.assertEqual(len(self.logs.query(dict(self.request,source='archives'))['sources']),1)
        folder=Path(self.temp.name)/'notes'/'prod'/'logs'
        self.assertEqual(len(list(folder.glob('archive-*.log'))),1)

class RuntimeArchiveIntegrationTest(unittest.TestCase):
    def test_upgrade_failure_recovery_and_rollback_archive_before_replacement(self):
        from test_runtime import RuntimeTests, FakeDocker
        from deployctl.runtime import DockerDriver
        fixture=RuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        class Driver(FakeDocker,DockerDriver):
            def restore_pull(self,*args):
                pass
        fixture.driver=Driver()
        fixture.configure()
        manager=fixture.manager()
        logs=node_logs.NodeLogs(manager.root)
        def containers(*args):
            return [{'id':'b'*64,'version':fixture.driver.active}] if fixture.driver.active else []
        def output(*args):
            return (fixture.driver.active+' output',False)
        with patch.object(node_logs.NodeLogs,'containers',side_effect=containers),patch.object(node_logs.NodeLogs,'runtime_text',side_effect=output):
            manager.deploy('project-a','production',fixture.package('v1.0.0'))
            manager.deploy('project-a','production',fixture.package('v1.1.0'),upgrade=True)
            manager.rollback('project-a','production')
            fixture.driver.fail_versions.add('v1.2.0')
            with self.assertRaises(RuntimeError):
                manager.deploy('project-a','production',fixture.package('v1.2.0'),upgrade=True)
        query=dict(project='project-a',environment='production',source='archives')
        result=logs.query(query)
        versions=[item['version'] for item in result['sources']]
        self.assertEqual(versions.count('v1.0.0'),2)
        self.assertIn('v1.1.0',versions)
        self.assertIn('v1.2.0',versions)
        for item in result['sources']:
            value=logs.query(dict(query,source='archive',archive_id=item['id']))
            self.assertEqual(value['text'],item['version']+' output')

    def test_bounded_command_timeout_returns_promptly(self):
        import sys
        import time
        started=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'log_read_timeout'):
            node_logs.bounded_command([sys.executable,'-c','import time; time.sleep(20)'],1024,timeout=0.05)
        self.assertLess(time.monotonic()-started,2)
