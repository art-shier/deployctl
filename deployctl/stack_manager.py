"""Application-scoped multi-service transactions; caller holds the instance lock."""
import copy
import re
import math

from .contract import mapping, validate_name
from .multiservice_release import validate_multiservice_release
from .multiservice_runtime import ServiceExecutor
from .service_docker import ServiceDockerDriver
from .service_plan import plan_services, service_fingerprint
from .service_state import write_inventory
from .stack_snapshot import (create_stack_snapshot, load_stack_snapshot, validate_stack_ref,
                             validate_stack_values)


def empty_stack_state(app, env):
    return {'schema_version': 3, 'multi_service': True, 'application': app, 'environment': env,
            'current': None, 'previous': None, 'transaction': None, 'binding': None,
            'services': {}, 'volumes': {}, 'jobs': {}, 'network': None}


def _text(value, field, maximum=1024):
    if not isinstance(value, str) or not value or len(value) > maximum or '\x00' in value:
        raise ValueError('invalid '+field)


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('invalid inventory digest')


def _resource(item, present=False):
    fields = {'name','identity'} | ({'present'} if present else set())
    mapping(item, fields, fields, 'resource identity')
    _text(item['name'],'resource name'); _text(item['identity'],'resource identity')
    if present and type(item['present']) is not bool: raise ValueError('invalid resource presence')


def _job_record(item):
    fields = {'id','container','target','source','status','deadline_at','target_volumes','fingerprint','snapshot'}
    mapping(item, fields, fields, 'job record')
    _digest(item['id']); _digest(item['fingerprint']); _digest(item['source'])
    if item['container'] != 'job-'+item['id'][:32]: raise ValueError('invalid job container identity')
    _text(item['target'],'job target',128*1024)
    if item['status'] not in ('starting','running','succeeded','failed','unknown'): raise ValueError('invalid job status')
    deadline = item['deadline_at']
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline <= 0: raise ValueError('invalid job deadline')
    validate_stack_ref(item['snapshot'])
    if not isinstance(item['target_volumes'],dict) or len(item['target_volumes']) > 128: raise ValueError('invalid job volumes')
    for name, value in item['target_volumes'].items():
        validate_name(name,'volume'); _resource(value)


def _journal(value):
    if not isinstance(value,dict): raise ValueError('invalid transaction journal')
    phase = value.get('phase')
    phases = ('prepared','pulling','resources','job-running','job-succeeded','applying',
              'checking','checked','succeeded','rolling-back','rolled-back','recovery-required','stop','restart')
    if phase not in phases or type(value.get('rollback_barrier')) is not bool: raise ValueError('invalid transaction phase/barrier')
    action = value.get('action')
    if action is not None:
        mapping(action, {'kind','service'}, {'kind','service'}, 'journal action')
        if action['kind'] not in ('pull','ensure-resources','job','up','start','healthy','restore','stop','restart'):
            raise ValueError('invalid transaction action')
        if action['service'] is not None: validate_name(action['service'],'service')
    if phase in ('stop','restart'):
        mapping(value, {'phase','action','rollback_barrier'}, {'phase','action','rollback_barrier'}, 'operation journal')
        return
    fields = {'id','phase','action','previous','attempted','completed','jobs','rollback_barrier'}
    mapping(value,fields,fields,'deployment journal')
    if not isinstance(value['id'],str) or not re.fullmatch('[a-f0-9]{32}',value['id']): raise ValueError('invalid operation identity')
    for key in ('attempted','completed'):
        if not isinstance(value[key],list) or len(value[key]) > 128: raise ValueError('invalid journal service list')
        for name in value[key]: validate_name(name,'service')
        if len(set(value[key])) != len(value[key]): raise ValueError('duplicate journal service')
    if not isinstance(value['previous'],dict) or len(value['previous']) > 128: raise ValueError('invalid previous services')
    if not isinstance(value['jobs'],dict) or len(value['jobs']) > 128: raise ValueError('invalid journal jobs')
    for name,status in value['jobs'].items():
        validate_name(name,'job')
        if status not in ('unknown','succeeded'): raise ValueError('invalid journal job status')


