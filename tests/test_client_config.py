from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from deployctl.cli import main
from deployctl.platform_credentials import Credentials
from deployctl.cli import parser


IDENTITY = {'schema_version': 1, 'id': 'fixture', 'role': 'deployer',
            'project': '', 'projects': ['notes'], 'groups': ['default'], 'environments': ['prod']}
TOKEN = 'private-fixture-token'


class ClientConfigTests(unittest.TestCase):
    def invoke(self, arguments):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                code = main(arguments)
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue(), error.getvalue()

    def test_login_without_flags_saves_under_current_user_home(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            with patch('pathlib.Path.home', return_value=home), patch('deployctl.platform_credentials.LEGACY_PATH', str(Path(folder) / 'absent/client.json'), create=True):
                with patch('getpass.getpass', return_value=TOKEN), patch('deployctl.platform_client.PlatformClient.json', return_value=IDENTITY):
                    code, output, error = self.invoke(['login'])
                self.assertEqual(code, 0, error)
                path = home / '.ctl/client.json'
                self.assertTrue(path.is_file())
                self.assertEqual(Credentials.load().server, 'https://ctl.shier.art')
                self.assertEqual(Credentials.load().token, TOKEN)
                self.assertNotIn(TOKEN, output)
                self.assertEqual(self.invoke(['config', 'set', 'server', 'custom.test'])[0], 0)
                self.assertEqual(json.loads(path.read_text()), {'server': 'https://custom.test'})
                self.assertEqual(self.invoke(['config', 'get', 'server'])[1].strip(), 'https://custom.test')
                if os.name != 'nt':
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_private_legacy_login_is_copied_once_and_user_config_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            legacy = Path(folder) / 'legacy/client.json'
            Credentials.save(legacy, 'https://legacy.test', TOKEN)
            original = legacy.read_bytes()
            with patch('pathlib.Path.home', return_value=home), patch('deployctl.platform_credentials.LEGACY_PATH', str(legacy), create=True):
                self.assertEqual(Credentials.get_server(), 'https://legacy.test')
                self.assertEqual(Credentials.load().token, TOKEN)
                target = home / '.ctl/client.json'
                self.assertEqual(json.loads(target.read_text()), {'server': 'https://legacy.test', 'token': TOKEN})
                self.assertEqual(legacy.read_bytes(), original)
                self.assertEqual(self.invoke(['config', 'set', 'server', 'new.test'])[0], 0)
                with self.assertRaisesRegex(ValueError, 'ctl login'):
                    Credentials.load()
                self.assertEqual(Credentials.get_server(), 'https://new.test')
                target.write_text('{}')
                with self.assertRaises(ValueError):
                    Credentials.get_server()

    def test_explicit_config_does_not_import_legacy_or_write_to_home(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            legacy = Path(folder) / 'legacy/client.json'
            Credentials.save(legacy, 'https://legacy.test', TOKEN)
            explicit = Path(folder) / 'explicit/client.json'
            with patch('pathlib.Path.home', return_value=home), patch('deployctl.platform_credentials.LEGACY_PATH', str(legacy), create=True):
                self.assertEqual(self.invoke(['config', 'get', 'server', '--client-config', str(explicit)])[1].strip(), 'https://ctl.shier.art')
                self.assertFalse((home / '.ctl').exists())
                self.assertEqual(self.invoke(['config', 'set', 'server', 'explicit.test', '--client-config', str(explicit)])[0], 0)
                self.assertEqual(json.loads(explicit.read_text()), {'server': 'https://explicit.test'})

    def test_unreadable_legacy_file_does_not_block_user_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            legacy = Path(folder) / 'legacy/client.json'
            Credentials.save(legacy, 'https://legacy.test', TOKEN)
            legacy.chmod(0)
            try:
                with patch('pathlib.Path.home', return_value=home), patch('deployctl.platform_credentials.LEGACY_PATH', str(legacy)), patch('deployctl.platform_credentials.os.open', side_effect=PermissionError('unreadable legacy file')):
                    self.assertEqual(self.invoke(['config', 'get', 'server']), (0, 'https://ctl.shier.art\n', ''))
                    self.assertFalse((home / '.ctl/client.json').exists())
            finally:
                legacy.chmod(0o600)

    @unittest.skipIf(os.name == 'nt', 'Linux ownership and permissions')
    def test_insecure_legacy_directory_does_not_block_fresh_home_login(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            legacy = Path(folder) / 'legacy/client.json'
            Credentials.save(legacy, 'https://legacy.test', TOKEN)
            legacy.parent.chmod(0o755)
            with patch('pathlib.Path.home', return_value=home), patch('deployctl.platform_credentials.LEGACY_PATH', str(legacy), create=True):
                with patch('getpass.getpass', return_value=TOKEN), patch('deployctl.platform_client.PlatformClient.json', return_value=IDENTITY):
                    self.assertEqual(self.invoke(['login'])[0], 0)
                self.assertEqual(Credentials.load().server, 'https://ctl.shier.art')
                self.assertEqual(legacy.parent.stat().st_mode & 0o777, 0o755)

    def test_every_platform_command_uses_home_credentials_by_default(self):
        for args in [['login'], ['config', 'get', 'server'], ['config', 'set', 'server', 'ctl.test'], ['whoami'], ['projects'], ['publish', 'notes', '--version', 'v1.0.0', '--package', 'fixture'], ['install', 'notes'], ['upgrade', 'notes']]:
            with self.subTest(args=args):
                self.assertIsNone(parser().parse_args(args).client_config)

    def test_fresh_login_uses_default_and_saves_validated_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            with patch('getpass.getpass', return_value=TOKEN), patch('deployctl.platform_client.PlatformClient.json', return_value=IDENTITY):
                code, output, _ = self.invoke(['login', '--client-config', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(Credentials.load(path).server, 'https://ctl.shier.art')
            self.assertEqual(Credentials.load(path).token, TOKEN)
            self.assertNotIn(TOKEN, output)

    def test_config_then_login_and_discovery_use_saved_server_over_real_http(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.path, self.headers.get('Authorization')))
                payload = IDENTITY if self.path == '/api/v1/me' else [{'slug': 'notes', 'name': 'Notes', 'group': 'default'}]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'private/client.json'
                origin = f'http://127.0.0.1:{server.server_port}'
                suffix = ['--client-config', str(path)]
                self.assertEqual(self.invoke(['config', 'set', 'server', origin + '/'] + suffix)[0], 0)
                self.assertEqual(self.invoke(['config', 'get', 'server'] + suffix)[1].strip(), origin)
                with patch('getpass.getpass', return_value=TOKEN):
                    self.assertEqual(self.invoke(['login'] + suffix)[0], 0)
                self.assertEqual(self.invoke(['whoami'] + suffix)[0], 0)
                self.assertEqual(self.invoke(['projects'] + suffix)[0], 0)
                self.assertEqual(hits, [('/api/v1/me', 'Bearer ' + TOKEN), ('/api/v1/me', 'Bearer ' + TOKEN), ('/api/v1/projects', 'Bearer ' + TOKEN)])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_switch_clears_old_token_and_same_server_preserves_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            Credentials.save(path, 'https://old.test', TOKEN)
            suffix = ['--client-config', str(path)]
            self.assertEqual(self.invoke(['config', 'set', 'server', 'https://old.test/'] + suffix)[0], 0)
            self.assertEqual(Credentials.load(path).token, TOKEN)
            self.assertEqual(self.invoke(['config', 'set', 'server', 'new.test'] + suffix)[0], 0)
            self.assertEqual(json.loads(path.read_text()), {'server': 'https://new.test'})
            code, output, error = self.invoke(['whoami'] + suffix)
            self.assertEqual(code, 1)
            self.assertIn('ctl login', error)
            self.assertNotIn(TOKEN, output + error)

    def test_existing_login_server_is_retained_without_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            Credentials.save(path, 'https://existing.test', TOKEN)
            with patch('getpass.getpass', return_value='replacement-fixture-token'), patch('deployctl.platform_client.PlatformClient.json', return_value=IDENTITY):
                self.assertEqual(self.invoke(['login', '--client-config', str(path)])[0], 0)
            self.assertEqual(Credentials.load(path).server, 'https://existing.test')

    def test_explicit_server_and_private_token_file_remain_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            token_path = Path(folder) / 'token'
            token_path.write_text(TOKEN + '\n')
            token_path.chmod(0o600)
            with patch('deployctl.platform_client.PlatformClient.json', return_value=IDENTITY):
                code, _, _ = self.invoke(['login', '--server', 'https://custom.test/', '--token-file', str(token_path), '--client-config', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(Credentials.load(path).server, 'https://custom.test')
            self.assertEqual(Credentials.load(path).token, TOKEN)

    def test_invalid_config_and_failed_login_leave_saved_identity_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            Credentials.save(path, 'https://existing.test', TOKEN)
            suffix = ['--client-config', str(path)]
            before = path.read_bytes()
            for origin in ['http://remote.test', 'https://token@new.test', 'https://new.test/path', 'https://new.test?secret=x']:
                self.assertEqual(self.invoke(['config', 'set', 'server', origin] + suffix)[0], 1)
                self.assertEqual(path.read_bytes(), before)
            with patch('getpass.getpass', return_value='replacement-fixture-token'), patch('deployctl.platform_client.PlatformClient.json', side_effect=RuntimeError('HTTP 401')):
                self.assertEqual(self.invoke(['login', '--server', 'https://new.test'] + suffix)[0], 1)
            self.assertEqual(path.read_bytes(), before)

    def test_get_default_does_not_create_file_and_corrupt_config_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            suffix = ['--client-config', str(path)]
            code, output, _ = self.invoke(['config', 'get', 'server'] + suffix)
            self.assertEqual(code, 0)
            self.assertEqual(output.strip(), 'https://ctl.shier.art')
            self.assertFalse(path.exists())
            Credentials.save(path, 'https://existing.test', TOKEN)
            path.write_text('{}')
            with patch('getpass.getpass', return_value=TOKEN), patch('deployctl.platform_client.PlatformClient.json') as query:
                self.assertEqual(self.invoke(['login'] + suffix)[0], 1)
                query.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Linux permissions and symlinks')
    def test_config_only_file_stays_private_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/client.json'
            suffix = ['--client-config', str(path)]
            self.assertEqual(self.invoke(['config', 'set', 'server', 'new.test'] + suffix)[0], 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            path.chmod(0o644)
            self.assertEqual(self.invoke(['config', 'get', 'server'] + suffix)[0], 1)
            path.chmod(0o600)
            linked = path.parent / 'linked.json'
            linked.symlink_to(path)
            self.assertEqual(self.invoke(['config', 'get', 'server', '--client-config', str(linked)])[0], 1)
            self.assertEqual(self.invoke(['config', 'set', 'server', 'other.test', '--client-config', str(linked)])[0], 1)
