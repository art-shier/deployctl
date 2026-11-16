"""Reviewed restoration into a fresh resource generation; never reuse old data."""
import copy
import hashlib
import json

from .multiservice_release import validate_multiservice_release
from .service_plan import service_values
from .stack_jobs import select_jobs, target_volumes
from .stack_snapshot import validate_stack_values


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def legacy_inventory(stack, state):
    """An explicit legacy cutover may retain, never adopt, a stopped source."""
    from .deployment_state import normalize_state
    state = normalize_state(state, stack.app, stack.env)
    ref = state['current']
    if not ref or ref['legacy'] or state['transaction']:
        raise ValueError('legacy restore requires an established snapshot and no pending operation')
    directory, release, snapshot, environment = stack.manager.reference_environment(stack.home, stack.folder, ref, stack.app)
    ids = stack.manager.driver.compose(directory, stack.project, environment, 'ps', '--all', '-q', 'app').splitlines()
    if len(ids) != 1: raise ValueError('legacy source container missing or ambiguous')
    resources = json.loads(stack.manager.driver._run(['docker','inspect',ids[0]]))
    if not isinstance(resources,list) or len(resources)!=1: raise ValueError('legacy source container ambiguous')
    resource = resources[0]; conf = resource.get('Config') or {}; labels = conf.get('Labels') or {}
    required = {'com.docker.compose.project':stack.project, 'com.docker.compose.service':'app',
                'io.team-deploy.application':stack.app, 'io.team-deploy.version':ref['version'],
                'io.team-deploy.configuration':ref['configuration']}
    if not resource.get('Id') or conf.get('Image') != release['image'] or any(labels.get(k)!=v for k,v in required.items()):
        raise ValueError('legacy source container identity mismatch')
    if (resource.get('State') or {}).get('Running') is not False:
        raise ValueError('legacy application must be stopped before planning restoration')
    mounts = sorted(resource.get('Mounts') or [],key=lambda m:m.get('Destination',''))
    volumes = []
    for mount in mounts:
        if mount.get('Type') == 'volume':
            raw = json.loads(stack.manager.driver._run(['docker','volume','inspect',mount['Name']]))
            if len(raw)!=1 or not raw[0].get('CreatedAt'): raise ValueError('legacy source volume identity missing')
            volumes.append({'name':mount['Name'],'identity':raw[0]['CreatedAt']})
    return {'container_id':resource['Id'], 'fingerprint':_hash({'config':conf,'host':resource.get('HostConfig'),
            'mounts':mounts,'volumes':volumes}), 'volumes':volumes}


def source_inventory(stack, state):
    """Allow absence after a disaster, but never accept changed ownership/identity."""
    result = {'services': {}, 'volumes': {}, 'network': None}
    for name, item in state['services'].items():
        driver = stack.driver(stack.load(item['snapshot']), state)
        info = driver.observe(name, expected_id=item['container_id'])
        result['services'][name] = info
    snapshot = stack.load(state['current'])
    driver = stack.driver(snapshot, state)
    for name, item in state['volumes'].items():
        resource = driver.inspect('volume', item['name'])
        if resource is not None:
            driver._owned(resource, 'volume', name)
            if resource.get('CreatedAt') != item['identity']:
                raise ValueError('source volume identity changed')
        result['volumes'][name] = dict(item, present=resource is not None)
    if state['network']:
        network = driver.inspect('network', state['network']['name'])
        if network is not None:
            driver._owned(network, 'network', 'app')
            if network.get('Id') != state['network']['identity']:
                raise ValueError('source network identity changed')
        result['network'] = network.get('Id') if network else None
    return result


def plan_restore(stack, state, release, values, sources, requested, overrides=None, management=None, legacy=None):
    if state['schema_version'] != 3 or (not state['current'] and legacy is None):
        raise ValueError('restore requires an established multi-service installation')
    if state['transaction']:
        raise ValueError('pending transaction requires recovery before a new restoration')
    release = validate_multiservice_release(release)
    if release['application'] != stack.app:
        raise ValueError('restore application mismatch')
    values = validate_stack_values(values)
    selection = select_jobs(release, {}, values, sources, requested)
    config = release['deployment']
    data_jobs = {name for name in selection['active'] if not config['jobs'][name]['rollback_compatible']}
    if not data_jobs:
        raise ValueError('isolated restore requires a data restoration job')
    covered = set()
    for name in data_jobs: covered.update(target_volumes(config, name))
    if set(config['volumes']) - covered:
        raise ValueError('restoration jobs must cover all persistent volumes')
    nodes = {**config['services'], **config['jobs']}
    def ancestors(name):
        found = set()
        def visit(key):
            for dep in nodes[key]['depends_on']:
                if dep not in found:
                    found.add(dep); visit(dep)
        visit(name)
        return found
    for name, spec in config['services'].items():
        service_values(spec, values)
        if spec['lifecycle'] == 'application' and not data_jobs <= ancestors(name):
            raise ValueError('application readiness must depend on every restoration job')
    if state['binding']:
        endpoint = config.get('primary_endpoint')
        if not endpoint: raise ValueError('restore must preserve the published endpoint')
        name, key = endpoint.split('.')
        endpoint = config['services'][name]['endpoints'][key]
        if {'port':endpoint['host_port'], 'address':endpoint['bind_address']} != state['binding']:
            raise ValueError('restore must preserve the published endpoint')
    stack.manager.driver.check()
    observed = legacy_inventory(stack, legacy) if legacy is not None else source_inventory(stack, state)
    digest = _hash({'state':state, 'observed':observed, 'release':release,
                    'values':values, 'selection':selection, 'overrides':overrides, 'management':management, 'legacy':legacy})
    result = {'plan_id':digest, 'generation':digest[:32], 'version':release['version'],
            'jobs':selection['active'], 'order':selection['order'],
            'retained_volumes':sorted(item['name'] for item in state['volumes'].values()),
            'source_services':observed.get('services',{})}
    if legacy is not None:
        result['legacy_source'] = observed
        result['retained_volumes'] = [item['name'] for item in observed['volumes']]
    return result


