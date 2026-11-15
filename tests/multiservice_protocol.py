"""Real Go/SQL/Python multi-service delivery protocol; no container execution."""
import json
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployctl.node_agent import RuntimeExecutor, enroll, read_json, validate_task
from deployctl.platform_client import PlatformClient, PlatformError
from deployctl.platform_credentials import Credentials


def main():
    identity = json.load(sys.stdin)
    owner = PlatformClient(Credentials(identity['origin'], identity['token']))
    owner.json('POST', '/api/v1/projects', {'slug': 'lifelog', 'name': 'Lifelog', 'group': 'default',
        'default_environment': 'prod', 'image_repository': 'registry.example/lifelog'})
    owner.json('PUT', '/api/v1/projects/lifelog/environments/prod', {'expected_revision': 1,
        'runtime_env': [{'key': 'DB_HOST', 'operation': 'set', 'value': 'postgres'},
                        {'key': 'DB_PASSWORD', 'operation': 'set', 'secret': True, 'value': 'frozen-test-secret'}]})
    package = Path(__file__).resolve().parents[1] / 'control-server/internal/artifacts/testdata/multiservice/lifelog-v2.0.1.tar.gz'
    published = owner.publish('lifelog', 'v2.0.1', package, channel='stable')
    assert len(published['images']) == 3
    with tempfile.TemporaryDirectory() as temp, patch('deployctl.platform_client.__version__', '1.20.0'):
        root = Path(temp)
        resolved = owner.resolve('lifelog', 'prod')
        assert resolved['configuration']['secrets'] == {'DB_PASSWORD': 'frozen-test-secret'}
        assert resolved['configuration']['config'] == {'DB_HOST': 'postgres'}
        assert owner.download_release(resolved, root).read_bytes() == package.read_bytes()
        owner.recover_release('lifelog', 'v2.0.1', root / 'recovered')
        enrollment = owner.json('POST', '/api/v1/node-enrollments', {})
        home = root / 'node'
        node = enroll(identity['origin'], enrollment['enrollment_token'], home)
        owner.json('PATCH', f'/api/v1/nodes/{node}', {'name': 'protocol-node', 'enabled': True,
            'groups': ['default'], 'projects': [], 'excluded_projects': [], 'environments': ['prod']})
        credentials = read_json(home / 'node.json')
        client = PlatformClient(Credentials(credentials['server'], credentials['token']))
        request = {'node_id': node, 'environment': 'prod', 'action': 'install', 'request_id': uuid.uuid4().hex}
        heartbeat = {'agent_version': '1.20.0', 'platform': 'linux', 'docker_available': True, 'instances': []}
        client.json('POST', '/api/v1/node/heartbeat', heartbeat)
        try:
            owner.json('POST', '/api/v1/projects/lifelog/tasks', request)
            raise AssertionError('version alone must not advertise multi-service support')
        except PlatformError as exc:
            assert exc.status == 403
        heartbeat['capabilities'] = ['multi_service_v1']
        client.json('POST', '/api/v1/node/heartbeat', heartbeat)
        submitted = owner.json('POST', '/api/v1/projects/lifelog/tasks', request)
        owner.json('PUT', '/api/v1/projects/lifelog/environments/prod', {'expected_revision': 2,
            'runtime_env': [{'key': 'DB_PASSWORD', 'operation': 'set', 'value': 'later-test-secret'}]})
        task = client.json('POST', '/api/v1/node/poll', {'wait_seconds': 0})['task']
        validate_task(task, node)
        assert task['id'] == submitted['id']
        assert task['resolution']['configuration']['secrets']['DB_PASSWORD'] == 'frozen-test-secret'
        client.json('POST', f"/api/v1/node/tasks/{task['id']}/start", {})
        assert client.download_release(task['resolution'], root).read_bytes() == package.read_bytes()
        runtime = object.__new__(RuntimeExecutor); runtime.client = client
        with runtime.registry_config(task) as folder:
            credentials = json.loads((Path(folder) / 'config.json').read_text())
            assert credentials['auths']['registry.example']['registrytoken'].count('.') == 2
        assert not Path(folder).exists()
        listed = owner.json('GET', '/api/v1/projects/lifelog/tasks')
        assert 'frozen-test-secret' not in json.dumps(listed)
    print('PASS: multi-service publish/resolve/recover, capability gate, frozen secret classification, task validation, package checksum and scoped registry token; no Docker execution')


if __name__ == '__main__':
    main()
