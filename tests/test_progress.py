from contextlib import redirect_stderr, redirect_stdout
import io
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from deployctl.cli import main
from deployctl.runtime import Manager
from deployctl.release import build_release
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials
from test_release import CONFIG, IMAGE
from test_runtime import FakeDocker


class SignalStream(io.StringIO):
    def __init__(self, tty=False):
        super().__init__()
        self.waiting = threading.Event()
        self.tty = tty
    def isatty(self): return self.tty
    def write(self, value):
        result = super().write(value)
        if '[waiting]' in value: self.waiting.set()
        return result


class ProgressTests(unittest.TestCase):
    def reporter(self, stream, **kwargs):
        try:
            from deployctl.progress import Progress
        except ImportError:
            self.fail('installation progress reporter is missing')
        return Progress(stream=stream, **kwargs)

    def test_stage_is_visible_while_work_is_blocked_and_finishes_last(self):
        stream = SignalStream()
        progress = self.reporter(stream, interval=.01)
        with progress.stage('Pulling image'):
            self.assertIn('[working] Pulling image', stream.getvalue())
            self.assertTrue(stream.waiting.wait(1), 'no heartbeat while operation is still running')
        lines = stream.getvalue().splitlines()
        self.assertIn('[waiting] Pulling image', stream.getvalue())
        self.assertIn('elapsed', stream.getvalue())
        self.assertTrue(lines[-1].startswith('[done] Pulling image'))

    def test_failure_and_interruption_are_not_reported_as_success_or_echoed(self):
        for error, status in [(RuntimeError('private-token-marker'), '[failed]'), (KeyboardInterrupt(), '[interrupted]')]:
            stream = SignalStream()
            progress = self.reporter(stream)
            with self.assertRaises(type(error)):
                with progress.stage('Preparing configuration'):
                    raise error
            self.assertIn(status + ' Preparing configuration', stream.getvalue())
            self.assertNotIn('[done]', stream.getvalue())
            self.assertNotIn('private-token-marker', stream.getvalue())

    def test_unavailable_progress_stream_does_not_change_work_result(self):
        class BrokenStream:
            def write(self, value): raise BrokenPipeError('closed display')
        progress = self.reporter(BrokenStream())
        completed = []
        with progress.stage('Pulling image'):
            progress.downloaded(25, 100)
            completed.append(True)
        self.assertEqual(completed, [True])

    def test_real_download_percentage_and_unknown_size_are_distinct(self):
        stream = SignalStream(tty=True)
        now = [0.]
        progress = self.reporter(stream, interval=1, clock=lambda: now[0])
        progress.downloaded(0, 100)
        now[0] = 2
        progress.downloaded(25, 100)
        self.assertIn('25%', stream.getvalue())
        self.assertIn('[', stream.getvalue())
        stream = SignalStream()
        progress = self.reporter(stream)
        progress.downloaded(1234, None)
        self.assertIn('1234', stream.getvalue())
        self.assertNotIn('%', stream.getvalue())

    def test_deployment_phases_are_reported_in_order_without_configuration_values(self):
        stream = SignalStream()
        progress = self.reporter(stream)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config = base / 'config/project-a/production'
            config.mkdir(parents=True)
            (config / 'config.env').write_text('DATABASE_URL=private-config-marker\n')
            package = build_release(CONFIG, IMAGE, 'v1.0.0', base / 'packages')
            manager = Manager(base / 'apps', base / 'config', FakeDocker(), progress=progress)
            state = manager.deploy('project-a', 'production', package)
            self.assertEqual(state['current']['version'], 'v1.0.0')
        output = stream.getvalue()
        phases = ['Reading release', 'Checking Docker', 'Pulling image', 'Starting container', 'Checking readiness', 'Final readiness check', 'Saving deployment state']
        positions = [output.index('[working] ' + phase) for phase in phases]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('private-config-marker', output)

    def test_failed_health_reports_recovery_and_preserves_failed_exit(self):
        stream = SignalStream()
        progress = self.reporter(stream)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config = base / 'config/project-a/production'
            config.mkdir(parents=True)
            (config / 'config.env').write_text('DATABASE_URL=fixture\n')
            driver = FakeDocker()
            driver.fail_versions.add('v1.0.0')
            manager = Manager(base / 'apps', base / 'config', driver, progress=progress)
            package = build_release(CONFIG, IMAGE, 'v1.0.0', base / 'packages')
            with self.assertRaisesRegex(RuntimeError, 'candidate stopped'):
                manager.deploy('project-a', 'production', package)
            self.assertIsNone(driver.active)
        self.assertIn('[failed] Checking readiness', stream.getvalue())
        self.assertIn('[working] Recovering previous deployment', stream.getvalue())
        self.assertNotIn('[done] Saving deployment state', stream.getvalue())

    def test_cli_progress_goes_to_stderr_and_quiet_preserves_stdout(self):
        for quiet in [False, True]:
            output, diagnostics = io.StringIO(), io.StringIO()
            flags = ['--quiet'] if quiet else []
            with patch('deployctl.runtime.Manager') as manager, patch('deployctl.download.acquire_release', return_value=Path('archive')), redirect_stdout(output), redirect_stderr(diagnostics):
                manager.return_value.deploy.return_value = {'current': {'version': 'v1.0.0'}}
                try:
                    code = main(['install', 'project-a', '--prod', '--release', 'archive', *flags])
                except SystemExit as error:
                    code = error.code
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue().strip(), 'OK: project-a/prod running v1.0.0')
            if quiet:
                self.assertEqual(diagnostics.getvalue(), '')
            else:
                self.assertIn('[working]', diagnostics.getvalue())


