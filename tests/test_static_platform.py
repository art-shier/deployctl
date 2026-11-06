import hashlib
import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import unittest
from deployctl.platform_client import PlatformClient,validate_resolution
from deployctl.platform_credentials import Credentials

FIXTURE=Path(__file__).parent/'fixtures/static-archives/valid.zip'

def static_resolution(raw):
    return {'schema_version':2,'minimum_client_version':'1.13.0','project':'project-a','environment':'prod','deployment_type':'static',
            'release':{'id':'a'*32,'version':'v1.0.0','package_path':'/api/v1/projects/project-a/artifacts/'+'a'*32,
                       'sha256':hashlib.sha256(raw).hexdigest(),'archive_format':'zip','size':len(raw),'expanded_size':9,'entry_count':3,'commit':''},
            'configuration':{'id':'b'*32,'revision':1,'deployment_defaults':{'target_dir':'/var/www/a'}}}

class StaticPlatformTests(unittest.TestCase):
    def test_static_resolution_excludes_secrets_and_unknown_fields(self):
        value=static_resolution(FIXTURE.read_bytes())
        self.assertEqual(validate_resolution(value,'project-a','prod')['deployment_type'],'static')
        value['configuration']['runtime_env']={'SECRET':'private'}
        with self.assertRaises(ValueError): validate_resolution(value,'project-a','prod')

    def test_real_http_publish_download_and_recover(self):
        raw=FIXTURE.read_bytes();record=dict(static_resolution(raw)['release'],project='project-a',deployment_type='static',status='published')
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if '/artifacts/' in self.path: data=raw;ctype='application/zip'
                elif '/releases/' in self.path: data=json.dumps(record).encode();ctype='application/json'
                else: data=json.dumps({'slug':'project-a','deployment_type':'static'}).encode();ctype='application/json'
                self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']));seen.append(body)
                data=json.dumps(record).encode();self.send_response(201);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=PlatformClient(Credentials(f'http://127.0.0.1:{server.server_port}','private-test-token'))
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(client.download_release(static_resolution(raw),directory).read_bytes(),raw)
                self.assertEqual(client.publish('project-a','v1.0.0',FIXTURE,commit='')['id'],'a'*32)
                self.assertIn(raw,seen[0]);self.assertIn(b'name="commit"',seen[0])
                client.recover_release('project-a','v1.0.0',directory,commit='')
                self.assertEqual((Path(directory)/'project-a-v1.0.0.zip').read_bytes(),raw)
                with self.assertRaises(ValueError):client.recover_release('project-a','v1.0.0',directory,commit='c'*40)
                with self.assertRaises(ValueError):client.download_to_file(record['package_path'],Path(directory)/'bad','0'*64,256*1024*1024)
                self.assertFalse((Path(directory)/'bad').exists())
        finally:server.shutdown();server.server_close();thread.join()

    def test_download_stream_larger_than_docker_limit(self):
        payload=b'x'*(11*1024*1024)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=PlatformClient(Credentials(f'http://127.0.0.1:{server.server_port}','private-test-token'))
            with tempfile.TemporaryDirectory() as directory:
                path=client.download_to_file('/large',Path(directory)/'large',hashlib.sha256(payload).hexdigest(),256*1024*1024)
                self.assertEqual(path.stat().st_size,11*1024*1024)
        finally:server.shutdown();server.server_close();thread.join()

    def test_download_rejects_redirect_truncation_and_declared_overflow(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/stolen');self.end_headers();return
                self.send_response(200)
                self.send_header('Content-Length','12' if self.path=='/truncated' else '268435457')
                self.end_headers();self.wfile.write(b'short')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=PlatformClient(Credentials(f'http://127.0.0.1:{server.server_port}','private-test-token'))
            with tempfile.TemporaryDirectory() as directory:
                for name in ('redirect','truncated','overflow'):
                    destination=Path(directory)/name
                    with self.assertRaises((ValueError,RuntimeError)):
                        client.download_to_file('/'+name,destination,hashlib.sha256(b'short').hexdigest(),256*1024*1024)
                    self.assertFalse(destination.exists())
        finally:server.shutdown();server.server_close();thread.join()
