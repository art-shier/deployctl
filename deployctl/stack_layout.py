"""Generic single-service to multi-service layout transition under upgrade.

No data restore policy: project programs own any initialization/validation jobs.
"""
import copy
import hashlib
import json
from .service_plan import service_values
from .stack_jobs import select_jobs


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def legacy_inventory(stack, state, *, allow_running=False, stop=False, expected=None):
    """An explicit legacy cutover may retain, never adopt, a stopped source."""
    from .deployment_state import normalize_state
    state = normalize_state(state, stack.app, stack.env)
    ref = state['current']
    if not ref or ref['legacy'] or state['transaction']:
        raise ValueError('layout transition requires an established snapshot and no pending operation')
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
    running=(resource.get('State') or {}).get('Running')
    if running is not False and not (running is True and (allow_running or stop)):
        raise ValueError('legacy application must be stopped before changing service layout')
    mounts = sorted(resource.get('Mounts') or [],key=lambda m:m.get('Destination',''))
    volumes = []
    for mount in mounts:
        if mount.get('Type') == 'volume':
            raw = json.loads(stack.manager.driver._run(['docker','volume','inspect',mount['Name']]))
            if len(raw)!=1 or not raw[0].get('CreatedAt'): raise ValueError('legacy source volume identity missing')
            volumes.append({'name':mount['Name'],'identity':raw[0]['CreatedAt']})
    observed={'container_id':resource['Id'], 'fingerprint':_hash({'config':conf,'host':resource.get('HostConfig'),
            'mounts':mounts,'volumes':volumes}), 'volumes':volumes}
    if stop:
        if observed!=expected: raise ValueError('legacy source identity changed before stop')
        if running:
            stack.manager.driver._run(['docker','stop','--time','30',resource['Id']])
            if legacy_inventory(stack,state)!=expected:
                raise ValueError('legacy source changed after stop')
    return observed


def plan_layout(stack, state, legacy, release, values, requested, overrides, management):
    if state['current'] or state['transaction']:
        raise ValueError('layout transition requires a stopped legacy installation')
    stack.manager.driver.check()
    observed = legacy_inventory(stack,legacy,allow_running=True)
    selection = select_jobs(release,{},values,requested)
    for spec in release['deployment']['services'].values(): service_values(spec,values)
    primary = release['deployment'].get('primary_endpoint')
    if not primary: raise ValueError('layout transition must preserve published endpoint')
    service,key=primary.split('.')
    endpoint=release['deployment']['services'][service]['endpoints'][key]
    if {'port':endpoint['host_port'],'address':endpoint['bind_address']} != state['binding']:
        raise ValueError('layout transition must preserve published endpoint')
    identity=_hash({'source':legacy,'observed':observed,'target':release,'values':values,
                    'selection':selection,'overrides':overrides,'management':management})
    return {'plan_id':identity,'generation':identity[:32],'version':release['version'],
            'legacy_source':observed,'retained_volumes':[v['name'] for v in observed['volumes']],
            'jobs':selection['active'],'order':selection['order']}


def apply_layout(stack,state,legacy,release,values,requested,overrides,management):
    from .stack_manager import StackManager,empty_stack_state
    from .stack_snapshot import create_stack_snapshot
    from .service_state import write_inventory
    plan=plan_layout(stack,state,legacy,release,values,requested,overrides,management)
    generation=plan['generation'];selection=select_jobs(release,{},values,requested)
    snapshot=create_stack_snapshot(stack.folder,release,values,overrides,management,
                                   active_jobs=selection['active'],generation=generation)
    home=stack.home/'layouts'/generation
    if (home/'state.json').exists(): raise ValueError('layout candidate exists; recover pending deployment')
    candidate=StackManager(stack.manager,home,stack.folder,stack.app,stack.env)
    target=empty_stack_state(stack.app,stack.env);target['generation']=generation
    driver=candidate.driver(snapshot,target)
    for name in selection['order']:driver.pull(name)
    write_inventory(home/'source-state.json',legacy)
    state['transaction']={'origin':None,'target':snapshot.ref,'journal':{
        'id':generation,'phase':'changing-layout','action':None,'rollback_barrier':True,
        'layout':{'plan_id':plan['plan_id'],'selection':selection,'source_sha256':_hash(legacy),
                  'observed':plan['legacy_source']}}}
    stack.save(state) # Intent before candidate resources, including abrupt exit.
    candidate.save(target)
    return resume_layout(stack,state)


def resume_layout(stack,state):
    from .stack_manager import StackManager,empty_stack_state
    from .runtime_snapshot import reject_links
    from .service_state import MAX_STATE
    tx=state['transaction'];meta=tx['journal']['layout'];generation=tx['journal']['id']
    home=stack.home/'layouts'/generation
    path=home/'source-state.json';reject_links(path)
    if not path.is_file() or path.stat().st_size>MAX_STATE:raise ValueError('legacy source audit missing')
    legacy=json.loads(path.read_text())
    if _hash(legacy)!=meta['source_sha256'] or legacy_inventory(stack,legacy,allow_running=True)!=meta['observed']:
        raise ValueError('legacy source changed during layout transition')
    # Durable intent already exists; stop only the verified old API container.
    # Its data and external companions remain untouched. Recovery repeats this
    # check safely if the process exits after Docker stops the container.
    if legacy_inventory(stack,legacy,stop=True,expected=meta['observed'])!=meta['observed']:
        raise ValueError('legacy source changed while stopping for upgrade')
    candidate=StackManager(stack.manager,home,stack.folder,stack.app,stack.env)
    if not (home/'state.json').exists():
        target=empty_stack_state(stack.app,stack.env);target['generation']=generation;candidate.save(target)
    target=stack.manager.state(home,stack.app,stack.env)
    snapshot=stack.load(tx['target'])
    if select_jobs(snapshot.release,{},snapshot.values,meta['selection']['active'])!=meta['selection']:
        raise ValueError('job selection changed from deployment snapshot')
    if target.get('generation')!=generation:raise ValueError('layout candidate generation mismatch')
    if target['current']!=snapshot.ref or target['transaction']:
        if target['transaction']:
            if target['transaction']['target']!=snapshot.ref:raise ValueError('layout target changed')
            candidate.reconcile_intended_resources(target,snapshot)
            candidate.apply(target,snapshot,meta['selection'],resuming=True)
        else:candidate.apply(target,snapshot,meta['selection'])
        target=stack.manager.state(home,stack.app,stack.env)
    observed=candidate.status(target)
    if not observed['services'] or any(not v['healthy'] for v in observed['services'].values()):
        raise ValueError('new service layout is not healthy')
    if target['transaction'] or target['current']!=snapshot.ref:raise ValueError('layout deployment incomplete')
    if legacy_inventory(stack,legacy)!=meta['observed']:raise ValueError('legacy source changed before promotion')
    target['previous']=None
    stack.save(target)
    return target
