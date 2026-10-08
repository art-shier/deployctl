import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from deployctl.platform_credentials import Credentials
from deployctl.platform_client import PlatformClient, validate_origin, validate_resolution
from deployctl.runtime_config import merge_runtime_values, merge_install_params


class PlatformTests(unittest.TestCase):
    def test_origin_and_resolution_boundaries(self):
        for url in ['http://example.org', 'https://token@example.org', 'https://x/a', 'https://x?token=x']:
            with self.assertRaises(ValueError): validate_origin(url)
        self.assertEqual(validate_origin('http://127.0.0.1:1234/'), 'http://127.0.0.1:1234')
        value = self.resolution()
        self.assertEqual(validate_resolution(value, 'notes', 'prod')['configuration']['runtime_env']['A'], '')
        for path in ['https://evil.test/package', '//evil.test/p', '/api/v1/projects/other/artifacts/'+'a'*32]:
            value['release']['package_path'] = path
            with self.assertRaises(ValueError): validate_resolution(value, 'notes', 'prod')

    @staticmethod
    def resolution():
        return {'schema_version':1,'minimum_client_version':'1.7.0','project':'notes','environment':'prod',
                'release':{'id':'a'*32,'version':'v1.0.0','image':'registry.test/notes@sha256:'+'b'*64,
                           'package_path':'/api/v1/projects/notes/artifacts/'+'a'*32,'sha256':'c'*64},
                'configuration':{'id':'d'*32,'revision':1,'runtime_env':{'A':''},'install_params':{},'deployment_defaults':{}}}

    def test_credentials_private_atomic_and_no_links(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'private/client.json'
            Credentials.save(path, 'https://ctl.test', 'secret-test-token')
            self.assertEqual(Credentials.load(path).token, 'secret-test-token')
            if os.name != 'nt': self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('[]')
            with self.assertRaises(ValueError): Credentials.load(path)

    def test_network_error_does_not_echo_token(self):
        with patch('deployctl.platform_client.build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://ctl.test',401,'secret-test-token',None,None)
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as error:
                PlatformClient(Credentials('https://ctl.test','secret-test-token')).request('GET','/api/v1/me')
            self.assertNotIn('secret-test-token', str(error.exception))

    def test_remote_precedence_and_local_overrides(self):
        values, overrides = merge_runtime_values({'A':'base'}, {}, {'A':'local'}, {'B':'cli'}, [], 'v1.0.0', {'A':'remote','B':''})
        self.assertEqual(values, {'A':'remote','B':'','APP_VERSION':'v1.0.0'})
        self.assertEqual(overrides, {'A':'local','B':'cli'})
        next_values, _ = merge_runtime_values({}, {}, overrides, {}, [], 'v1.0.0', {})
        self.assertEqual(next_values['A'], 'local')
        self.assertEqual(merge_install_params({'ADMIN':'cli'}, {'ADMIN':'remote'}), {'ADMIN':'remote'})

    def test_redirect_is_never_followed(self):
        hits=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302); self.send_header('Location','/stolen');self.end_headers()
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=PlatformClient(Credentials(f'http://127.0.0.1:{server.server_port}','private-test-token'))
            with self.assertRaisesRegex(RuntimeError,'HTTP 302'): client.request('GET','/api/v1/me')
            self.assertEqual(hits,['/api/v1/me'])
        finally: server.shutdown();server.server_close();worker.join()

    @unittest.skipIf(os.name=='nt','Linux file permissions and symlinks')
    def test_credentials_reject_public_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'client.json'
            Credentials.save(path,'https://ctl.test','private-test-token')
            path.chmod(0o644)
            with self.assertRaises(ValueError): Credentials.load(path)
            path.chmod(0o600)
            linked=Path(root)/'linked.json';linked.symlink_to(path)
            with self.assertRaises(ValueError): Credentials.load(linked)

    def test_managed_cli_and_prod_conflicts(self):
        from contextlib import redirect_stdout,redirect_stderr,nullcontext
        import io
        from deployctl.cli import main
        client=PlatformClient(Credentials('https://ctl.test','private-test-token'))
        with patch('deployctl.platform_credentials.Credentials.load',return_value=Credentials(client.server,client.token)), patch('deployctl.platform_client.PlatformClient') as platform, patch('deployctl.runtime.Manager') as manager, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            platform.return_value.resolve.return_value=self.resolution()
            platform.return_value.registry_config.return_value=nullcontext(None)
            platform.return_value.download_release.return_value=Path('fixture')
            manager.return_value.deploy.return_value={'current':{'version':'v1.0.0'}}
            with patch('deployctl.cli.report_receipt'):
                self.assertEqual(main(['install','notes','--prod']),0)
            call=manager.return_value.deploy.call_args
            self.assertEqual(call.args[:2],('notes','prod'))
            self.assertEqual(call.kwargs['managed_runtime'],{'A':''})
            self.assertEqual(main(['install','notes','--prod','--env','test']),1)

if __name__ == '__main__': unittest.main()
