import copy
import pytest
from test_stack_snapshot import full_release
from test_service_plan import values
from test_multiservice_contract import IMAGE


def with_restore():
    from deployctl.multiservice_release import validate_multiservice_release
    r = full_release()
    r['deployment']['jobs'] = {'restore': {'image': IMAGE, 'trigger': 'bootstrap', 'command': ['restore'],
        'volumes': {'data':'/var/lib/postgresql'}, 'environment': {'TOKEN':{'secret':'RESTORE_TOKEN'}}}}
    r['deployment']['services']['postgres']['depends_on'] = {'restore':'succeeded'}
    return validate_multiservice_release(r)


def test_bootstrap_selection_has_no_project_data_policy():
    from deployctl.stack_jobs import select_jobs
    r = with_restore(); v = values(); v['secrets']['RESTORE_TOKEN'] = 'token'
    selected = select_jobs(r, {}, v, [])
    assert selected['active'] == ['restore']
    assert selected['order'] == ['restore','postgres','api']
    assert select_jobs(r, {'volumes':{'data':{'identity':'old'}}}, v, [])['active']==['restore']


def test_completed_restore_does_not_repeat_when_app_job_image_or_credentials_change():
    from deployctl.stack_jobs import select_jobs
    r = with_restore(); r['deployment']['jobs']['restore']['image'] = IMAGE.replace('a'*64,'b'*64)
    state = {'volumes':{'data':{'name':'project-data','identity':'volume-id'}},
             'jobs':{'restore':{'status':'succeeded','target_volumes':{'data':{'name':'project-data','identity':'volume-id'}}}}}
    # Credential removed after bootstrap: normal upgrades must not need it again.
    plan = select_jobs(r, state, values(), [])
    assert plan['active'] == [] and plan['order'] == ['postgres','api']
    state['volumes']['data']['identity'] = 'replaced'
    with pytest.raises(ValueError, match='volume'): select_jobs(r, state, values(), [])


def test_explicit_jobs_are_not_run_by_ordinary_install():
    from deployctl.stack_jobs import select_jobs
    r = full_release(); r['deployment']['jobs'] = {'cleanup': {'image':IMAGE, 'trigger':'explicit', 'command':['cleanup'],
        'rollback_compatible':True, 'environment': {'TOKEN':{'secret':'UNAVAILABLE'}}}}
    from deployctl.multiservice_release import validate_multiservice_release
    r = validate_multiservice_release(r)
    assert select_jobs(r, {}, values(), [])['active'] == []
    r['deployment']['services']['api']['depends_on']['cleanup'] = 'succeeded'
    with pytest.raises(ValueError, match='explicit'): select_jobs(r, {}, values(), [])


def job_driver(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot
    from deployctl.service_docker import ServiceDockerDriver
    from test_service_docker import Commands
    v = values(); v['secrets']['RESTORE_TOKEN'] = 'job-secret'
    snapshot = create_stack_snapshot(tmp_path, with_restore(), v)
    driver = ServiceDockerDriver(Commands(), snapshot.directory, 'lifelog-prod-test', snapshot.release,
                                 snapshot.environment('lifelog-prod-test'), {})
    driver.snapshot = snapshot
    execution = 'job-'+'a'*32
    resource = {'Id':'job-container', 'Config':{'Image':IMAGE, 'Cmd':['restore'], 'Env':['TOKEN=job-secret'],
        'Labels':{'com.docker.compose.project':driver.project,'com.docker.compose.service':'restore',
                  'io.team-deploy.application':'lifelog','io.team-deploy.service':'restore',
                  'io.team-deploy.lifecycle':'job','io.team-deploy.execution':execution}},
        'Mounts':[{'Type':'volume','Name':driver.project+'-data','Destination':'/var/lib/postgresql','RW':True}],
        'HostConfig':{'Privileged':False,'CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],
                      'Memory':512*1024*1024,'NanoCpus':1000000000,'PortBindings':{},'RestartPolicy':{'Name':'no'}},
        'NetworkSettings':{'Networks':{driver.network:{'Aliases':['restore']}}},
        'State':{'Running':False,'Status':'exited','ExitCode':0}}
    driver.inspect = lambda kind,name: resource
    return driver, execution, resource


def test_job_reconciliation_checks_command_and_never_treats_created_as_success(tmp_path):
    from deployctl.stack_jobs import JobDockerAdapter
    driver, execution, resource = job_driver(tmp_path)
    adapter = JobDockerAdapter(driver, 'restore')
    assert adapter.inspect_job(execution) == {'running':False,'exit_code':0}
    resource['State']['Status'] = 'created'
    assert adapter.inspect_job(execution)['exit_code'] is None
    resource['Config']['Cmd'] = ['true']
    with pytest.raises(ValueError): adapter.inspect_job(execution)


def test_job_launch_keeps_exit_record_and_scopes_container_name(tmp_path):
    from deployctl.stack_jobs import JobDockerAdapter
    driver, execution, resource = job_driver(tmp_path)
    driver.container = lambda name: None
    adapter = JobDockerAdapter(driver, 'restore')
    adapter.start_job(execution, driver.config['jobs']['restore'])
    command = driver.commands.calls[-1]
    assert '--rm' not in command and '--no-deps' in command and '--service-ports' not in command
    assert command[command.index('--name')+1] == 'lifelog-prod-test-'+execution
    assert command[-1] == 'restore'


@pytest.mark.parametrize('requested', [[{}], [None], [['restore']], ['restore','restore']])
def test_invalid_job_selection_fails_with_validation_error(requested):
    from deployctl.stack_jobs import select_jobs
    with pytest.raises(ValueError, match='selection'):
        select_jobs(with_restore(), {}, values(), requested=requested)
