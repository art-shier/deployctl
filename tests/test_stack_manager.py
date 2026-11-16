"""Real Manager/filesystem/planner/executor; only the Docker engine is simulated."""
import copy
from pathlib import Path
from unittest.mock import patch
import pytest
from deployctl.runtime import Manager
from deployctl.release import build_release
from test_multiservice_contract import source, IMAGE
from test_multiservice_release import API_IMAGE
from test_service_plan import values


class Engine:
    def __init__(self):
        self.containers = {}; self.volumes = {}; self.calls = []; self.fail = None; self.serial = 0; self.network = None
    def check(self): pass


class Adapter:
    def __init__(self, commands, directory, project, release, environment, inventory):
        self.engine = commands; self.release = release; self.config = release['deployment']; self.inventory = inventory
        self.project = project; self.network = project+'-internal'; self.rollback_drivers = {}; self.snapshot = None
    def inspect(self, kind, name):
        if kind == 'volume': return self.engine.volumes.get(name)
        if kind == 'network': return self.engine.network
        raise AssertionError(kind)
    def _owned(self, resource, kind, name):
        assert resource['Labels']['io.team-deploy.project'] == self.project
    def container(self, name): return self.engine.containers.get(name)
    def observe(self, name, expected_id=None):
        c = self.container(name)
        if c is None: return {'running': False, 'healthy': False, 'container_id': None}
        if expected_id and c['Id'] != expected_id: raise ValueError('container identity changed')
        if c['image'] != self.config['services'][name]['image']: raise ValueError('container image changed')
        if c['bundle'] != str(self.snapshot.bundles[name]): raise ValueError('configuration mount changed')
        return {'running': c['running'], 'healthy': c['running'], 'container_id': c['Id']}
    def pull(self, name): self.engine.calls.append(('pull', name))
    def ensure_resources(self):
        self.engine.network = {'Id':'network-id','Labels':{'io.team-deploy.project':self.project,'io.team-deploy.resource':'network:app'}}
        self.inventory['network'] = {'name': self.network, 'identity': 'network-id'}
        for name in self.config['volumes']:
            actual = self.project+'-'+name
            if actual not in self.engine.volumes:
                self.engine.volumes[actual] = {'Name': actual, 'CreatedAt': 'volume-id', 'Labels': {'io.team-deploy.project': self.project, 'io.team-deploy.resource': 'volume:'+name}}
            self.inventory['volumes'][name] = {'name': actual, 'identity': self.engine.volumes[actual]['CreatedAt'], 'present': True}
    def up(self, name):
        self.engine.calls.append(('up', name)); self.engine.serial += 1
        self.engine.containers[name] = {'Id': f'container-{self.engine.serial}', 'image': self.config['services'][name]['image'], 'bundle': str(self.snapshot.bundles[name]), 'running': True}
        if self.engine.fail == self.release['version'] and name == 'api': raise RuntimeError('simulated API startup failure')
    def start(self, name):
        self.engine.calls.append(('start', name)); self.engine.containers[name]['running'] = True
    def healthy(self, name):
        assert self.observe(name)['healthy']
        return self.container(name)
    def stop(self, name): self.engine.calls.append(('stop', name)); self.engine.containers[name]['running'] = False
    def run_job(self, name): return self.job_runner(name)
    def restore(self, name, previous):
        old = self.rollback_drivers[name];old.up(name);old.healthy(name)


def package(tmp_path, version='v2.0.0', api_image=API_IMAGE):
    return build_release(source(), {'api': api_image, 'postgres': IMAGE}, version, tmp_path / 'packages')


@pytest.fixture
def runtime(tmp_path):
    engine = Engine(); manager = Manager(tmp_path/'apps', tmp_path/'config', driver=engine)
    with patch('deployctl.stack_manager.ServiceDockerDriver', Adapter):
        yield manager, engine


def install(manager, package, **kwargs):
    return manager.deploy('lifelog', 'prod', package, managed_configuration=values(), **kwargs)


def test_manager_install_upgrade_and_rollback_preserve_database(runtime, tmp_path):
    manager, engine = runtime
    first = install(manager, package(tmp_path))
    pg = copy.deepcopy(engine.containers['postgres']); volumes = copy.deepcopy(engine.volumes)
    second = install(manager, package(tmp_path, 'v2.1.0', 'registry.example/api@sha256:'+'c'*64), upgrade=True)
    assert second['current']['version'] == 'v2.1.0'
    assert engine.containers['postgres'] == pg and engine.volumes == volumes
    restored = manager.rollback('lifelog', 'prod')
    assert restored['current']['version'] == 'v2.0.0'
    assert engine.containers['api']['image'] == API_IMAGE
    assert engine.containers['postgres'] == pg and engine.volumes == volumes
    assert [call for call in engine.calls if call == ('up', 'postgres')] == [('up', 'postgres')]


