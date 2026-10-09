import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import subprocess
import sys
import hashlib
from unittest.mock import patch

from deployctl.cli import main, parser
from deployctl import server_bundle

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/art-shier/ctl-server@sha256:' + 'a' * 64


def release(tag='v1.9.0', **changes):
    return dict(tag_name=tag, draft=False, prerelease=False,
                assets=[{'name': f'ctl-platform-{tag}.tar.gz'},
                        {'name': f'ctl-platform-{tag}.tar.gz.sha256'}], **changes)


class ServerCommandTests(unittest.TestCase):
    def test_install_without_url_and_flat_aliases_share_options(self):
        for command in (['server', 'install'], ['server-install']):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    args = parser().parse_args([*command, '--api-port', '8084'])
            except SystemExit:
                self.fail('server install still requires an explicit release URL')
            self.assertEqual(args.command, 'server')
            self.assertEqual(args.server_command, 'install')
            self.assertIsNone(args.release)
            self.assertIsNone(args.server_version)
            self.assertEqual(args.api_port, 8084)
        for operation in ('upgrade', 'restart', 'start', 'stop', 'status', 'logs'):
            args = parser().parse_args(['server-' + operation])
            self.assertEqual(args.server_command, operation)
        args = parser().parse_args(['server', 'upgrade', '--version', 'v1.9.0'])
        self.assertEqual(args.server_version, 'v1.9.0')

    def test_explicit_release_and_version_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['server-install', '--release', '/tmp/package.tar.gz', '--version', 'v1.9.0'])

    def test_latest_resolves_to_fixed_official_bundle(self):
        self.assertTrue(callable(getattr(server_bundle, 'resolve_server_release', None)), 'official server release resolution is missing')
        with patch.object(server_bundle, 'fetch', return_value=json.dumps(release()).encode()) as transport:
            source, tag = server_bundle.resolve_server_release()
        self.assertEqual(tag, 'v1.9.0')
        self.assertEqual(source, 'https://github.com/art-shier/deployctl/releases/download/v1.9.0/ctl-platform-v1.9.0.tar.gz')
        self.assertEqual(transport.call_args.args[0].full_url,
                         'https://api.github.com/repos/art-shier/deployctl/releases/latest')

    def test_exact_version_cannot_silently_resolve_another_version(self):
        self.assertTrue(callable(getattr(server_bundle, 'resolve_server_release', None)), 'official server release resolution is missing')
        with patch.object(server_bundle, 'fetch', return_value=json.dumps(release()).encode()):
            with self.assertRaisesRegex(ValueError, 'version'):
                server_bundle.resolve_server_release('v1.8.0')
            source, tag = server_bundle.resolve_server_release('1.9.0')
            self.assertEqual(tag, 'v1.9.0')
            self.assertIn('/v1.9.0/', source)

    def test_unpublished_missing_duplicate_or_prerelease_latest_is_rejected(self):
        self.assertTrue(callable(getattr(server_bundle, 'resolve_server_release', None)), 'official server release resolution is missing')
        fixtures = []
        for key in ('draft', 'prerelease'):
            value = release(); value[key] = True; fixtures.append(value)
        value = release(); value['assets'].pop(); fixtures.append(value)
        value = release(); value['assets'].append(value['assets'][0]); fixtures.append(value)
        fixtures.extend([[], {'tag_name': '../../escape'}])
        for value in fixtures:
            with self.subTest(value=value), patch.object(server_bundle, 'fetch', return_value=json.dumps(value).encode()):
                with self.assertRaises(ValueError): server_bundle.resolve_server_release()

    def test_restart_readiness_uses_actual_loopback_endpoint_and_times_out(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/api/v1/health/ready' else 404)
                self.end_headers()
                self.wfile.write(b'{"status":"ready"}')
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        try: server_bundle.wait_ready(http.server_port, timeout=.5)
        finally: http.shutdown(); http.server_close(); thread.join()
        with self.assertRaisesRegex(RuntimeError, 'readiness timed out'):
            server_bundle.wait_ready(http.server_port, timeout=.03)

    def test_readiness_cannot_accept_proxy_response_or_redirect_or_html(self):
        class Handler(BaseHTTPRequestHandler):
            hits = 0
            mode = 'proxy'
            def do_GET(self):
                Handler.hits += 1
                self.send_response(302 if Handler.mode == 'redirect' and self.path == '/api/v1/health/ready' else 200)
                if Handler.mode == 'redirect': self.send_header('Location', '/landing')
                self.end_headers()
                self.wfile.write(b'<html>not ready</html>' if Handler.mode == 'html' else b'{"status":"ready"}')
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); closed_port = sock.getsockname()[1]
        environment = {**os.environ, 'http_proxy':f'http://127.0.0.1:{http.server_port}',
                       'HTTP_PROXY':f'http://127.0.0.1:{http.server_port}', 'no_proxy':'', 'NO_PROXY':''}
        try:
            child = subprocess.run([sys.executable, '-c', f'from deployctl.server_bundle import wait_ready; wait_ready({closed_port}, timeout=.05)'],
                                   env=environment, cwd=ROOT, capture_output=True, timeout=5)
            self.assertNotEqual(child.returncode, 0, 'proxy HTTP200 falsely reported the stopped local API ready')
            self.assertEqual(Handler.hits, 0)
            for mode in ('html', 'redirect'):
                Handler.mode = mode
                with self.assertRaisesRegex(RuntimeError, 'readiness timed out'):
                    server_bundle.wait_ready(http.server_port, timeout=.03)
        finally: http.shutdown(); http.server_close(); thread.join()

    @unittest.skipIf(os.name == 'nt', 'protected root state and bundle installation')
    def test_automatic_source_checks_downloaded_manifest_version_before_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            package = server_bundle.build_bundle(ROOT, folder/'package', IMAGE, 'v1.9.0')
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'run_bootstrap'), patch.object(server_bundle, 'acquire_release', return_value=package), patch.object(server_bundle, 'fetch', return_value=json.dumps(release()).encode()):
                state = server_bundle.deploy_server(home=folder/'instance')
                self.assertEqual(state['current']['version'], 'v1.9.0')
                self.assertIsNone(state['pending'])
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'run_bootstrap'), patch.object(server_bundle, 'acquire_release', return_value=package), patch.object(server_bundle, 'fetch', return_value=json.dumps(release('v1.9.1')).encode()):
                with self.assertRaisesRegex(ValueError, 'bundle version'):
                    server_bundle.deploy_server(home=folder/'instance', upgrade=True)
            self.assertEqual(json.loads((folder/'instance/server-state.json').read_text()), state)

    @unittest.skipIf(os.name == 'nt', 'protected root state and old cache migration')
    def test_legacy_cache_requires_original_archive_and_then_works_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); home = folder/'instance'
            package = server_bundle.build_bundle(ROOT, folder/'package', IMAGE, 'v1.9.0')
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'run_bootstrap'):
                state = server_bundle.deploy_server(package, home=home)
            (home/'instance.json').write_text(json.dumps({'external_database': True, 'api_port': 8084})); (home/'instance.json').chmod(0o600)
            (home/'compose.env').write_text(''); (home/'compose.env').chmod(0o600)
            bundle = home/'server-releases'/state['current']['sha256']
            archive = bundle/'.verified-release.tar.gz'; archive.unlink()
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'check_project'), patch.object(server_bundle, 'run_server_command'), patch.object(server_bundle, 'acquire_release', return_value=package):
                self.assertEqual(server_bundle.operate_server('status', home), state)
            self.assertEqual(archive.read_bytes(), package.read_bytes())
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'check_project'), patch.object(server_bundle, 'run_server_command'), patch.object(server_bundle, 'acquire_release', side_effect=RuntimeError('offline')):
                self.assertEqual(server_bundle.operate_server('status', home), state)
            archive.unlink()
            manifest_path = bundle/'server-release.json'
            manifest = json.loads(manifest_path.read_text()); manifest['files']['control-deploy/compose.yaml'] = '0'*64
            manifest_path.write_text(json.dumps(manifest))
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'acquire_release', return_value=package):
                with self.assertRaisesRegex(ValueError, 'cache.*modified'):
                    server_bundle.operate_server('status', home)
            self.assertFalse(archive.exists())
            self.assertEqual(json.loads((home/'server-state.json').read_text()), state)

    @unittest.skipIf(os.name == 'nt', 'protected instance permissions')
    def test_bootstrap_success_does_not_skip_api_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'instance.json').write_text(json.dumps({'external_database': False, 'api_port': 8084})); (home/'instance.json').chmod(0o600)
            with patch.object(server_bundle, 'check_project'), patch.object(server_bundle, 'run_server_command'), patch.object(server_bundle, 'wait_ready', side_effect=RuntimeError('readiness failed')):
                with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
                    server_bundle.run_bootstrap(home, home, IMAGE, {})

    @unittest.skipIf(os.name == 'nt', 'real POSIX command and protected instance paths')
    def test_installed_lifecycle_preserves_files_and_uses_instance_profile(self):
        self.assertTrue(callable(getattr(server_bundle, 'operate_server', None)), 'installed server lifecycle is missing')
        # The fixture owns a fake Docker daemon. Root authorization is tested
        # separately; hosted unit-test runners intentionally run unprivileged.
        with tempfile.TemporaryDirectory() as tmp, patch.object(server_bundle, 'require_runtime'):
            folder = Path(tmp); home = folder/'instance'
            package = server_bundle.build_bundle(ROOT, folder/'package', IMAGE, 'v1.9.0')
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'run_bootstrap'):
                server_bundle.deploy_server(package, home=home)
            (home/'instance.json').write_text(json.dumps({'external_database': False, 'api_port': 8084}))
            (home/'instance.json').chmod(0o600)
            (home/'compose.env').write_text('CTL_IMAGE=' + IMAGE + '\nCTL_HOME=' + str(home) + '\n')
            (home/'compose.env').chmod(0o600)
            executable = folder/'docker'
            executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with Path(os.environ['SERVER_TEST_CALLS']).open('a') as stream:
    stream.write(json.dumps({'args':sys.argv[1:], 'token':os.environ.get('GH_TOKEN')})+'\\n')
