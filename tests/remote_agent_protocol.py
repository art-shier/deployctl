"""Invoked by Go httptest with private stdin identity; all Docker calls simulated."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployctl.node_agent import Agent, RuntimeExecutor, enroll, fingerprint, read_json, write_json
from deployctl.platform_client import PlatformClient, PlatformError
from deployctl.platform_credentials import Credentials
from deployctl.release import build_release
from deployctl.contract import read_yaml
from deployctl.runtime import Manager


class DockerFixture:
    docker_config = None

    def __init__(self):
        self.active = None
        self.deployments = 0
        self.fail = set()

    def check(self):
        pass

    def pull(self, directory, project, environment):
        # Exercise the real task-specific registry token response/temporary file.
        assert self.docker_config
        config = json.loads((Path(self.docker_config) / 'config.json').read_text())
        assert config['auths']['registry.example']['registrytoken'].count('.') == 2

    def restore_pull(self, *args):
        pass

    def up(self, directory, project, environment):
        self.deployments += 1
        release = read_yaml(directory / 'release.yaml')
        self.active = {'image': release['image'], 'running': True, 'labels': {
            'io.team-deploy.version': release['version'],
            'io.team-deploy.application': release['application'],
            'io.team-deploy.configuration': environment.get('DEPLOYCTL_CONFIGURATION')}}

    def probe(self, directory, project, environment, release, binding):
        if release['version'] in self.fail:
            raise RuntimeError('fixture health failure')

    def inspect_container(self, *args):
        return self.active

    def down(self, *args):
        self.active = None

    def operate(self, *args):
        return ''


def main():
    identity = json.load(sys.stdin)
    owner = PlatformClient(Credentials(identity['origin'], identity['token']))
    owner.json('POST', '/api/v1/projects', {'slug': 'notes', 'name': 'Notes', 'group': 'default',
        'default_environment': 'prod', 'image_repository': 'registry.example/notes'})
    owner.json('PUT', '/api/v1/projects/notes/environments/prod', {'expected_revision': 1,
        'runtime_env': [{'key': 'SECRET', 'operation': 'set', 'secret': True, 'value': 'frozen-fixture'}]})
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        config = {'schema_version': 1, 'application': 'notes', 'container': {'port': 8080},
                  'health': {'readiness_path': '/ready', 'startup_timeout_seconds': 1}, 'required_config': ['SECRET']}
        image = 'registry.example/notes@sha256:' + 'a' * 64
        for version in ('v1.0.0', 'v1.1.0', 'v1.2.0'):
            package = build_release(config, image, version, base / 'packages')
            owner.publish('notes', version, package, channel='stable' if version == 'v1.0.0' else None)

        nodes = []
        for index in range(2):
            created = owner.json('POST', '/api/v1/nodes', {'name': f'node-{index}', 'groups': ['default'],
                'projects': [], 'excluded_projects': [], 'environments': ['prod']})
            home = base / f'node-{index}'
            node_id = enroll(identity['origin'], created['enrollment_token'], home)
            credential = read_json(home / 'node.json')
            client = PlatformClient(Credentials(credential['server'], credential['token']))
            driver = DockerFixture()
            manager = Manager(base / f'apps-{index}', base / f'config-{index}', driver=driver)
            runner = Agent(home, node_id, client, RuntimeExecutor(manager, client))
            runner.heartbeat()
            nodes.append((node_id, client, runner, driver, manager))

        def submit(index, action='install', version=None):
            request = {'node_id': nodes[index][0], 'environment': 'prod', 'action': action, 'request_id': uuid.uuid4().hex}
            if version:
                request['version'] = version
            first = owner.json('POST', '/api/v1/projects/notes/tasks', request)
            second = owner.json('POST', '/api/v1/projects/notes/tasks', request)
            assert first['id'] == second['id']
            return first

        first = submit(0)
        second = submit(1)
        assert first['instance_id'] != second['instance_id']
        owner.json('PUT', '/api/v1/projects/notes/environments/prod', {'expected_revision': 2,
            'runtime_env': [{'key': 'SECRET', 'operation': 'set', 'value': 'changed-after-queue'}]})
        with patch('deployctl.node_agent.http_ready', return_value=True):
            for _, client, runner, driver, manager in nodes:
                runner.run_once()
                runner.heartbeat()
                home, folder = manager.paths('notes', 'prod')
                state = manager.state(home, 'notes', 'prod')
                snapshot = folder / 'runtime' / state['current']['configuration'] / '.env.json'
                assert json.loads(snapshot.read_text())['SECRET'] == 'frozen-fixture'
                assert driver.deployments == 1
            instances = owner.json('GET', '/api/v1/projects/notes/instances')
            assert len(instances) == 2 and all(i['actual_version'] == 'v1.0.0' for i in instances)
            assert all(i['health'] == 'healthy' for i in instances)
            listed = owner.json('GET', '/api/v1/projects/notes/tasks')
            assert all(t['status'] == 'succeeded' for t in listed)
            assert 'frozen-fixture' not in json.dumps(listed)

            upgrade = submit(0, 'upgrade', 'v1.1.0')
            nodes[0][2].run_once()
            listed = owner.json('GET', '/api/v1/projects/notes/instances')
            versions = {i['node_id']: i['actual_version'] for i in listed}
            assert versions == {nodes[0][0]: 'v1.1.0', nodes[1][0]: 'v1.0.0'}
            rollback = submit(0, 'rollback')
            nodes[0][2].run_once()
            listed = owner.json('GET', '/api/v1/projects/notes/instances')
            assert all(i['actual_version'] == 'v1.0.0' for i in listed)

            failed = submit(0, 'upgrade', 'v1.2.0')
            nodes[0][3].fail.add('v1.2.0')
            nodes[0][2].run_once()
            listed = owner.json('GET', '/api/v1/projects/notes/tasks')
            result = next(t for t in listed if t['id'] == failed['id'])
            assert result['status'] == 'failed'
            assert result['result']['observation']['actual_version'] == 'v1.0.0'

            recovery = submit(0, 'upgrade', 'v1.1.0')
            lease = nodes[0][1].json('POST', '/api/v1/node/poll', {'wait_seconds': 0})['task']
            path = nodes[0][2].journals / (lease['id'] + '.json')
            write_json(path, {'id': lease['id'], 'node_id': nodes[0][0], 'instance_id': lease['instance_id'], 'fingerprint': fingerprint(lease),
                'status': 'starting', 'stage': 'accepted', 'reported': False})
            restarted = Agent(nodes[0][2].home, nodes[0][0], nodes[0][1], nodes[0][2].executor)
            before = nodes[0][3].deployments
            restarted.flush_reports()
            assert nodes[0][3].deployments == before
            result = next(t for t in owner.json('GET', '/api/v1/projects/notes/tasks') if t['id'] == recovery['id'])
            assert result['status'] == 'needs_attention'

            cross = submit(0, 'upgrade', 'v1.1.0')
            nodes[0][1].json('POST', '/api/v1/node/poll', {'wait_seconds': 0})
            try:
                nodes[1][1].request('GET', f"/api/v1/node/tasks/{cross['id']}/package")
                raise AssertionError('cross-node artifact accepted')
            except PlatformError as error:
                assert error.status in (403, 404)
            owner.json('POST', f"/api/v1/projects/notes/tasks/{cross['id']}/cancel", {})
            # Also exercise the Linux CI helper's orchestration here. Its Docker
            # adapter remains simulated in this portable protocol test only.
            from remote_agent_docker_integration import verify_remote_agent
            with patch('remote_agent_docker_integration.AgentDockerDriver', DockerFixture):
                verify_remote_agent(owner, identity['origin'], base / 'ci-helper', 'notes', config, image,
                                    nodes[0][4].root, nodes[0][4].config_root)
        print('PASS: real Go/PostgreSQL/Python protocol; enrollment, frozen configs, task-scoped package/registry, two-node state, upgrade, rollback, failure recovery, crash journal and cross-node denial (Docker simulated)')


if __name__ == '__main__':
    main()