def test_failed_upgrade_restores_only_api_and_clears_completed_rollback(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path)); pg = copy.deepcopy(engine.containers['postgres'])
    engine.fail = 'v2.1.0'
    with pytest.raises(RuntimeError, match='startup'):
        install(manager, package(tmp_path, 'v2.1.0', 'registry.example/api@sha256:'+'c'*64), upgrade=True)
    home, _ = manager.paths('lifelog', 'prod'); state = manager.state(home, 'lifelog', 'prod')
    assert state['current']['version'] == 'v2.0.0' and state['transaction'] is None
    assert engine.containers['api']['image'] == API_IMAGE and engine.containers['postgres'] == pg


def test_missing_volume_blocks_upgrade_before_any_container_changes(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path)); engine.volumes.clear(); calls = list(engine.calls)
    with pytest.raises(ValueError, match='volume'):
        install(manager, package(tmp_path, 'v2.1.0'), upgrade=True)
    assert engine.calls == calls


def test_persistent_secret_change_is_maintenance_not_automatic_recreation(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path)); calls = list(engine.calls)
    changed = values(); changed['secrets']['pg_password'] = 'new-password'
    with pytest.raises(ValueError, match='maintenance'):
        manager.deploy('lifelog', 'prod', package(tmp_path, 'v2.1.0'), upgrade=True, managed_configuration=changed)
    assert engine.calls == calls


def test_failed_first_install_keeps_new_database_inventory(runtime, tmp_path):
    manager, engine = runtime; engine.fail = 'v2.0.0'
    with pytest.raises(RuntimeError): install(manager, package(tmp_path))
    home, _ = manager.paths('lifelog', 'prod'); state = manager.state(home, 'lifelog', 'prod')
    assert state['current'] is None and state['transaction'] is None
    assert state['services']['postgres']['container_id'] == engine.containers['postgres']['Id']
    assert state['volumes']['data']['identity'] == 'volume-id'
    assert engine.containers['postgres']['running']


def test_existing_multi_service_state_cannot_enter_legacy_deployer(runtime, tmp_path):
    from test_release import CONFIG
    manager, engine = runtime; install(manager, package(tmp_path)); calls = list(engine.calls)
    config = copy.deepcopy(CONFIG); config['application'] = 'lifelog'
    old = build_release(config, API_IMAGE, 'v1.0.0', tmp_path/'legacy')
    with pytest.raises(ValueError, match='legacy'):
        install(manager, old, upgrade=True)
    assert engine.calls == calls


def test_stack_status_and_application_stop_preserve_database(runtime, tmp_path):
    import json
    manager, engine = runtime; install(manager, package(tmp_path)); pg = copy.deepcopy(engine.containers['postgres'])
    status = json.loads(manager.operate('lifelog', 'prod', 'status'))
    assert status['services']['postgres']['healthy'] is True
    manager.operate('lifelog', 'prod', 'stop')
    assert engine.containers['api']['running'] is False and engine.containers['postgres'] == pg
    with pytest.raises(ValueError, match='maintenance'):
        manager.operate('lifelog', 'prod', 'stop', service='postgres')


def test_agent_observes_native_stack_health(runtime, tmp_path):
    from deployctl.node_agent import RuntimeExecutor
    from unittest.mock import Mock
    manager, engine = runtime; install(manager, package(tmp_path))
    executor = RuntimeExecutor(manager, Mock())
    result = executor.observe({'id':'a'*32, 'project':'lifelog', 'environment':'prod'})
    assert result['health'] == 'healthy' and result['actual_version'] == 'v2.0.0'
    assert result['binding'] == {'host_port':8081, 'bind_address':'127.0.0.1'}


def test_stopped_database_is_started_without_recreation(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path))
    original_id = engine.containers['postgres']['Id']
    engine.containers['postgres']['running'] = False
    install(manager, package(tmp_path, 'v2.1.0'), upgrade=True)
    assert engine.containers['postgres']['Id'] == original_id
    assert engine.containers['postgres']['running']