if sys.argv[1:3] == ['compose','version']: print('2.30.0')
if 'ps' in sys.argv and '--all' in sys.argv: print('api running 127.0.0.1:8084->8080/tcp')
if 'logs' in sys.argv: print('fixture service log')
''')
            executable.chmod(0o700)
            calls = folder/'calls.jsonl'
            before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            environment = {**os.environ, 'PATH':str(folder)+os.pathsep+os.environ['PATH'],
                           'SERVER_TEST_CALLS':str(calls), 'GH_TOKEN':'parent-secret'}
            with patch.dict(os.environ, environment), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'wait_ready'):
                for action in ('restart', 'stop', 'start', 'status', 'logs'):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(main(['server-' + action, '--home', str(home)]), 0)
                    if action == 'status': self.assertIn('v1.9.0', output.getvalue())
            operations = [json.loads(line) for line in calls.read_text().splitlines()]
            compose = [v for v in operations if '--project-name' in v['args']]
            self.assertEqual(len(compose), 5)
            for call in compose:
                self.assertIsNone(call['token'])
                self.assertIn('--profile', call['args'])
                self.assertIn('database', call['args'])
                self.assertEqual(call['args'][call['args'].index('--env-file')+1], str(home/'compose.env'))
                self.assertNotIn('down', call['args'])
            after = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file() and p.name != '.server.lock'}
            self.assertEqual(after, {k:v for k,v in before.items() if k.name != '.server.lock'})
            with patch.dict(os.environ, environment), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'wait_ready', side_effect=RuntimeError('not ready')), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['server-start', '--home', str(home)]), 1, 'start must fail when API is not ready')
            compose_count = len([line for line in calls.read_text().splitlines() if '--project-name' in line])
            active = json.loads((home/'server-state.json').read_text())['current']
            bundle = home/'server-releases'/active['sha256']
            compose_path = bundle/'control-deploy/compose.yaml'
            manifest_path = bundle/'server-release.json'
            old_compose, old_manifest = compose_path.read_bytes(), manifest_path.read_bytes()
            compose_path.write_bytes(old_compose.replace(b'127.0.0.1:', b'0.0.0.0:'))
            modified = json.loads(old_manifest)
            modified['files']['control-deploy/compose.yaml'] = hashlib.sha256(compose_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(modified))
            with patch.dict(os.environ, environment), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), patch.object(server_bundle, 'wait_ready'), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['server-start', '--home', str(home)]), 1, 'self-consistent modified cache must be rejected')
            compose_path.write_bytes(old_compose); manifest_path.write_bytes(old_manifest)
            env_path = home/'compose.env'; saved_env = env_path.read_bytes()
            env_path.write_text('CTL_IMAGE=unverified:latest\nCTL_HOME=' + str(home) + '\n')
            with patch.dict(os.environ, environment), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['server-start', '--home', str(home)]), 1)
            env_path.write_bytes(saved_env)
            self.assertEqual(len([json.loads(line) for line in calls.read_text().splitlines() if '--project-name' in line]), compose_count)
            state = json.loads((home/'server-state.json').read_text()); state['pending'] = state['current']
            (home/'server-state.json').write_text(json.dumps(state))
            count = compose_count
            with patch.dict(os.environ, environment), patch.object(server_bundle, 'LOCK_HOME', folder/'locks'), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['server-restart', '--home', str(home)]), 1)
            self.assertEqual(len([json.loads(line) for line in calls.read_text().splitlines() if '--project-name' in line]), count)

    def test_actual_server_runtime_rejects_non_root_before_docker(self):
        with patch.object(server_bundle.sys, 'platform', 'linux'), patch.object(server_bundle.os, 'geteuid', return_value=1000, create=True):
            with self.assertRaisesRegex(ValueError, 'Linux root'):
                server_bundle.require_runtime()


if __name__ == '__main__': unittest.main()