def validate_stack_state(state, app, env):
    fields = set(empty_stack_state(app, env))
    mapping(state, fields, fields, 'stack state')
    if type(state['schema_version']) is not int or state['schema_version'] != 3 or state['multi_service'] is not True:
        raise ValueError('unsupported stack state')
    if state['application'] != app or state['environment'] != env:
        raise ValueError('stack state belongs to another application/environment')
    for key in ('current', 'previous'):
        if state[key] is not None: validate_stack_ref(state[key])
    for kind in ('services', 'volumes', 'jobs'):
        if not isinstance(state[kind], dict) or len(state[kind]) > 128:
            raise ValueError('invalid stack inventory')
        for name, item in state[kind].items():
            validate_name(name, kind)
            if not isinstance(item, dict): raise ValueError('invalid stack inventory record')
    for item in state['services'].values():
        fields = {'snapshot','lifecycle','fingerprint','container_id','running','healthy'}
        mapping(item,fields,fields,'service inventory')
        if type(item['running']) is not bool or type(item['healthy']) is not bool: raise ValueError('invalid service health')
        _digest(item['fingerprint'])
        validate_stack_ref(item.get('snapshot'))
        if item.get('lifecycle') not in ('application', 'persistent') or not re.fullmatch('[a-f0-9]{64}', item.get('fingerprint', '')):
            raise ValueError('invalid applied service reference')
        if item.get('container_id') is not None and not isinstance(item['container_id'], str):
            raise ValueError('invalid service container identity')
    if state['network'] is not None: _resource(state['network'])
    for item in state['volumes'].values(): _resource(item, present=True)
    for item in state['jobs'].values(): _job_record(item)
    if state['binding'] is not None:
        from .platform_client import validate_defaults
        mapping(state['binding'],{'port','address'},{'port','address'},'stack binding')
        validate_defaults({'host_port':state['binding']['port'],'bind_address':state['binding']['address']})
    if state['transaction'] is not None:
        tx = mapping(state['transaction'], {'target', 'origin', 'journal'}, {'target', 'origin', 'journal'}, 'stack transaction')
        validate_stack_ref(tx['target'])
        if tx['origin'] is not None: validate_stack_ref(tx['origin'])
        _journal(tx['journal'])
    return copy.deepcopy(state)