def test_agent_executes_multi_service_resolution_through_manager(runtime, tmp_path):
    from deployctl.node_agent import RuntimeExecutor
    from unittest.mock import Mock
    manager, engine = runtime; pkg = package(tmp_path)
    client = Mock(); client.server = 'https://ctl.test'
    client.download_release.return_value = pkg
    client.json.return_value = {'registry_host':'registry.example', 'token':''}
    cfg = {'id':'b'*32, 'revision':1, **values(), 'install_params':{}, 'deployment_defaults':{}}
    task = {'id':'a'*32, 'instance_id':'c'*32, 'action':'install', 'project':'lifelog', 'environment':'prod',
            'resolution':{'schema_version':3, 'configuration':cfg, 'release':{'id':'d'*32}}}
    result = RuntimeExecutor(manager, client).execute(task, lambda stage: None)
    assert result['health'] == 'healthy' and engine.containers['postgres']['running']


def test_existing_cli_install_command_deploys_managed_stack(runtime, tmp_path, capsys):
    from deployctl.cli import main
    from unittest.mock import Mock
    from contextlib import nullcontext
    manager, engine = runtime; pkg = package(tmp_path)
    client = Mock(); client.server = 'https://ctl.test'
    client.resolve.return_value = {'schema_version':3, 'project':'lifelog', 'environment':'prod',
        'release':{'id':'a'*32, 'version':'v2.0.0', 'images':{'api':'registry.example/lifelog@sha256:'+'b'*64,'postgres':'registry.example/lifelog@sha256:'+'a'*64}},
        'configuration':{'id':'b'*32, 'revision':1, **values(), 'install_params':{}, 'deployment_defaults':{}}}
    client.download_release.return_value = pkg; client.registry_config.return_value = nullcontext(None)
    with patch('deployctl.runtime.Manager', return_value=manager), \
            patch('deployctl.platform_credentials.Credentials.load'), patch('deployctl.platform_client.PlatformClient', return_value=client):
        result = main(['install','lifelog','--env','prod','--quiet','--root',str(manager.root),'--config-root',str(manager.config_root)])
    assert result == 0, capsys.readouterr().err
    assert engine.containers['postgres']['running'] and engine.containers['api']['running']


def test_primary_endpoint_binding_is_preserved_across_upgrades(runtime, tmp_path):
    manager, engine = runtime
    install(manager, package(tmp_path), port=8099, bind='127.0.0.2')
    state = install(manager, package(tmp_path, 'v2.1.0'), upgrade=True)
    assert state['binding'] == {'port':8099, 'address':'127.0.0.2'}


def test_interrupt_retains_transaction_and_explicit_rollback_recovers(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path)); pg = copy.deepcopy(engine.containers['postgres'])
    original_up = Adapter.up
    def interrupted(driver, name):
        original_up(driver, name)
        if name == 'api' and driver.release['version'] == 'v2.1.0': raise KeyboardInterrupt()
    with patch.object(Adapter, 'up', interrupted), pytest.raises(KeyboardInterrupt):
        install(manager, package(tmp_path, 'v2.1.0', 'registry.example/api@sha256:'+'c'*64), upgrade=True)
    home, _ = manager.paths('lifelog', 'prod')
    assert manager.state(home, 'lifelog', 'prod')['transaction'] is not None
    recovered = manager.rollback('lifelog', 'prod')
    assert recovered['transaction'] is None and engine.containers['api']['image'] == API_IMAGE
    assert engine.containers['postgres'] == pg


class JobAdapter:
    def __init__(self, driver, job): self.engine = driver.engine; self.job = job
    def inspect_job(self, identity): return getattr(self.engine, 'jobs', {}).get(identity)
    def start_job(self, identity, spec):
        if not hasattr(self.engine, 'jobs'): self.engine.jobs = {}
        self.engine.calls.append(('job', self.job))
        self.engine.jobs[identity] = {'running':False, 'exit_code': 1 if getattr(self.engine, 'job_fail', False) else 0}
    def stop_job(self, identity): self.engine.jobs[identity]['running'] = False


def job_package(tmp_path, version='v2.0.0', api_image=API_IMAGE):
    config = source()
    config['jobs'] = {'restore':{'image':api_image,'trigger':'bootstrap','command':['restore'],
        'volumes':{'data':'/var/lib/postgresql'},'environment':{'TOKEN':{'secret':'RESTORE_TOKEN'}}}}
    config['services']['postgres']['depends_on'] = {'restore':'succeeded'}
    return build_release(config, {'api':api_image, 'postgres':IMAGE, 'restore':api_image}, version, tmp_path/'packages')


