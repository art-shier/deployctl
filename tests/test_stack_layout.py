import copy
from unittest.mock import patch
import pytest
from deployctl.stack_manager import StackManager, empty_stack_state
from test_stack_manager import runtime
from test_stack_jobs import with_restore
from test_service_plan import values


def legacy():
    return {'schema_version':2,'application':'lifelog','environment':'prod',
            'current':{'version':'v1.0.0','configuration':'a'*32,'configuration_sha256':'b'*64,'legacy':False,
                       'binding':{'port':8081,'address':'127.0.0.1'}},'previous':None,'transaction':None,
            'binding':{'port':8081,'address':'127.0.0.1'}}


def test_legacy_restore_requires_stopped_verified_source_and_explicit_restore(runtime):
    manager,engine=runtime;home,folder=manager.paths('lifelog','prod')
    stack=StackManager(manager,home,folder,'lifelog','prod');v=values();v['secrets']['RESTORE_TOKEN']='private'
    with patch('deployctl.stack_layout.legacy_inventory',return_value={'container_id':'c'*64,'fingerprint':'d'*64,'volumes':[]}):
        plan=stack.deploy(legacy(),with_restore(),managed_configuration=v,preview=True)
    assert plan['legacy_source']['container_id']=='c'*64 and 'private' not in str(plan)


def test_legacy_source_rejects_running_or_changed_identity(runtime):
    from deployctl.stack_layout import legacy_inventory
    manager,_=runtime;home,folder=manager.paths('lifelog','prod');stack=StackManager(manager,home,folder,'lifelog','prod')
    snap=type('Snapshot',(),{})()
    labels={'com.docker.compose.project':stack.project,'com.docker.compose.service':'app',
            'io.team-deploy.application':'lifelog','io.team-deploy.version':'v1.0.0','io.team-deploy.configuration':'a'*32}
    resource={'Id':'c'*64,'Config':{'Image':'image','Labels':labels},'HostConfig':{},'Mounts':[],'State':{'Running':False}}
    import json
    manager.driver._run=lambda *a,**k:json.dumps([resource])
    manager.driver.compose=lambda *a,**k:'c'*64
    with patch.object(manager,'reference_environment',return_value=(home,{'image':'image'},snap,{})):
        first=legacy_inventory(stack,legacy());assert first['container_id']=='c'*64
        resource['State']['Running']=True
        with pytest.raises(ValueError,match='stopped'):legacy_inventory(stack,legacy())
        resource['State']['Running']=False;resource['Config']['Labels']['io.team-deploy.configuration']='f'*32
        with pytest.raises(ValueError,match='identity'):legacy_inventory(stack,legacy())

@pytest.mark.parametrize('changed', [False, True])
def test_legacy_cutover_checkpoint_recovery_never_adopts_old_resources(runtime, tmp_path, changed):
    from deployctl.service_state import write_inventory
    from deployctl.release import build_release
    from deployctl.stack_jobs import target_volumes, volume_identities
    from deployctl.service_plan import service_fingerprint
    manager,engine=runtime;home,folder=manager.paths('lifelog','prod');old=legacy()
    write_inventory(home/'state.json',old)
    release=with_restore();config=copy.deepcopy(release['deployment']);config['schema_version']=2;config['application']='lifelog'
    images={name:node['image'] for name,node in {**config['services'],**config['jobs']}.items()}
    package=build_release(config,images,'v2.0.0',tmp_path/'packages')
    v=values();v['secrets']['RESTORE_TOKEN']='token'
    observed={'container_id':'c'*64,'fingerprint':'d'*64,'volumes':[{'name':'old-data','identity':'old-id'}]}
    calls=[]
    def bind(driver,snapshot,state,selection,save):
        def run(name):
            calls.append(name)
            spec=snapshot.release['deployment']['jobs'][name]
            state['jobs'][name]={'id':'c'*64,'container':'job-'+'c'*32,'target':driver.project,'source':selection['sources'][name],
                'status':'succeeded','deadline_at':9999999999,'target_volumes':volume_identities(state,target_volumes(snapshot.release['deployment'],name)),
                'fingerprint':service_fingerprint(spec,snapshot.values),'snapshot':snapshot.ref}
            save(state)
        return run
    save=StackManager.save
    def crash(stack,state):
        if stack.home.parent.name=='layouts': raise RuntimeError('process lost before candidate save')
        return save(stack,state)
    with patch('deployctl.stack_layout.legacy_inventory',return_value=observed),patch('deployctl.stack_jobs.bind_job_runner',bind):
        plan=manager.plan('lifelog','prod',package,upgrade=True,managed_configuration=v)
        with patch.object(StackManager,'save',crash),pytest.raises(RuntimeError,match='process lost'):
            manager.deploy('lifelog','prod',package,upgrade=True,managed_configuration=v)
        pending=manager.state(home,'lifelog','prod')
        assert pending['current'] is None and pending['transaction']['journal']['layout']
        if changed:
            observed['fingerprint']='f'*64
            with pytest.raises(ValueError,match='source changed'):manager.recover('lifelog','prod',plan['generation'])
            assert calls==[]
        else:
            result=manager.recover('lifelog','prod',plan['generation'])
            assert result['transaction'] is None and result['previous'] is None and result['current']['version']=='v2.0.0'
            assert result['generation']==plan['generation'] and calls==['restore']
            assert result['binding']==old['binding']
            assert all(item['name']!='old-data' for item in result['volumes'].values())
            assert __import__('json').loads((home/'layouts'/plan['generation']/'source-state.json').read_text())==old


def test_layout_upgrade_without_any_data_or_initialization_job(runtime,tmp_path):
    from deployctl.service_state import write_inventory
    from test_stack_manager import package
    manager,engine=runtime;home,folder=manager.paths('lifelog','prod')
    write_inventory(home/'state.json',legacy())
    observed={'container_id':'c'*64,'fingerprint':'d'*64,'volumes':[]}
    pkg=package(tmp_path)
    with patch('deployctl.stack_layout.legacy_inventory',return_value=observed):
        plan=manager.plan('lifelog','prod',pkg,upgrade=True,managed_configuration=values())
        assert plan['jobs']==[]
        result=manager.deploy('lifelog','prod',pkg,upgrade=True,managed_configuration=values())
    assert result['schema_version']==3 and result['transaction'] is None
    assert result['jobs']=={} and result['binding']['port']==8081
    assert result['previous'] is None