class ResponseReadTests(unittest.TestCase):
    def read(self, response, limit, progress=None):
        try:
            from deployctl.download import read_response
        except ImportError:
            self.fail('bounded progress-aware response reader is missing')
        return read_response(response, limit, progress)

    def response(self, raw, length=None):
        class Response(io.BytesIO): pass
        response = Response(raw)
        response.headers = {} if length is None else {'Content-Length': str(length)}
        return response

    def test_download_reports_actual_bytes_and_respects_size_limit(self):
        class Recorder:
            def __init__(self): self.updates = []
            def downloaded(self, current, total): self.updates.append((current, total))
        progress = Recorder()
        raw = b'a' * 200000
        self.assertEqual(self.read(self.response(raw, len(raw)), len(raw), progress), raw)
        self.assertEqual(progress.updates[0], (0, 200000))
        self.assertEqual(progress.updates[-1], (200000, 200000))
        self.assertGreater(len(progress.updates), 2)
        self.assertEqual(sorted(progress.updates), progress.updates)
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.read(self.response(b'12345'), 4)

    def test_truncated_response_is_not_reported_as_complete(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            self.read(self.response(b'1234', 8), 16)

    def test_unknown_length_finishes_with_actual_bytes_even_before_throttle_interval(self):
        from deployctl.progress import Progress
        stream = SignalStream()
        progress = Progress(stream=stream)
        self.assertEqual(self.read(self.response(b'1234'), 16, progress), b'1234')
        self.assertIn('Release download: 4 bytes (total size unknown)', stream.getvalue())
        self.assertNotIn('%', stream.getvalue())

    def test_managed_download_over_real_http_shows_wait_and_verifies_package(self):
        from deployctl.progress import Progress
        stream = SignalStream()
        progress = Progress(stream=stream, interval=.01)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            package = build_release(CONFIG, IMAGE, 'v1.0.0', base / 'packages')
            raw = package.read_bytes()
            requests = []
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append((self.path, self.headers.get('Authorization')))
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.flush()
                    stream.waiting.wait(1)
                    self.wfile.write(raw)
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client = PlatformClient(Credentials(f'http://127.0.0.1:{server.server_port}', 'private-http-token'))
                resolution = {'project': 'project-a', 'release': {'package_path': '/api/v1/projects/project-a/artifacts/' + 'a' * 32, 'version': 'v1.0.0', 'image': IMAGE, 'sha256': hashlib.sha256(raw).hexdigest()}}
                with progress.stage('Downloading release'):
                    actual = client.download_release(resolution, base, progress=progress)
                self.assertEqual(actual.read_bytes(), raw)
                self.assertEqual(requests, [(resolution['release']['package_path'], 'Bearer private-http-token')])
                self.assertIn('[waiting] Downloading release', stream.getvalue())
                self.assertIn('100%', stream.getvalue())
                self.assertNotIn('private-http-token', stream.getvalue())
                resolution['release']['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    with progress.stage('Downloading invalid release'):
                        client.download_release(resolution, base, progress=progress)
                self.assertIn('[failed] Downloading invalid release', stream.getvalue())
                self.assertNotIn('[done] Downloading invalid release', stream.getvalue())
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