def test_bootstrap_runs_once_and_application_upgrades_skip_restore(runtime, tmp_path):
    manager, engine = runtime; initial = values(); initial['secrets']['RESTORE_TOKEN'] = 'bootstrap-only-key'
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter):
        first = manager.deploy('lifelog','prod',job_package(tmp_path),managed_configuration=initial)
        pg = copy.deepcopy(engine.containers['postgres'])
        second = manager.deploy('lifelog','prod',job_package(tmp_path,'v2.1.0','registry.example/api@sha256:'+'c'*64),
                                upgrade=True,managed_configuration=values())
        assert second['current']['version'] == 'v2.1.0'
        manager.rollback('lifelog','prod')
    assert [call for call in engine.calls if call[0]=='job'] == [('job','restore')]
    assert engine.containers['postgres'] == pg
    assert first['jobs']['restore']['status'] == 'succeeded'


def test_failed_data_job_retains_recovery_barrier_and_blocks_binary_rollback(runtime, tmp_path):
    manager, engine = runtime; engine.job_fail = True
    initial = values(); initial['secrets']['RESTORE_TOKEN'] = 'bootstrap-only-key'
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter), pytest.raises(RuntimeError):
        manager.deploy('lifelog','prod',job_package(tmp_path),managed_configuration=initial)
    home, _ = manager.paths('lifelog','prod'); state = manager.state(home,'lifelog','prod')
    assert state['transaction']['journal']['phase'] == 'recovery-required'
    assert state['jobs']['restore']['status'] == 'failed' and engine.volumes
    assert not engine.containers
    with pytest.raises(ValueError, match='recovery'): manager.rollback('lifelog','prod')


def test_plan_is_read_only_and_reports_only_api_change(runtime, tmp_path):
    manager, engine = runtime; install(manager, package(tmp_path))
    changed = package(tmp_path,'v2.1.0','registry.example/api@sha256:'+'c'*64)
    before = {str(p):p.read_bytes() for root in (manager.root,manager.config_root) for p in root.rglob('*') if p.is_file()}
    calls = list(engine.calls)
    plan = manager.plan('lifelog','prod',changed,upgrade=True,managed_configuration=values())
    assert plan['services'] == {'api':'update','postgres':'unchanged'}
    assert plan['applicable'] and 'fingerprints' not in plan
    after = {str(p):p.read_bytes() for root in (manager.root,manager.config_root) for p in root.rglob('*') if p.is_file()}
    assert before == after and engine.calls == calls
    assert 'admin-secret' not in str(plan)


def test_plan_before_install_does_not_create_instance_or_configuration_directories(runtime, tmp_path):
    manager, engine = runtime
    plan = manager.plan('lifelog','prod',package(tmp_path),managed_configuration=values())
    assert plan['services'] == {'api':'create','postgres':'create'}
    assert not manager.root.exists() and not manager.config_root.exists()


@pytest.mark.parametrize('operation', ['plan','deploy'])
def test_existing_unregistered_volume_is_rejected_before_pull(runtime, tmp_path, operation):
    from deployctl.runtime import project_name
    manager, engine = runtime; project = project_name('lifelog','prod')
    engine.volumes[project+'-data'] = {'Name':project+'-data','CreatedAt':'old-data',
        'Labels':{'io.team-deploy.project':project,'io.team-deploy.resource':'volume:data'}}
    with pytest.raises(ValueError, match='unregistered'):
        getattr(manager, operation)('lifelog','prod',package(tmp_path),managed_configuration=values())
    assert not engine.calls


def test_cli_plan_prints_changes_without_installing(runtime, tmp_path, capsys):
    import json
    from deployctl.cli import main
    manager, engine = runtime
    config = source(); config['services']['api']['environment'] = {'DB_HOST':{'literal':'postgres'}}
    config['services']['postgres']['secret_files'] = {}
    pkg = build_release(config, {'api':API_IMAGE,'postgres':IMAGE},'v2.0.0',tmp_path/'packages')
    with patch('deployctl.runtime.Manager', return_value=manager):
        result = main(['install','lifelog','--env','prod','--release',str(pkg),'--plan','--quiet',
                       '--root',str(manager.root),'--config-root',str(manager.config_root)])
    output = capsys.readouterr()
    assert result == 0, output.err
    assert json.loads(output.out)['services'] == {'api':'create','postgres':'create'}
    assert not manager.root.exists() and not manager.config_root.exists() and not engine.calls