def restore_stack(stack, state, release, values, sources, requested, plan_id, overrides=None, management=None, legacy=None):
    from .stack_manager import StackManager, empty_stack_state
    from .stack_snapshot import create_stack_snapshot
    from .service_state import write_inventory
    plan = plan_restore(stack, state, release, values, sources, requested, overrides, management, legacy)
    if plan_id != plan['plan_id']:
        raise ValueError('restore plan changed or plan_id missing; preview again')
    generation = plan['generation']
    selection = select_jobs(release, {}, values, sources, requested)
    snapshot = create_stack_snapshot(stack.folder, release, values, overrides, management,
        active_jobs=selection['active'], generation=generation)
    candidate_home = stack.home / 'restorations' / generation
    candidate = StackManager(stack.manager, candidate_home, stack.folder, stack.app, stack.env)
    if (candidate_home / 'state.json').exists():
        raise ValueError('restoration candidate already exists; recover the pending operation')
    target = empty_stack_state(stack.app, stack.env)
    target['generation'] = generation
    # Pull before stopping source writers. No runtime resource is allocated yet.
    driver = candidate.driver(snapshot, target)
    for name in selection['order']: driver.pull(name)
    write_inventory(candidate_home / 'source-state.json', legacy if legacy is not None else state)
    state['transaction'] = {'origin':state['current'], 'target':snapshot.ref,
        'journal':{'id':generation, 'phase':'restoring', 'action':None, 'rollback_barrier':True,
                   'restore':{'plan_id':plan_id, 'selection':selection, 'stage':'freezing'}}}
    if legacy is not None:
        state['transaction']['journal']['restore']['legacy'] = {'state_sha256':_hash(legacy), 'observed':plan['legacy_source']}
    stack.save(state)
    candidate.save(target)
    return resume_restore(stack, state)


def resume_restore(stack, state):
    from .stack_manager import StackManager
    from .stack_jobs import JobDockerAdapter
    tx = state['transaction']; journal = tx['journal']; meta = journal['restore']
    generation = journal['id']
    candidate_home = stack.home / 'restorations' / generation
    candidate = StackManager(stack.manager, candidate_home, stack.folder, stack.app, stack.env)
    if not (candidate_home / 'state.json').exists():
        if meta['stage'] != 'freezing':
            raise ValueError('restoration candidate inventory missing')
        from .stack_manager import empty_stack_state
        target = empty_stack_state(stack.app,stack.env)
        target['generation'] = generation
        candidate.save(target)
    target = stack.manager.state(candidate_home, stack.app, stack.env)
    snapshot = stack.load(tx['target'])
    expected = select_jobs(snapshot.release, {}, snapshot.values, meta['selection']['sources'], meta['selection']['active'])
    if expected != meta['selection']:
        raise ValueError('restoration selection differs from frozen snapshot')
    if target.get('generation') != generation:
        raise ValueError('restoration candidate generation mismatch')
    if 'legacy' in meta:
        from .runtime_snapshot import reject_links
        from .service_state import MAX_STATE
        path = candidate_home / 'source-state.json'; reject_links(path)
        if not path.is_file() or path.stat().st_size > MAX_STATE: raise ValueError('legacy source audit missing')
        legacy = json.loads(path.read_text())
        if _hash(legacy) != meta['legacy']['state_sha256'] or legacy_inventory(stack,legacy) != meta['legacy']['observed']:
            raise ValueError('legacy source changed during restoration')
    if meta['stage'] == 'freezing':
        # Stop verified application writers first, then persistent services.
        # Missing resources are expected in disaster recovery; replacements are not.
        if 'legacy' not in meta: source_inventory(stack, state)
        for lifecycle in ('application', 'persistent'):
            for name, item in state['services'].items():
                if item['lifecycle'] != lifecycle: continue
                old = stack.driver(stack.load(item['snapshot']), state)
                old.freeze(name, item['container_id'])
        for name, item in state['jobs'].items():
            old = stack.driver(stack.load(item['snapshot']), state)
            adapter = JobDockerAdapter(old, name)
            observed = adapter.inspect_job(item['container'])
            if observed and observed['running']:
                adapter.stop_job(item['container'])
                if adapter.inspect_job(item['container'])['running']:
                    raise ValueError('source job writer did not stop')
        meta['stage'] = 'restoring'
        stack.save(state)
    if target['current'] != snapshot.ref or target['transaction']:
        if target['transaction']:
            if target['transaction']['target'] != snapshot.ref:
                raise ValueError('candidate transaction changed')
            # The frozen selection belongs to this freshly allocated generation.
            # JobRunner reconciles recorded executions and starts only jobs that
            # have no durable execution record; failed/unknown attempts refuse.
            candidate.reconcile_intended_resources(target, snapshot)
            candidate.apply(target, snapshot, meta['selection'], resuming=True)
        else:
            candidate.apply(target, snapshot, meta['selection'])
        target = stack.manager.state(candidate_home, stack.app, stack.env)
    observed = candidate.status(target)
    if not observed['services'] or any(not item['healthy'] for item in observed['services'].values()):
        raise ValueError('restored services are not healthy; promotion prohibited')
    if target['transaction'] or target['current'] != snapshot.ref:
        raise ValueError('restoration has not completed')
    target['previous'] = tx['origin']
    stack.save(target)
    return target
