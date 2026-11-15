"""One-shot scheduling with bootstrap completion bound to retained volume identity."""
import copy
import json
import re

from .contract import validate_name
from .multiservice_contract import dependency_order
from .service_plan import service_values, service_fingerprint
from .service_jobs import JobRunner


def target_volumes(config, name):
    nodes = {**config['services'], **config['jobs']}
    result, visited = set(), set()
    def visit(key):
        if key in visited: return
        visited.add(key); result.update(nodes[key]['volumes'])
        for dependency in nodes[key]['depends_on']: visit(dependency)
    visit(name)
    return sorted(result)


def volume_identities(state, names):
    return {name: {'name': state['volumes'][name]['name'], 'identity': state['volumes'][name]['identity']}
            for name in names}


def select_jobs(release, state, values, sources=None, requested=None):
    sources, requested = sources or {}, requested or []
    config = release['deployment']; jobs = config['jobs']
    if not isinstance(sources, dict) or not isinstance(requested, list) or any(not isinstance(name, str) for name in requested) or len(set(requested)) != len(requested):
        raise ValueError('invalid job selection')
    if set(sources) - set(jobs) or set(requested) - set(jobs): raise ValueError('unknown selected job')
    for name, source in sources.items():
        if not isinstance(source, str) or not re.fullmatch('[a-f0-9]{64}', source):
            raise ValueError('job source identity must be a verified SHA256')
    active, succeeded, identities = [], set(), {}
    order = dependency_order(config['services'], jobs)
    for name in order:
        if name not in jobs: continue
        spec = jobs[name]; prior = state.get('jobs', {}).get(name); targets = target_volumes(config, name)
        if prior:
            if prior.get('status') != 'succeeded':
                raise ValueError('job requires verified recovery; automatic repeat prohibited')
            try: current = volume_identities(state, targets)
            except KeyError as exc: raise ValueError('completed job volume missing; recovery required') from exc
            if current != prior.get('target_volumes'):
                raise ValueError('completed job volume identity changed; recovery required')
            if name in requested:
                raise ValueError('completed job cannot be repeated without explicit verified recovery')
            # A completed bootstrap does not rerun just because the next app image
            # contains a newer restore executable or old bootstrap keys were removed.
            if spec['trigger'] == 'explicit' and prior.get('fingerprint') != service_fingerprint(spec, values):
                continue
            succeeded.add(name)
            continue
        if spec['trigger'] == 'explicit' and name not in requested: continue
        service_values(spec, values)
        if not spec['rollback_compatible']:
            if name not in sources: raise ValueError('data-changing job requires verified source identity')
            if not targets or any(volume in state.get('volumes', {}) for volume in targets):
                raise ValueError('bootstrap data job requires a fresh isolated target volume')
        identities[name] = sources.get(name, service_fingerprint(spec, values))
        active.append(name)
    enabled = set(config['services']) | set(active)
    for name in enabled:
        spec = config['services'].get(name) or jobs[name]
        for dependency in spec['depends_on']:
            if dependency not in enabled and dependency not in succeeded:
                raise ValueError('dependency requires an explicit verified job')
    return {'active': active, 'sources': identities, 'order': [name for name in order if name in enabled]}


class JobDockerAdapter:
    def __init__(self, driver, job):
        self.driver, self.job = driver, job
        self.spec = driver.config['jobs'][job]

    def name(self, execution):
        if not isinstance(execution, str) or not re.fullmatch(r'job-[a-f0-9]{32}', execution):
            raise ValueError('invalid job execution identity')
        return self.driver.project+'-'+execution

    def inspect_job(self, execution):
        resource = self.driver.inspect('container', self.name(execution))
        if resource is None: return None
        conf = resource.get('Config') or {}; labels = conf.get('Labels') or {}
        required = {'com.docker.compose.project': self.driver.project, 'com.docker.compose.service': self.job,
                    'io.team-deploy.lifecycle': 'job', 'io.team-deploy.execution': execution}
        if any(labels.get(key) != value for key, value in required.items()) or conf.get('Image') != self.spec['image']:
            raise ValueError('job container identity mismatch')
        self.driver.verify_container(self.job, resource, job=True)
        state = resource.get('State') or {}
        code = state.get('ExitCode') if state.get('Status') == 'exited' else None
        return {'running': state.get('Running') is True, 'exit_code': code}

    def start_job(self, execution, spec):
        if spec != self.spec: raise ValueError('job specification changed')
        # Raw data-file restore must never share a mounted volume with a running database.
        for name, service in self.driver.config['services'].items():
            if set(service['volumes']) & set(spec['volumes']):
                container = self.driver.container(name)
                if container and (container.get('State') or {}).get('Running'):
                    raise ValueError('job volume has an active service writer')
        self.driver.compose('run', '--detach', '--no-deps', '--pull', 'never', '-T', '--use-aliases',
                            '--name', self.name(execution), '--label', 'io.team-deploy.execution='+execution, self.job)

    def stop_job(self, execution):
        # Verify ownership before stopping a process; the runner verifies exit.
        if self.inspect_job(execution) is None: raise ValueError('job container missing')
        self.driver.commands._run(['docker', 'stop', '--time', '10', self.name(execution)], self.driver.environment)


def bind_job_runner(driver, snapshot, state, selection, save):
    def run(name):
        if name not in selection['active']: raise ValueError('job was not selected')
        config = snapshot.release['deployment']; spec = config['jobs'][name]
        targets = volume_identities(state, target_volumes(config, name))
        identity = json.dumps({'project': driver.project, 'volumes': targets}, sort_keys=True)
        fingerprint = service_fingerprint(spec, snapshot.values)
        records = copy.deepcopy(state['jobs'])
        def checkpoint(value):
            value[name].update(target_volumes=targets, fingerprint=fingerprint, snapshot=snapshot.ref)
            state['jobs'] = copy.deepcopy(value)
            save(state)
        runner = JobRunner(JobDockerAdapter(driver, name), records, checkpoint)
        return runner.run(name, spec, identity, selection['sources'][name], fingerprint)
    return run
