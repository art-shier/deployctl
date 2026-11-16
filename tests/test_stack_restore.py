import copy
import pytest
from deployctl.stack_manager import StackManager, empty_stack_state
from test_stack_manager import runtime, install, package
from test_stack_jobs import with_restore
from test_service_plan import values


def test_restore_preview_requires_source_and_does_not_touch_runtime(runtime, tmp_path):
    from deployctl.stack_restore import plan_restore
    manager, engine = runtime
    state = install(manager, package(tmp_path))
    home, folder = manager.paths('lifelog', 'prod')
    stack = StackManager(manager, home, folder, 'lifelog', 'prod')
    release = with_restore(); v = values(); v['secrets']['RESTORE_TOKEN'] = 'private'
    before = copy.deepcopy(engine.calls)
    with pytest.raises(ValueError, match='source'):
        plan_restore(stack, state, release, v, {}, [])
    result = plan_restore(stack, state, release, v, {'restore':'a'*64}, [])
    assert len(result['plan_id']) == 64
    assert result['generation'] != state.get('generation')
    assert result['retained_volumes'] == sorted(v['name'] for v in state['volumes'].values())
    assert 'private' not in str(result)
    assert engine.calls == before


def test_restore_plan_rejects_release_without_restore_job(runtime, tmp_path):
    from deployctl.stack_restore import plan_restore
    manager, engine = runtime
    state = install(manager, package(tmp_path))
    home, folder = manager.paths('lifelog', 'prod')
    stack = StackManager(manager, home, folder, 'lifelog', 'prod')
    with pytest.raises(ValueError, match='restoration job'):
        plan_restore(stack, state, stack.load(state['current']).release, values(), {}, [])


def test_restore_cli_has_reviewed_plan_and_source_flags():
    from deployctl.cli import parser
    args = parser().parse_args(['restore','lifelog','--env','prod','--plan',
                               '--job-source','restore='+'a'*64,'--plan-id','b'*64])
    assert args.plan and args.plan_id == 'b'*64


def test_restore_promotes_new_generation_and_retains_source_after_freeze_interruption(tmp_path):
    from unittest.mock import patch
    from deployctl.runtime import Manager
    from deployctl.stack_restore import plan_restore, restore_stack
    from deployctl.stack_jobs import target_volumes, volume_identities
    from deployctl.service_plan import service_fingerprint
    from test_stack_manager import Adapter, Engine
    engines = {}; calls = []; fail = [True]
    class IsolatedAdapter(Adapter):
        def __init__(self, commands, directory, project, release, environment, inventory):
            engine = engines.setdefault(project, Engine())
            super().__init__(engine, directory, project, release, environment, inventory)
        def freeze(self, name, expected_id):
            info = self.observe(name, expected_id)
            if info['container_id']: self.engine.containers[name]['running'] = False
            calls.append(('freeze',self.project,name))
            if fail[0]:
                fail[0] = False
                raise RuntimeError('interrupted freeze')
    def bind(driver, snapshot, state, selection, save):
        def run(name):
            calls.append(('job',driver.project,name))
            spec = snapshot.release['deployment']['jobs'][name]
            state['jobs'][name] = {'id':'c'*64,'container':'job-'+'c'*32,'target':driver.project,
                'source':selection['sources'][name], 'status':'succeeded', 'deadline_at':9999999999,
                'target_volumes':volume_identities(state,target_volumes(snapshot.release['deployment'],name)),
                'fingerprint':service_fingerprint(spec,snapshot.values),'snapshot':snapshot.ref}
            save(state)
        return run
    manager = Manager(tmp_path/'apps',tmp_path/'config',driver=Engine())
    with patch('deployctl.stack_manager.ServiceDockerDriver',IsolatedAdapter), patch('deployctl.stack_jobs.bind_job_runner',bind):
        state = install(manager,package(tmp_path))
        home, folder = manager.paths('lifelog','prod')
        stack = StackManager(manager,home,folder,'lifelog','prod')
        old_volumes = copy.deepcopy(state['volumes'])
        release = with_restore(); v=values(); v['secrets']['RESTORE_TOKEN']='token'
        plan = plan_restore(stack,state,release,v,{'restore':'a'*64},[])
        with pytest.raises(RuntimeError,match='interrupted freeze'):
            restore_stack(stack,state,release,v,{'restore':'a'*64},[],plan['plan_id'])
        pending = manager.state(home,'lifelog','prod')
        assert pending['current'].get('generation') is None
        assert pending['transaction']['journal']['phase']=='restoring'
        result = manager.recover('lifelog','prod',plan['generation'])
        assert result['generation']==plan['generation'] and result['transaction'] is None
        assert result['volumes'] != old_volumes
        old_engine = engines[stack.project]
        assert all(not item['running'] for item in old_engine.containers.values())
        assert all(item['name'] in old_engine.volumes for item in old_volumes.values())
        assert len([call for call in calls if call[0]=='job'])==1
        assert calls[-1][0]=='job'