class StackManager:
    def __init__(self, manager, home, folder, app, env):
        from .runtime import project_name
        self.manager, self.home, self.folder = manager, home, folder
        self.app, self.env, self.project = app, env, project_name(app, env)

    def save(self, state):
        validate_stack_state(state, self.app, self.env)
        write_inventory(self.home / 'state.json', state)

    def driver(self, snapshot, state):
        driver = ServiceDockerDriver(self.manager.driver, snapshot.directory, self.project,
                                     snapshot.release, snapshot.environment(self.project), state)
        driver.snapshot = snapshot
        return driver

    def load(self, ref):
        return load_stack_snapshot(self.folder, ref, self.app)

    def observe(self, state, candidate):
        """Verify applied snapshots and resource identities before planning changes."""
        result = copy.deepcopy(state)
        driver = self.driver(candidate, state)
        if state['network']:
            network = driver.inspect('network', driver.network)
            if not network or network.get('Id') != state['network'].get('identity'):
                raise ValueError('established network missing or identity changed; recovery required')
            driver._owned(network, 'network', 'app')
        elif driver.inspect('network', driver.network) is not None:
            raise ValueError('unregistered network exists; explicit recovery required')
        for name in candidate.release['deployment']['volumes']:
            if name not in state['volumes'] and driver.inspect('volume', self.project+'-'+name) is not None:
                raise ValueError('unregistered volume exists; refusing adoption')
        for name, item in state['volumes'].items():
            actual_name = self.project+'-'+name
            if item.get('name') != actual_name: raise ValueError('volume name/ownership mismatch')
            resource = driver.inspect('volume', actual_name)
            if resource is None or resource.get('CreatedAt') != item.get('identity'):
                raise ValueError('established volume missing or identity changed; recovery required')
            driver._owned(resource, 'volume', name)
            result['volumes'][name]['present'] = True
        old_drivers = {}
        for name, item in state['services'].items():
            applied = self.load(item['snapshot'])
            old = self.driver(applied, state)
            if name not in applied.release['deployment']['services']:
                raise ValueError('service snapshot does not declare applied service')
            spec = applied.release['deployment']['services'][name]
            if service_fingerprint(spec, applied.values) != item['fingerprint']:
                raise ValueError('service fingerprint differs from applied snapshot')
            info = old.observe(name, expected_id=item.get('container_id'))
            if item['lifecycle'] == 'persistent' and item.get('container_id') and not info['container_id']:
                raise ValueError('persistent service container missing; recovery required')
            result['services'][name].update(info)
            old_drivers[name] = old
        for name in candidate.release['deployment']['services']:
            if name not in state['services'] and driver.container(name) is not None:
                raise ValueError('unregistered service container exists; refusing adoption')
        return result, driver, old_drivers

    def capture(self, state, target, driver, old_drivers):
        """Keep actual inventory, including dependencies created by failed installs."""
        for name in target.release['deployment']['services']:
            container = driver.container(name)
            if container is None:
                continue
            selected = driver
            try:
                info = driver.observe(name)
            except ValueError:
                selected = old_drivers.get(name)
                if selected is None: raise
                info = selected.observe(name)
            snapshot = selected.snapshot
            spec = snapshot.release['deployment']['services'][name]
            state['services'][name] = dict(info, lifecycle=spec['lifecycle'], snapshot=snapshot.ref,
                fingerprint=service_fingerprint(spec, snapshot.values))

    def apply(self, state, snapshot, selection=None):
        self.manager.driver.check()
        observed, driver, old_drivers = self.observe(state, snapshot)
        plan = plan_services(snapshot.release, observed, snapshot.values)
        from .stack_jobs import select_jobs, bind_job_runner
        selection = selection or select_jobs(snapshot.release, state, snapshot.values)
        plan['order'] = selection['order']
        if not plan['applicable']:
            raise ValueError('service plan requires explicit maintenance or recovery')
        driver.job_runner = bind_job_runner(driver, snapshot, state, selection, self.save)
        driver.rollback_drivers = old_drivers
        origin = copy.deepcopy(state['current'])
        baseline = copy.deepcopy(state['services'])
        barrier = bool((state.get('transaction') or {}).get('journal', {}).get('rollback_barrier'))

        def journal(value):
            if value['phase'] == 'checked':
                self.capture(state, snapshot, driver, old_drivers)
            state['transaction'] = {'target': snapshot.ref, 'origin': origin, 'journal': value}
            self.save(state)

        try:
            ServiceExecutor(driver, journal).apply(plan, snapshot.release['deployment']['services'],
                {name:snapshot.release['deployment']['jobs'][name] for name in selection['active']}, baseline, rollback_barrier=barrier)
            self.capture(state, snapshot, driver, old_drivers)
        except BaseException:
            # Do not pretend an interrupted/unknown deployment has rolled back.
            try:
                self.capture(state, snapshot, driver, old_drivers)
                phase = (state.get('transaction') or {}).get('journal', {}).get('phase')
                if phase == 'rolled-back': state['transaction'] = None
                self.save(state)
            except Exception as exc:
                raise RuntimeError('deployment inventory requires recovery; pending transaction retained') from exc
            raise
        if state['current'] != snapshot.ref:
            state['previous'] = state['current']
        state['current'] = snapshot.ref
        state['transaction'] = None
        config = snapshot.release['deployment']
        endpoint = config.get('primary_endpoint')
        if endpoint:
            name, key = endpoint.split('.')
            binding = config['services'][name]['endpoints'][key]
            state['binding'] = {'port': binding['host_port'], 'address': binding['bind_address']}
        else: state['binding'] = None
        self.save(state)
        return state

    def deploy(self, state, release, *, managed_configuration=None, updates=None, unset=None,
               defaults=None, port=None, bind=None, management=None, params=None, job_sources=None, requested_jobs=None, preview=False):
        if release['schema_version'] != 3:
            raise ValueError('legacy release cannot replace managed multi-service resources')
        if state['schema_version'] != 3:
            if state['current'] or state['previous']:
                raise ValueError('legacy installation requires explicit migration into a new managed stack')
            state = empty_stack_state(self.app, self.env)
        if params: raise ValueError('installation inputs require explicit job bindings')
        previous = self.load(state['current']) if state['current'] else None
        if managed_configuration is None:
            if preview:
                from .runtime import parse_env
                from .runtime_snapshot import reject_links
                pools = []
                for name in ('config.env', 'secrets.env'):
                    path = self.folder / name; reject_links(path)
                    if path.exists() and name == 'secrets.env' and path.stat().st_mode & 0o077:
                        raise ValueError('secrets.env must have permissions 600')
                    pools.append(parse_env(path) if path.exists() else {})
                config, secrets = pools
            else: config, secrets = self.manager.configuration(self.folder)
            values = {'config': config, 'secrets': secrets}
        else: values = validate_stack_values(managed_configuration)
        overrides = copy.deepcopy(previous.overrides) if previous else {}
        overrides.update(updates or {})
        for key in unset or []: overrides.pop(key, None)
        for key, value in overrides.items():
            is_secret = key in values['secrets'] or (previous is not None and key in previous.values['secrets'])
            if is_secret: values['config'].pop(key, None)
            values['secrets' if is_secret else 'config'][key] = value
        effective = copy.deepcopy(release)
        if state['binding']:
            if port is None: port = state['binding']['port']
            if bind is None: bind = state['binding']['address']
        config = effective['deployment']; defaults = defaults or {}
        if defaults or port is not None or bind is not None:
            if not config.get('primary_endpoint'):
                raise ValueError('endpoint/resource overrides require primary_endpoint')
            name, key = config['primary_endpoint'].split('.')
            endpoint = config['services'][name]['endpoints'][key]
            for field, value in [('host_port', port if port is not None else defaults.get('host_port')),
                                 ('bind_address', bind if bind is not None else defaults.get('bind_address'))]:
                if value is not None: endpoint[field] = value
            config['services'][name]['resources'].update({k:v for k,v in defaults.items() if k in ('cpus','memory_limit')})
        effective = validate_multiservice_release(effective)
        from .stack_jobs import select_jobs
        selection = select_jobs(effective, state, values, job_sources, requested_jobs)
        if preview:
            from .stack_snapshot import StackSnapshot
            nodes = {**effective['deployment']['services'],
                     **{name:effective['deployment']['jobs'][name] for name in selection['active']}}
            bundles = {name:self.folder / 'services' / name / service_fingerprint(spec, values) for name,spec in nodes.items()}
            snapshot = StackSnapshot({}, self.home / 'releases' / effective['version'], effective, values, bundles, overrides, management)
            self.manager.driver.check()
            observed, _, _ = self.observe(state, snapshot)
            plan = plan_services(effective, observed, values)
            plan.pop('fingerprints')
            plan.update(jobs={name:('run' if name in selection['active'] else 'skipped') for name in effective['deployment']['jobs']},
                        order=selection['order'], version=effective['version'])
            return plan
        snapshot = create_stack_snapshot(self.folder, effective, values, overrides, management, active_jobs=selection['active'])
        return self.apply(state, snapshot, selection)

    def recover(self, state, operation):
        from .stack_jobs import bind_job_runner, select_jobs, target_volumes, volume_identities
        tx = state['transaction']
        if (not isinstance(operation, str) or not re.fullmatch('[a-f0-9]{32}', operation)
                or not tx or tx['journal'].get('id') != operation):
            raise ValueError('recovery operation does not match pending transaction')
        snapshot = self.load(tx['target'])
        self.manager.driver.check()
        _, driver, _ = self.observe(state, snapshot)
        jobs = snapshot.release['deployment']['jobs']
        active, sources = [], {}
        for name in jobs:
            if name not in snapshot.bundles: continue
            prior = state['jobs'].get(name)
            if not prior:
                raise ValueError('job was not durably started; explicit recovery required')
            if prior.get('snapshot') != snapshot.ref or prior.get('fingerprint') != service_fingerprint(jobs[name], snapshot.values):
                raise ValueError('job snapshot identity changed; recovery required')
            targets = target_volumes(snapshot.release['deployment'], name)
            if prior.get('target_volumes') != volume_identities(state, targets):
                raise ValueError('job target volume identity changed; recovery required')
            if prior.get('status') not in ('starting', 'running', 'succeeded'):
                raise ValueError('job outcome requires verified recovery; automatic repeat prohibited')
            if prior['status'] != 'succeeded':
                active.append(name); sources[name] = prior['source']
        # JobRunner inspects an existing deterministic container; it cannot start
        # a new attempt because every selected job has a durable prior record.
        runner = bind_job_runner(driver, snapshot, state, {'active':active, 'sources':sources}, self.save)
        for name in active: runner(name)
        selection = select_jobs(snapshot.release, state, snapshot.values)
        if selection['active']:
            raise ValueError('unstarted jobs require explicit verified recovery')
        return self.apply(state, snapshot, selection)

    def rollback(self, state):
        tx = state['transaction']
        if tx and tx['journal'].get('rollback_barrier'):
            raise ValueError('data-changing job requires verified recovery; automatic rollback prohibited')
        target = tx['origin'] if tx else state['previous']
        if not target:
            raise ValueError('no previous stack snapshot; retained resources require explicit recovery')
        snapshot = self.load(target)
        return self.apply(state, snapshot)

    def status(self, state):
        target = state['transaction']['target'] if state['transaction'] else state['current']
        if not target: return copy.deepcopy(state)
        snapshot = self.load(target)
        observed, _, _ = self.observe(state, snapshot)
        return observed

    def operate(self, state, action, tail=100, service=None):
        import json
        if service is not None:
            validate_name(service, 'service')
            if service not in state['services']: raise ValueError('unknown managed service')
        if action == 'status':
            result = self.status(state)
            if service is not None: result['services'] = {service: result['services'][service]}
            return json.dumps(result, indent=2)
        if state['transaction'] and action != 'logs':
            raise RuntimeError('pending stack transaction; verified recovery required')
        if not state['current']:
            raise ValueError('application has no successful stack deployment')
        snapshot = self.load(state['current'])
        _, driver, old_drivers = self.observe(state, snapshot)
        names = [service] if service else list(state['services'])
        if action == 'logs':
            if type(tail) is not int or not 1 <= tail <= 10000: raise ValueError('log tail must be 1..10000')
            output = []
            for name in names:
                applied = old_drivers[name]
                text = applied.compose('logs', '--no-color', '--tail', str(tail), name)
                for value in applied.snapshot.values['secrets'].values():
                    if value: text = text.replace(value, '[REDACTED]')
                output.append(text)
            return '\n'.join(output)
        if action not in ('stop', 'restart'): raise ValueError('unsupported stack operation')
        if service and state['services'][service]['lifecycle'] == 'persistent':
            raise ValueError('persistent service operations require explicit maintenance')
        names = [name for name in names if state['services'][name]['lifecycle'] == 'application']
        from .multiservice_contract import dependency_order
        order = dependency_order(snapshot.release['deployment']['services'], snapshot.release['deployment']['jobs'])
        selected = [name for name in order if name in names]
        if action == 'stop': selected.reverse()
        for name in selected:
            state['transaction'] = {'origin': state['current'], 'target': state['current'],
                'journal': {'phase': action, 'action': {'kind': action, 'service': name}, 'rollback_barrier': False}}
            self.save(state)
            applied = old_drivers[name]
            if action == 'stop': applied.stop(name)
            else: applied.compose('restart', '--no-deps', name); applied.healthy(name)
        self.capture(state, snapshot, driver, old_drivers)
        state['transaction'] = None
        self.save(state)
        return ''
