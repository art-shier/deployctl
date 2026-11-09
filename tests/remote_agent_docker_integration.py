"""Remote Agent path for the existing disposable Linux Docker CI fixture."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from deployctl.node_agent import Agent, AgentDockerDriver, RuntimeExecutor, enroll, read_json
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials
from deployctl.runtime import Manager
from deployctl.release import build_release


def verify_remote_agent(owner, origin, base, application, deployment, image, root, config_root):
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
        runtime = logs(instance['id'])
        assert 'started' in runtime['text'] and expected in runtime['text'], 'runtime log is not from current container'
        process = logs(instance['id'], source='deployment', task_id=task['id'])
        assert 'Deployment succeeded' in process['text'], 'missing task process log'
        history = logs(instance['id'], source='archives')['sources']
        previous = 'v1.0.0' if action == 'upgrade' else 'v1.0.1'
        archived = next(item for item in history if item['version'] == previous)
        snapshot = logs(instance['id'], source='archive', archive_id=archived['id'])
        assert 'started' in snapshot['text'] and previous in snapshot['text'], 'old container output was not preserved'
        # Holding the runtime lock must not prevent the independent query path.
        with worker.executor.runtime_lock:
            assert logs(instance['id'])['text']
    print('PASS: Agent task upgrade, health observation, cached rollback and local CLI state compatibility')
