from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/ready' else 503)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


class HealthTests(unittest.TestCase):
    def test_readiness_uses_real_http_status(self):
        from deployctl.runtime import http_ready
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            self.assertTrue(http_ready(base + '/ready'))
            self.assertFalse(http_ready(base + '/broken'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
