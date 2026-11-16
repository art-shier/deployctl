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
        self.snapshot = None

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
        prior = self.inventory.get('network')
        if existing is not None:
            self._owned(existing, 'network', 'app')
            if not prior: raise ValueError('unregistered network exists; explicit recovery required')
            if prior.get('name') != self.network or prior.get('identity') != existing.get('Id'):
                raise ValueError('network identity changed; recovery required')
        else:
            if prior: raise ValueError('established network missing; recovery required')
            self.commands._run(['docker', 'network', 'create', '--label', 'io.team-deploy.project=' + self.project,
                                '--label', 'io.team-deploy.resource=network:app', self.network])
            existing = self.inspect('network', self.network)
            if not existing or not existing.get('Id'): raise ValueError('created network cannot be verified')
            self._owned(existing, 'network', 'app')
        self.inventory['network'] = {'name': self.network, 'identity': existing['Id']}
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
            elif prior is None:
                raise ValueError('unregistered volume exists; refusing adoption')
            self._owned(existing, 'volume', name)
            identity = existing.get('CreatedAt')
            if not identity or (prior and (prior.get('name') != actual_name or prior.get('identity') != identity)):
                raise ValueError('volume identity changed; recovery required')
            self.inventory['volumes'][name] = {'name': actual_name, 'identity': identity, 'present': True}

    def up(self, name):
        if name not in self.config['services']: raise ValueError('not a managed service')
        self.compose('up', '-d', '--no-build', '--pull', 'never', '--no-deps', name)

    def start(self, name):
        if name not in self.config['services']: raise ValueError('not a managed service')
        if self.container(name) is None:
            if self.config['services'][name]['lifecycle'] == 'persistent':
                raise ValueError('persistent container missing; explicit recovery required')
            return self.up(name)
        self.compose('start', name)

    def stop(self, name):
        if name not in self.config['services'] or self.config['services'][name]['lifecycle'] != 'application':
            raise ValueError('automatic stop is only allowed for application services')
        self.compose('stop', name)

    def container(self, name):
        ids = self.commands._run(['docker', 'ps', '--all', '--filter', 'label=com.docker.compose.project='+self.project,
                                  '--filter', 'label=com.docker.compose.service='+name, '--format', '{{.ID}}']).splitlines()
        if not ids: return None
        if len(ids) != 1: raise ValueError('multiple service containers found')
        raw = json.loads(self.commands._run(['docker', 'container', 'inspect', ids[0]]))
        if len(raw) != 1: raise ValueError('ambiguous service container')
        return raw[0]

    def observe(self, name, expected_id=None):
        return self.verify_container(name, self.container(name), expected_id)

    def verify_container(self, name, container, expected_id=None, job=False):
        from .runtime import http_ready
        from .service_plan import service_values
        if self.snapshot is None: raise ValueError('verified service snapshot required')
        if container is None: return {'container_id': None, 'running': False, 'healthy': False}
        spec = self.config['jobs' if job else 'services'][name]
        conf = container.get('Config') or {}; labels = conf.get('Labels') or {}
        if not container.get('Id') or expected_id and container['Id'] != expected_id:
            raise ValueError('service container identity changed')
        expected_labels = {'com.docker.compose.project': self.project, 'com.docker.compose.service': name,
            'io.team-deploy.application': self.release['application'], 'io.team-deploy.service': name,
            'io.team-deploy.lifecycle': 'job' if job else spec['lifecycle']}
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise ValueError('service container ownership mismatch')
        if conf.get('Image') != spec['image']: raise ValueError('service image mismatch')
        expected_env, _ = service_values(spec, self.snapshot.values)
        environment = {}
        for item in conf.get('Env') or []:
            key, sep, value = item.partition('=')
            if not sep or key in environment: raise ValueError('ambiguous service environment')
            environment[key] = value
        if any(environment.get(key) != value for key, value in expected_env.items()):
            raise ValueError('service configuration differs from verified snapshot')
        expected_mounts = {target: ('volume', self.project+'-'+key, True) for key, target in spec['volumes'].items()}
        expected_mounts.update({target: ('bind', str(self.snapshot.bundles[name] / 'secrets' / key), False)
                               for key, target in spec['secret_files'].items()})
        mounts = container.get('Mounts') or []
        actual_mounts = {item.get('Destination'): (item.get('Type'), item.get('Name') if item.get('Type') == 'volume'
                         else item.get('Source'), item.get('RW')) for item in mounts}
        if len(actual_mounts) != len(mounts) or actual_mounts != expected_mounts:
            raise ValueError('service mount identity or permissions changed')
        host = container.get('HostConfig') or {}
        caps = {'CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETGID', 'SETUID'} if spec['permission_profile'] == 'database-init' else set()
        normalize_caps = lambda items: {value.removeprefix('CAP_') for value in items or []}
        if (host.get('Privileged') or normalize_caps(host.get('CapAdd')) != caps
                or normalize_caps(host.get('CapDrop')) != {'ALL'}
                or not any(value in ('no-new-privileges', 'no-new-privileges:true') for value in host.get('SecurityOpt') or [])):
            raise ValueError('service container permissions changed')
        memory = spec['resources']['memory_limit']
        expected_memory = int(memory[:-1]) * {'k':1024, 'm':1024**2, 'g':1024**3}[memory[-1].lower()]
        if host.get('Memory') != expected_memory or host.get('NanoCpus') != round(spec['resources']['cpus']*1000000000):
            raise ValueError('service resource limits changed')
        expected_ports = {}
        for endpoint in spec.get('endpoints', {}).values():
            key = str(endpoint['container_port'])+'/tcp'
            expected_ports.setdefault(key, []).append({'HostIp': endpoint['bind_address'], 'HostPort': str(endpoint['host_port'])})
        normalize_ports = lambda ports: {key: sorted(items, key=lambda item: (item['HostIp'], item['HostPort'])) for key, items in ports.items()}
        if normalize_ports(host.get('PortBindings') or {}) != normalize_ports(expected_ports):
            raise ValueError('service port bindings changed')
        networks = (container.get('NetworkSettings') or {}).get('Networks') or {}
        if set(networks) != {self.network} or name not in (networks[self.network].get('Aliases') or []):
            raise ValueError('service network identity changed')
        if 'command' in spec and conf.get('Cmd') != spec['command']:
            raise ValueError('service command changed')
        if 'exec' in spec.get('health', {}) and (conf.get('Healthcheck') or {}).get('Test') != ['CMD', *spec['health']['exec']]:
            raise ValueError('service health command changed')
        if job and (host.get('RestartPolicy') or {}).get('Name', 'no') not in ('no', ''):
            raise ValueError('one-shot job must not restart automatically')
        state = container.get('State') or {}; running = state.get('Running') is True
        ready = False
        if running:
            if job: ready = True
            elif 'exec' in spec['health']:
                ready = (state.get('Health') or {}).get('Status') == 'healthy'
            else:
                endpoint = next(iter(spec['endpoints'].values()))
                address = '127.0.0.1' if endpoint['bind_address'] == '0.0.0.0' else endpoint['bind_address']
                ready = http_ready(f"http://{address}:{endpoint['host_port']}{spec['health']['http']}")
        return {'container_id': container['Id'], 'running': running, 'healthy': ready}

    def healthy(self, name):
        deadline = time.monotonic() + self.config['services'][name]['health']['startup_timeout_seconds']
        while True:
            if self.observe(name)['healthy']: return self.container(name)
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
