"""Remote Agent path for the existing disposable Linux Docker CI fixture."""
import json
import uuid

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
    print('PASS: Agent task upgrade, health observation, cached rollback and local CLI state compatibility')
