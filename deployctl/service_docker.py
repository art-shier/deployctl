"""Docker adapter that targets individual services and verifies owned storage."""
import json
from pathlib import Path
import time

from .contract import validate_name


class ServiceDockerDriver:
    def __init__(self, commands, directory, project, release, environment, inventory):
        self.commands = commands
        self.directory = Path(directory)
        self.project = validate_name(project, 'compose project', 96)
        self.release = release
        self.environment = dict(environment)
        self.inventory = inventory
        self.config = release['deployment']
        self.nodes = {**self.config['services'], **self.config.get('jobs', {})}
        self.network = project + '-internal'
        self.job_runner = None
        self.rollback_drivers = {}

    def compose(self, *args):
        return self.commands._run(['docker', 'compose', '--project-name', self.project,
            '--project-directory', str(self.directory), '--file', str(self.directory / 'compose.yaml'), *args], self.environment)

    def inspect(self, kind, name):
        # Inspect absence must be distinguished from daemon/auth failure.
        listing = self.commands._run(['docker', kind, 'ls', '--format', '{{.Name}}'] if kind in ('volume', 'network')
                                     else ['docker', 'ps', '-a', '--format', '{{.ID}} {{.Names}}'])
        if not any(name in line.split() for line in listing.splitlines()):
            return None
        result = json.loads(self.commands._run(['docker', kind, 'inspect', name]))
        if not isinstance(result, list) or len(result) != 1:
            raise ValueError('ambiguous Docker resource')
        return result[0]

    def pull(self, name):
        spec = self.nodes[name]
        self.commands._stream_pull(['docker', 'pull', spec['image']], self.environment)
        platform = self.commands._run(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', spec['image']])
        if platform != self.config['platform']:
            raise ValueError('image platform does not match release platform')

    def _owned(self, resource, kind, name):
        labels = resource.get('Labels') or {}
        expected = {'io.team-deploy.project': self.project, 'io.team-deploy.resource': kind + ':' + name}
        if any(labels.get(key) != value for key, value in expected.items()):
            raise ValueError('Docker resource ownership mismatch')

    def ensure_resources(self):
        existing = self.inspect('network', self.network)
        if existing is not None:
            self._owned(existing, 'network', 'app')
        else:
            self.commands._run(['docker', 'network', 'create', '--label', 'io.team-deploy.project=' + self.project,
                                '--label', 'io.team-deploy.resource=network:app', self.network])
        self.inventory.setdefault('volumes', {})
        for name in self.config['volumes']:
            actual_name = self.project + '-' + name
            prior = self.inventory['volumes'].get(name)
            existing = self.inspect('volume', actual_name)
            if existing is None:
                if prior:
                    raise ValueError('established volume missing; recovery required')
                self.commands._run(['docker', 'volume', 'create', '--label', 'io.team-deploy.project=' + self.project,
                                    '--label', 'io.team-deploy.resource=volume:' + name, actual_name])
                existing = self.inspect('volume', actual_name)
                if existing is None: raise ValueError('created volume cannot be verified')
            self._owned(existing, 'volume', name)
            identity = existing.get('CreatedAt')
            if not identity or (prior and prior.get('identity') != identity):
                raise ValueError('volume identity changed; recovery required')
            self.inventory['volumes'][name] = {'name': actual_name, 'identity': identity, 'present': True}

    def up(self, name):
        if name not in self.config['services']: raise ValueError('not a managed service')
        self.compose('up', '-d', '--no-build', '--pull', 'never', '--no-deps', name)

    def stop(self, name):
        if name not in self.config['services'] or self.config['services'][name]['lifecycle'] != 'application':
            raise ValueError('automatic stop is only allowed for application services')
        self.compose('stop', name)

    def container(self, name):
        ids = self.compose('ps', '--all', '-q', name).splitlines()
        if not ids: return None
        if len(ids) != 1: raise ValueError('multiple service containers found')
        raw = json.loads(self.commands._run(['docker', 'container', 'inspect', ids[0]]))
        if len(raw) != 1: raise ValueError('ambiguous service container')
        return raw[0]

    def healthy(self, name):
        from .runtime import http_ready
        spec = self.config['services'][name]
        deadline = time.monotonic() + spec['health']['startup_timeout_seconds']
        while True:
            container = self.container(name)
            if container:
                conf = container.get('Config', {}); labels = conf.get('Labels') or {}; state = container.get('State', {})
                if labels.get('com.docker.compose.project') != self.project or labels.get('com.docker.compose.service') != name:
                    raise ValueError('service container ownership mismatch')
                if conf.get('Image') != spec['image']:
                    raise ValueError('service image mismatch')
                if state.get('Running'):
                    if 'exec' in spec['health']:
                        ready = state.get('Health', {}).get('Status') == 'healthy'
                    else:
                        endpoint = next(iter(spec['endpoints'].values()))
                        host = '127.0.0.1' if endpoint['bind_address'] == '0.0.0.0' else endpoint['bind_address']
                        ready = http_ready(f"http://{host}:{endpoint['host_port']}{spec['health']['http']}")
                    if ready: return container
            if time.monotonic() >= deadline: raise RuntimeError('service readiness timed out: ' + name)
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def restore(self, name, previous):
        # Restored config must be prepared and integrity-checked by the manager.
        driver = self.rollback_drivers.get(name)
        if driver is None: raise ValueError('verified previous service driver required for rollback')
        driver.up(name); driver.healthy(name)

    def run_job(self, name):
        if self.job_runner is None: raise ValueError('verified job executor required')
        return self.job_runner(name)