def test_secret_override_stays_secret_when_management_removes_key(runtime, tmp_path):
    manager, engine = runtime
    config = source(); config['services']['api']['environment']['DB_PASSWORD'] = {'secret':'DB_PASSWORD'}
    def archive(version):
        return build_release(config, {'api':API_IMAGE,'postgres':IMAGE},version,tmp_path/version)
    initial = values(); initial['secrets']['DB_PASSWORD'] = initial['secrets'].pop('db_password')
    manager.deploy('lifelog','prod',archive('v2.0.0'),managed_configuration=initial,
                   runtime_env={'DB_PASSWORD':'override-private'})
    updated = copy.deepcopy(initial); updated['secrets'].pop('DB_PASSWORD')
    manager.deploy('lifelog','prod',archive('v2.1.0'),upgrade=True,
                   managed_configuration=updated)
    state = manager.state(manager.paths('lifelog','prod')[0], 'lifelog', 'prod')
    from deployctl.stack_snapshot import load_stack_snapshot
    snapshot = load_stack_snapshot(manager.paths('lifelog','prod')[1],state['current'],'lifelog')
    assert snapshot.values['secrets']['DB_PASSWORD'] == 'override-private'
    assert 'DB_PASSWORD' not in snapshot.values['config']


def test_cli_forwards_explicit_job_selection_without_changing_install_command(runtime, tmp_path, capsys):
    from deployctl.cli import main
    manager, engine = runtime
    pkg = package(tmp_path)
    with patch('deployctl.runtime.Manager', return_value=manager), patch.object(manager,'plan',return_value={}) as preview:
        result = main(['install','lifelog','--env','prod','--release',str(pkg),'--plan','--quiet',
                       '--run-job','restore'])
    assert result == 0, capsys.readouterr().err
    assert preview.call_args.kwargs['requested_jobs'] == ['restore']
    assert not engine.calls


def test_agent_forwards_job_selection_and_runs_restore_once(runtime, tmp_path):
    from deployctl.node_agent import RuntimeExecutor
    from unittest.mock import Mock
    manager, engine = runtime
    client = Mock(); client.server = 'https://ctl.test'; client.download_release.return_value = job_package(tmp_path)
    client.json.return_value = {'registry_host':'registry.example', 'token':''}
    cfg = {'id':'b'*32, 'revision':1, **values(), 'install_params':{}, 'deployment_defaults':{}}
    cfg['secrets']['RESTORE_TOKEN'] = 'bootstrap-only-key'
    task = {'id':'a'*32, 'instance_id':'c'*32, 'action':'install', 'project':'lifelog', 'environment':'prod', 'requested_jobs':['restore'],
            'resolution':{'schema_version':3, 'configuration':cfg, 'release':{'id':'d'*32}}}
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter):
        result = RuntimeExecutor(manager, client).execute(task, lambda stage: None)
    assert result['health'] == 'healthy'
    assert [call for call in engine.calls if call[0]=='job'] == [('job','restore')]


class SimulatedProcessLoss(BaseException):
    pass


def interrupted_restore(manager, tmp_path):
    class LostAdapter(JobAdapter):
        def inspect_job(self, identity):
            current = super().inspect_job(identity)
            if current: raise SimulatedProcessLoss()
            return current
    initial = values(); initial['secrets']['RESTORE_TOKEN'] = 'bootstrap-only-key'
    with patch('deployctl.stack_jobs.JobDockerAdapter', LostAdapter), pytest.raises(SimulatedProcessLoss):
        manager.deploy('lifelog','prod',job_package(tmp_path),managed_configuration=initial)
    home, _ = manager.paths('lifelog','prod')
    return manager.state(home,'lifelog','prod')


def test_recover_acknowledges_finished_job_without_repeating_restore(runtime, tmp_path):
    manager, engine = runtime; state = interrupted_restore(manager,tmp_path)
    operation = state['transaction']['journal']['id']
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter):
        result = manager.recover('lifelog','prod',operation)
    assert result['transaction'] is None and result['jobs']['restore']['status'] == 'succeeded'
    assert engine.containers['postgres']['running'] and engine.containers['api']['running']
    assert [call for call in engine.calls if call[0]=='job'] == [('job','restore')]


