import json
import pytest
from deployctl.service_docker import ServiceDockerDriver
from test_service_plan import release


class Commands:
    def __init__(self): self.calls = []; self.docker_config = None
    def _run(self, command, environment=None, timeout=600):
        self.calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']: return 'linux/amd64'
        return ''
    def _stream_pull(self, command, environment=None): self.calls.append(command)


def driver(tmp_path):
    return ServiceDockerDriver(Commands(), tmp_path, 'lifelog-prod-test', release(), {}, {})


def test_targeted_up_never_recreates_dependencies_or_removes_orphans(tmp_path):
    d = driver(tmp_path); d.up('api')
    command = d.commands.calls[-1]
    assert command[-7:] == ['up', '-d', '--no-build', '--pull', 'never', '--no-deps', 'api']
    assert 'down' not in command and '--remove-orphans' not in command


def test_pull_checks_runtime_platform(tmp_path):
    d = driver(tmp_path); d.pull('postgres')
    assert d.commands.calls[0][:2] == ['docker', 'pull']
    assert d.commands.calls[1][:3] == ['docker', 'image', 'inspect']


def test_unowned_existing_volume_cannot_be_reused(tmp_path):
    d = driver(tmp_path)
    d.inspect = lambda kind, name: {'Name': name, 'CreatedAt': 'now', 'Labels': {}}
    with pytest.raises(ValueError, match='ownership'): d.ensure_resources()


def test_unsupported_platform_fails_before_up(tmp_path):
    d = driver(tmp_path)
    d.commands._run = lambda *args, **kwargs: 'linux/arm64'
    with pytest.raises(ValueError, match='platform'): d.pull('postgres')


def inspected_service(tmp_path, name='postgres'):
    from deployctl.stack_snapshot import create_stack_snapshot
    from test_stack_snapshot import full_release
    from test_service_plan import values
    from deployctl.service_plan import service_values
    snapshot = create_stack_snapshot(tmp_path, full_release(), values())
    d = ServiceDockerDriver(Commands(), snapshot.directory, 'lifelog-prod-test', snapshot.release,
                            snapshot.environment('lifelog-prod-test'), {})
    d.snapshot = snapshot
    spec = snapshot.release['deployment']['services'][name]
    env, _ = service_values(spec, snapshot.values)
    container = {'Id': 'owned-id', 'Config': {'Image': spec['image'], 'Env': [k+'='+v for k,v in env.items()],
                 'Labels': {'com.docker.compose.project': d.project, 'com.docker.compose.service': name,
                            'io.team-deploy.application': 'lifelog', 'io.team-deploy.service': name,
                            'io.team-deploy.lifecycle': spec['lifecycle']}},
                 'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                 'HostConfig': {'Privileged': False, 'CapAdd': None, 'CapDrop': ['ALL'],
                                'SecurityOpt': ['no-new-privileges:true'], 'Memory': 512*1024*1024,
                                'NanoCpus': 1000000000, 'PortBindings': {}},
                 'NetworkSettings': {'Networks': {d.network: {'Aliases': [name]}}}, 'Mounts': []}
    if 'exec' in spec['health']: container['Config']['Healthcheck'] = {'Test': ['CMD', *spec['health']['exec']]}
    for key, target in spec['volumes'].items():
        container['Mounts'].append({'Type': 'volume', 'Name': d.project+'-'+key, 'Destination': target, 'RW': True})
    for key, target in spec['secret_files'].items():
        container['Mounts'].append({'Type': 'bind', 'Source': str(snapshot.bundles[name]/'secrets'/key), 'Destination': target, 'RW': False})
    for e in spec['endpoints'].values():
        container['HostConfig']['PortBindings'][str(e['container_port'])+'/tcp'] = [{'HostIp':e['bind_address'], 'HostPort':str(e['host_port'])}]
    d.container = lambda service: container
    return d, container


def test_observe_matches_owned_container_and_stable_secret_mount(tmp_path):
    d, container = inspected_service(tmp_path)
    assert d.observe('postgres', expected_id='owned-id') == {'container_id': 'owned-id', 'running': True, 'healthy': True}


@pytest.mark.parametrize('change', ['identity', 'image', 'volume', 'secret', 'writable-secret', 'privileged', 'network', 'health', 'memory'])
def test_observe_rejects_runtime_drift_before_deployment(tmp_path, change):
    d, c = inspected_service(tmp_path)
    if change == 'identity': c['Id'] = 'replacement-id'
    if change == 'image': c['Config']['Image'] = 'postgres:latest'
    if change == 'volume': c['Mounts'][0]['Name'] = 'foreign-data'
    if change == 'secret': c['Mounts'][1]['Source'] = '/tmp/untrusted-password'
    if change == 'writable-secret': c['Mounts'][1]['RW'] = True
    if change == 'privileged': c['HostConfig']['Privileged'] = True
    if change == 'network': c['NetworkSettings']['Networks']['foreign-network'] = {}
    if change == 'health': c['Config']['Healthcheck']['Test'] = ['CMD', 'true']
    if change == 'memory': c['HostConfig']['Memory'] = 0
    with pytest.raises(ValueError): d.observe('postgres', expected_id='owned-id')


def test_observe_configuration_error_does_not_echo_secret(tmp_path):
    d, c = inspected_service(tmp_path, 'api')
    c['Config']['Env'] = ['DB_HOST=postgres', 'DB_PASSWORD=private-wrong-secret']
    with pytest.raises(ValueError) as exc: d.observe('api')
    assert 'private-wrong-secret' not in str(exc.value) and 'business-secret' not in str(exc.value)


def test_unregistered_owned_volume_is_not_adopted(tmp_path):
    d = driver(tmp_path)
    def existing(kind, name):
        return {'Name': name, 'Id': 'network-id', 'CreatedAt': 'old-volume-id', 'Labels': {
            'io.team-deploy.project': d.project, 'io.team-deploy.resource': 'network:app' if kind == 'network' else 'volume:data'}}
    d.inspect = existing
    d.inventory['network'] = {'name': d.network, 'identity': 'network-id'}
    with pytest.raises(ValueError, match='unregistered'): d.ensure_resources()
