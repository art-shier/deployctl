"""Remote Agent path for the existing disposable Linux Docker CI fixture."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from deployctl.node_agent import Agent, AgentDockerDriver, RuntimeExecutor, enroll, read_json
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials
from deployctl.runtime import Manager
from deployctl.release import build_release


def verify_remote_agent(owner, origin, base, application, deployment, image, root, config_root, *, runtime_logs=True):
    package = build_release(deployment, image, 'v1.0.1', base / 'remote-packages')
    owner.publish(application, 'v1.0.1', package)
    node = owner.json('POST', '/api/v1/node-enrollments', {})
    agent_home = base / 'remote-agent'
    node_id = enroll(origin, node['enrollment_token'], agent_home)
    discovered = next(n for n in owner.json('GET', '/api/v1/nodes') if n['id'] == node_id)
    assert discovered['pending_authorization']
    owner.json('PATCH', f'/api/v1/nodes/{node_id}', {'name': 'CI Docker node', 'enabled': True, 'groups': [],
        'projects': [application], 'excluded_projects': [], 'environments': ['prod']})
    identity = read_json(agent_home / 'node.json')
    client = PlatformClient(Credentials(identity['server'], identity['token']))
    manager = Manager(root, config_root, driver=AgentDockerDriver())
    worker = Agent(agent_home, node_id, client, RuntimeExecutor(manager, client))
    def logs(instance_id, **options):
        # Real HTTP relay and Agent query path, independently of deployment tasks.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(owner.json, 'POST',
                f'/api/v1/projects/{application}/instances/{instance_id}/logs',
                dict({'source': 'runtime', 'tail': 200}, **options))
            worker.poll_logs()
            result = future.result(timeout=30)
            assert not result['error'], result['error']
            return result

    for action, requested, expected in [('upgrade', 'v1.0.1', 'v1.0.1'), ('rollback', None, 'v1.0.0')]:
        body = {'node_id': node_id, 'environment': 'prod', 'action': action, 'request_id': uuid.uuid4().hex}
        if requested:
            body['version'] = requested
        task = owner.json('POST', f'/api/v1/projects/{application}/tasks', body)
        worker.run_once()
        worker.heartbeat()
        result = next(t for t in owner.json('GET', f'/api/v1/projects/{application}/tasks') if t['id'] == task['id'])
        assert result['status'] == 'succeeded', 'remote Docker operation did not succeed'
        instance = next(i for i in owner.json('GET', f'/api/v1/projects/{application}/instances') if i['node_id'] == node_id)
        assert instance['actual_version'] == expected and instance['health'] == 'healthy'
        state = json.loads((root / application / 'prod' / 'state.json').read_text())
        assert state['current']['version'] == expected and not state['transaction']
        process = logs(instance['id'], source='deployment', task_id=task['id'])
        assert 'Deployment succeeded' in process['text'], 'missing task process log'
        with worker.executor.runtime_lock:
            assert logs(instance['id'], source='deployment', task_id=task['id'])['text']
        if not runtime_logs:
            continue  # Portable protocol fixture simulates Docker; task logs are real.
        runtime = logs(instance['id'])
        assert 'started' in runtime['text'] and expected in runtime['text'], 'runtime log is not from current container'
        history = logs(instance['id'], source='archives')['sources']
        previous = 'v1.0.0' if action == 'upgrade' else 'v1.0.1'
        archived = next(item for item in history if item['version'] == previous)
        snapshot = logs(instance['id'], source='archive', archive_id=archived['id'])
        assert 'started' in snapshot['text'] and previous in snapshot['text'], 'old container output was not preserved'
        # Holding the runtime lock must not prevent the independent query path.
        with worker.executor.runtime_lock:
            assert logs(instance['id'])['text']
    print('PASS: Agent task upgrade, health observation, cached rollback and local CLI state compatibility')


def verify_remote_multiservice(owner, origin, base, application, image, root, config_root):
    """Actual task authorization, Agent execution and log relay for schema 3."""
    import copy, socket, subprocess
    from deployctl.runtime import project_name
    def docker(*args):
        return subprocess.run(['docker',*args],check=True,capture_output=True,text=True).stdout.strip()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    environment='multi'; project=project_name(application,environment)
    config={'schema_version':2,'application':application,'platform':'linux/amd64',
        'primary_endpoint':'api.http','volumes':{'data':{'retain':True}},'services':{
            'api':{'lifecycle':'application','image':image,'environment':{'APP_VERSION':{'literal':'v3.0.0'}},
                   'depends_on':{'cache':'healthy'},'endpoints':{'http':{'container_port':8080,'host_port':port,'bind_address':'127.0.0.1'}},
                   'health':{'http':'/health/ready'}},
            'cache':{'lifecycle':'persistent','image':image,'volumes':{'data':'/data'},
                     'command':['python','-u','-c','import time; print("cache-started"); time.sleep(3600)'],
                     'health':{'exec':['python','-c','pass']}}}}
    from deployctl.multiservice_contract import validate_multiservice_deployment
    validate_multiservice_deployment(config)
    node=owner.json('POST','/api/v1/node-enrollments',{})
    home=base/'multi-agent'; node_id=enroll(origin,node['enrollment_token'],home)
    owner.json('PATCH',f'/api/v1/nodes/{node_id}',{'name':'Multi CI node','enabled':True,'groups':[],
        'projects':[application],'excluded_projects':[],'environments':[environment]})
    owner.json('PUT',f'/api/v1/projects/{application}/environments/{environment}',
               {'expected_revision':0,'runtime_env':[],'install_params':[],'deployment_defaults':{}})
    identity=read_json(home/'node.json');client=PlatformClient(Credentials(identity['server'],identity['token']))
    manager=Manager(root,config_root,driver=AgentDockerDriver())
    worker=Agent(home,node_id,client,RuntimeExecutor(manager,client));worker.heartbeat()
    actual_node=next(n for n in owner.json('GET','/api/v1/nodes') if n['id']==node_id)
    assert 'multi_service_v1' in actual_node['capabilities']
    def container(name):
        return docker('ps','-aq','--no-trunc','--filter','label=com.docker.compose.project='+project,
                      '--filter','label=com.docker.compose.service='+name)
    try:
        for version in ('v3.0.0','v3.0.1'):
            source=copy.deepcopy(config);source['services']['api']['environment']['APP_VERSION']['literal']=version
            package=build_release(source,{'api':image,'cache':image},version,base/'multi-packages')
            owner.publish(application,version,package)
        retained=None
        for action,version in [('install','v3.0.0'),('upgrade','v3.0.1'),('rollback',None)]:
            body={'node_id':node_id,'environment':environment,'action':action,'request_id':uuid.uuid4().hex}
            if version:body['version']=version
            task=owner.json('POST',f'/api/v1/projects/{application}/tasks',body)
            worker.run_once();worker.heartbeat()
            report=next(t for t in owner.json('GET',f'/api/v1/projects/{application}/tasks') if t['id']==task['id'])
            assert report['status']=='succeeded', 'multi-service Agent task failed'
            instance=next(i for i in owner.json('GET',f'/api/v1/projects/{application}/instances') if i['node_id']==node_id)
            assert instance['health']=='healthy' and set(instance['stack']['services'])=={'api','cache'}
            if retained is None:
                retained=container('cache')
                docker('exec','--user','0',retained,'sh','-c','echo retained > /data/probe')
            assert container('cache')==retained
            assert docker('exec',retained,'cat','/data/probe')=='retained'
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(owner.json,'POST',f'/api/v1/projects/{application}/instances/{instance["id"]}/logs',
                                   {'source':'runtime','tail':200,'service':'cache'})
                worker.poll_logs();logs=future.result(timeout=30)
            assert not logs['error'] and 'cache-started' in logs['text']
        print('PASS: schema-3 remote Agent install/upgrade/rollback, capability gate, retained service and selected log relay')
    finally:
        ids=docker('ps','-aq','--filter','label=com.docker.compose.project='+project).split()
        if ids:docker('rm','-f',*ids)
        for kind in ('network','volume'):
            for name in docker(kind,'ls','--filter','label=io.team-deploy.project='+project,'--format','{{.Name}}').split():
                docker(kind,'rm',name)