def test_recover_rejects_wrong_operation_and_missing_job_container(runtime, tmp_path):
    manager, engine = runtime; state = interrupted_restore(manager,tmp_path)
    before = list(engine.calls)
    with pytest.raises(ValueError, match='operation'):
        manager.recover('lifelog','prod','f'*32)
    assert engine.calls == before
    engine.jobs.clear()
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter), pytest.raises(RuntimeError, match='unknown'):
        manager.recover('lifelog','prod',state['transaction']['journal']['id'])
    assert engine.calls == before
    home, _ = manager.paths('lifelog','prod'); pending = manager.state(home,'lifelog','prod')
    assert pending['transaction'] and pending['jobs']['restore']['status'] == 'unknown'


def test_recovery_preserves_data_barrier_if_application_start_fails(runtime, tmp_path):
    manager, engine = runtime; state = interrupted_restore(manager,tmp_path)
    engine.fail = 'v2.0.0'
    with patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter), pytest.raises(RuntimeError):
        manager.recover('lifelog','prod',state['transaction']['journal']['id'])
    home, _ = manager.paths('lifelog','prod'); pending = manager.state(home,'lifelog','prod')
    assert pending['transaction']['journal']['rollback_barrier'] is True
    assert pending['transaction']['journal']['phase'] == 'recovery-required'


def test_cli_recover_uses_original_operation_id(runtime, tmp_path, capsys):
    from deployctl.cli import main
    manager, engine = runtime; state = interrupted_restore(manager,tmp_path)
    with patch('deployctl.cli.local_manager', return_value=manager), patch('deployctl.stack_jobs.JobDockerAdapter', JobAdapter):
        result = main(['recover','lifelog','--env','prod','--operation',state['transaction']['journal']['id'],
                       '--root',str(manager.root),'--config-root',str(manager.config_root)])
    assert result == 0, capsys.readouterr().err
    assert [call for call in engine.calls if call[0]=='job'] == [('job','restore')]


def test_database_identity_is_durable_before_starting_logical_restore(runtime, tmp_path):
    manager, engine = runtime
    config = source()
    config['jobs'] = {'restore':{'image':API_IMAGE,'trigger':'bootstrap','command':['restore'],
        'depends_on':{'postgres':'healthy'}}}
    config['services']['api']['depends_on'] = {'restore':'succeeded'}
    pkg = build_release(config, {'api':API_IMAGE,'postgres':IMAGE,'restore':API_IMAGE},'v2.0.0',tmp_path/'packages')
    class InspectJournal(JobAdapter):
        def start_job(self, identity, spec):
            home, _ = manager.paths('lifelog','prod'); durable = manager.state(home,'lifelog','prod')
            assert durable['services']['postgres']['container_id'] == engine.containers['postgres']['Id']
            super().start_job(identity,spec)
    with patch('deployctl.stack_jobs.JobDockerAdapter',InspectJournal):
        manager.deploy('lifelog','prod',pkg,managed_configuration=values())


