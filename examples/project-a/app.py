"""Dependency-free HTTP example: readiness, version and graceful shutdown."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import threading


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health/ready':
            healthy = os.environ.get('FAIL_READINESS') != '1'
            self.send_response(200 if healthy else 503)
            body = {'ready': healthy}
        elif self.path == '/version':
            self.send_response(200)
            body = {'version': os.environ.get('APP_VERSION', 'development')}
        elif self.path == '/':
            self.send_response(200)
            body = {'message': 'project-a is running'}
        else:
            self.send_response(404)
            body = {'error': 'not found'}
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, fmt, *args):
        print(json.dumps({'event': 'http', 'request': self.path, 'message': fmt % args}), flush=True)


def main():
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler)
    def stop(*args):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'event': 'started', 'version': os.environ.get('APP_VERSION', 'development')}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
