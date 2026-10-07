"""Dependency-free HTTP example: readiness, version and graceful shutdown."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import threading


def load_runtime_config(environment=None):
    values = dict(os.environ if environment is None else environment)
    if 'DEPLOYCTL_ENV_FILE' not in values:
        return values
    try:
        path = Path(values['DEPLOYCTL_ENV_FILE'])
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            raise ValueError('invalid configuration file')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items())
                or not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9][A-Za-z0-9.-]*)?', data.get('APP_VERSION', ''))
                or ('APP_VERSION' in values and data['APP_VERSION'] != values['APP_VERSION'])):
            raise ValueError('invalid configuration values/version')
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('cannot load the explicitly supplied runtime JSON configuration') from exc
    values.update(data)
    return values


CONFIGURATION = load_runtime_config()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health/ready':
            healthy = CONFIGURATION.get('FAIL_READINESS') != '1'
            self.send_response(200 if healthy else 503)
            body = {'ready': healthy}
        elif self.path == '/version':
            self.send_response(200)
            body = {'version': CONFIGURATION.get('APP_VERSION', 'development')}
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
    server = ThreadingHTTPServer(('0.0.0.0', int(CONFIGURATION.get('PORT', '8080'))), Handler)
    def stop(*args):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'event': 'started', 'version': CONFIGURATION.get('APP_VERSION', 'development')}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