def test_reviewed_maintenance_quiesces_app_and_preserves_volume(runtime,tmp_path):
    manager,engine=runtime; install(manager,package(tmp_path))
    config=source(); config['services']['postgres']['resources']={'cpus':0.5,'memory_limit':'512m'}
    pkg=build_release(config,{'api':API_IMAGE,'postgres':IMAGE},'v2.1.0',tmp_path/'packages')
    request={'services':['postgres']}
    before=copy.deepcopy(engine.volumes); pg=engine.containers['postgres']['Id']
    plan=manager.plan('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),maintenance=request)
    calls=list(engine.calls)
    with pytest.raises(ValueError,match='plan'):
        manager.deploy('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),maintenance=request)
    assert engine.calls==calls
    request['plan_id']=plan['maintenance']['plan_id']
    result=manager.deploy('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),maintenance=request)
    new=engine.calls[len(calls):]
    assert new.index(('stop','api')) < new.index(('up','postgres')) < new.index(('start','api'))
    assert engine.volumes==before and engine.containers['postgres']['Id']!=pg
    assert result['transaction'] is None


def test_maintenance_never_performs_in_place_image_migration(runtime,tmp_path):
    manager,engine=runtime; install(manager,package(tmp_path))
    config=source(); changed=IMAGE.replace('a'*64,'e'*64); config['services']['postgres']['image']=changed
    pkg=build_release(config,{'api':API_IMAGE,'postgres':changed},'v2.1.0',tmp_path/'packages')
    with pytest.raises(ValueError,match='explicit service replacement'):
        manager.plan('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),
                     maintenance={'services':['postgres']})


def test_cli_maintenance_requires_reviewed_resource_plan(runtime,tmp_path,capsys):
    from deployctl.cli import main
    manager,engine=runtime; install(manager,package(tmp_path))
    with patch('deployctl.runtime.Manager',return_value=manager), patch.object(manager,'plan',return_value={}) as plan:
        assert main(['maintain','lifelog','--env','prod','--release',str(package(tmp_path,'v2.1.0')),
                     '--service','postgres','--plan','--quiet'])==0
    assert plan.call_args.kwargs['upgrade'] is True
    assert plan.call_args.kwargs['maintenance']=={'services':['postgres'],'plan_id':None}


def test_maintenance_failure_keeps_pending_and_never_downgrades_database(runtime,tmp_path):
    manager,engine=runtime; install(manager,package(tmp_path))
    config=source(); config['services']['postgres']['resources']={'cpus':0.5,'memory_limit':'512m'}
    pkg=build_release(config,{'api':API_IMAGE,'postgres':IMAGE},'v2.1.0',tmp_path/'packages')
    request={'services':['postgres']}
    request['plan_id']=manager.plan('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),maintenance=request)['maintenance']['plan_id']
    original=Adapter.healthy
    def failed(driver,name):
        if name=='postgres' and driver.release['version']=='v2.1.0': raise RuntimeError('database unhealthy')
        return original(driver,name)
    with patch.object(Adapter,'healthy',failed),pytest.raises(RuntimeError):
        manager.deploy('lifelog','prod',pkg,upgrade=True,managed_configuration=values(),maintenance=request)
    home,_=manager.paths('lifelog','prod'); pending=manager.state(home,'lifelog','prod')
    assert pending['transaction']['journal']['maintenance']['services']==['postgres']
    assert not engine.containers['api']['running']
    with pytest.raises(ValueError,match='recovery'): manager.rollback('lifelog','prod')
    result=manager.recover('lifelog','prod',pending['transaction']['journal']['id'])
    assert result['transaction'] is None and engine.containers['api']['running']


def test_generation_install_and_upgrade_preserve_isolated_resources(runtime, tmp_path):
    from deployctl.stack_manager import StackManager, empty_stack_state
    manager, engine = runtime
    home, folder = manager.paths('lifelog', 'prod')
    stack = StackManager(manager, home, folder, 'lifelog', 'prod')
    state = empty_stack_state('lifelog', 'prod'); state['generation'] = 'a'*32
    stack.save(state)
    first = install(manager, package(tmp_path))
    assert first['current']['generation'] == 'a'*32
    assert all('-g'+'a'*32 in v['name'] for v in first['volumes'].values())
    pg = copy.deepcopy(engine.containers['postgres'])
    upgraded = install(manager, package(tmp_path, 'v2.1.0'), upgrade=True)
    assert upgraded['current']['generation'] == 'a'*32
    assert engine.containers['postgres'] == pg


def test_cross_generation_rollback_is_refused(runtime, tmp_path):
    from deployctl.stack_manager import StackManager
    manager, engine = runtime
    state = install(manager, package(tmp_path))
    state['previous'] = dict(state['current'], generation='b'*32)
    home, folder = manager.paths('lifelog', 'prod')
    stack = StackManager(manager, home, folder, 'lifelog', 'prod')
    with pytest.raises(ValueError, match='across service layouts'):
        stack.rollback(state)


@pytest.mark.parametrize('installed', [False, True])
@pytest.mark.parametrize('starting', [False, True])
def test_recover_after_real_crash_between_up_and_inventory_checkpoint(runtime,tmp_path,installed,starting):
    from deployctl.service_state import write_inventory
    manager,engine=runtime
    if installed: install(manager,package(tmp_path))
    home,_=manager.paths('lifelog','prod'); durable=[]
    original=Adapter.up
    def replaced(adapter,name):
        if name=='api': durable.append(manager.state(home,'lifelog','prod'))
        return original(adapter,name)
    with patch.object(Adapter,'up',replaced):
        install(manager,package(tmp_path,'v2.1.0',API_IMAGE.replace('b'*64,'c'*64)),upgrade=installed)
    crashed=durable[-1]
    write_inventory(home/'state.json',crashed)
    ready=[not starting]; observe=Adapter.observe; healthy=Adapter.healthy
    def observed(adapter,name,expected_id=None):
        result=observe(adapter,name,expected_id)
        if name=='api' and not ready[0]: result['healthy']=False
        return result
    def waited(adapter,name):
        if name=='api': ready[0]=True
        return healthy(adapter,name)
    with patch.object(Adapter,'observe',observed),patch.object(Adapter,'healthy',waited):
        result=manager.recover('lifelog','prod',crashed['transaction']['journal']['id'])
    assert ready[0]
    assert result['transaction'] is None and result['services']['api']['healthy']


def test_recover_after_real_crash_during_resource_creation(runtime,tmp_path):
    from deployctl.service_state import write_inventory
    manager,engine=runtime;home,_=manager.paths('lifelog','prod');durable=[]
    original=Adapter.ensure_resources
    def allocated(adapter):
        durable.append(manager.state(home,'lifelog','prod'))
        original(adapter)
        raise RuntimeError('simulated loss after allocation')
    with patch.object(Adapter,'ensure_resources',allocated),pytest.raises(RuntimeError):
        install(manager,package(tmp_path))
    crashed=durable[-1];write_inventory(home/'state.json',crashed)
    result=manager.recover('lifelog','prod',crashed['transaction']['journal']['id'])
    assert result['transaction'] is None and result['services']['postgres']['healthy']


def test_rollback_stops_added_application_and_later_upgrade_reuses_its_inventory(runtime,tmp_path):
    manager,engine=runtime;install(manager,package(tmp_path)); pg=copy.deepcopy(engine.containers['postgres'])
    cfg=source();cfg['services']['worker']=copy.deepcopy(cfg['services']['api'])
    cfg['services']['worker'].pop('endpoints',None)
    cfg['services']['worker']['health']={'exec':['true']}
    added=build_release(cfg,{'api':API_IMAGE,'postgres':IMAGE,'worker':API_IMAGE},'v2.1.0',tmp_path/'extra')
    install(manager,added,upgrade=True)
    result=manager.rollback('lifelog','prod')
    assert not engine.containers['worker']['running']
    assert result['services']['worker']['retired']
    with patch.object(Adapter,'compose',lambda self,*args:self.engine.calls.append(tuple(args)),create=True):
        manager.operate('lifelog','prod','restart')
    assert ('restart','--no-deps','worker') not in engine.calls
    assert not engine.containers['worker']['running']
    install(manager,added,upgrade=True)
    assert engine.containers['worker']['running'] and engine.containers['postgres']==pg


def test_maintenance_reconciles_missing_container_only_with_durable_recreation_intent(runtime,tmp_path):
    from deployctl.service_state import write_inventory
    manager,engine=runtime;install(manager,package(tmp_path))
    cfg=source();cfg['services']['postgres']['resources']={'memory_limit':'768m'}
    target=build_release(cfg,{'api':API_IMAGE,'postgres':IMAGE},'v2.1.0',tmp_path/'maintenance-gap')
    request={'services':['postgres']}
    plan=manager.plan('lifelog','prod',target,upgrade=True,managed_configuration=values(),maintenance=request)
    request['plan_id']=plan['maintenance']['plan_id'];home,_=manager.paths('lifelog','prod');durable=[]
    original=Adapter.up
    def interrupted(adapter,name):
        if name=='postgres':
            durable.append(manager.state(home,'lifelog','prod'))
            engine.containers.pop(name)
            raise RuntimeError('removed before create')
        return original(adapter,name)
    with patch.object(Adapter,'up',interrupted),pytest.raises(RuntimeError):
        install(manager,target,upgrade=True,maintenance=request)
    write_inventory(home/'state.json',durable[-1])
    result=manager.recover('lifelog','prod',durable[-1]['transaction']['journal']['id'])
    assert result['transaction'] is None and result['services']['postgres']['healthy']


def test_failed_rollback_retirement_keeps_pending_recovery(runtime,tmp_path):
    manager,engine=runtime;install(manager,package(tmp_path))
    cfg=source();cfg['services']['worker']=copy.deepcopy(cfg['services']['api'])
    cfg['services']['worker'].pop('endpoints',None);cfg['services']['worker']['health']={'exec':['true']}
    newer=build_release(cfg,{'api':API_IMAGE.replace('b'*64,'c'*64),'postgres':IMAGE,'worker':API_IMAGE},'v2.1.0',tmp_path/'retire-failure')
    install(manager,newer,upgrade=True);engine.fail='v2.0.0'
    with pytest.raises(RuntimeError): manager.rollback('lifelog','prod')
    home,_=manager.paths('lifelog','prod');pending=manager.state(home,'lifelog','prod')
    assert pending['transaction'] and pending['transaction']['journal']['rollback_barrier']
    engine.fail=None
    result=manager.recover('lifelog','prod',pending['transaction']['journal']['id'])
    assert result['current']['version']=='v2.0.0' and result['transaction'] is None
    assert not engine.containers['worker']['running']
