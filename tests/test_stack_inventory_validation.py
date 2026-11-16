import pytest
from deployctl.stack_manager import empty_stack_state, validate_stack_state


@pytest.mark.parametrize('change', [
    lambda s: s.update(network={'name':'network','identity':''}),
    lambda s: s.update(binding={'port':True,'address':'127.0.0.1'}),
    lambda s: s['volumes'].update(data={'name':'volume','identity':None,'present':True}),
    lambda s: s['jobs'].update(restore={'status':'succeeded'}),
    lambda s: s.update(transaction={'target':{'version':'v1.0.0','snapshot':'a'*64},'origin':None,
        'journal':{'phase':'anything','rollback_barrier':False}}),
    lambda s: s.update(transaction={'target':{'version':'v1.0.0','snapshot':'a'*64},'origin':None,
        'journal':{'phase':'stop','action':{'kind':'stop','service':'api'},'rollback_barrier':'false'}}),
])
def test_malformed_inventory_is_rejected_before_recovery(change):
    state = empty_stack_state('notes','prod'); change(state)
    with pytest.raises(ValueError): validate_stack_state(state,'notes','prod')


def test_oversized_state_rejected_before_json_loading(tmp_path):
    from deployctl.runtime import Manager
    manager = Manager(tmp_path/'runtime',tmp_path/'config')
    home, _ = manager.paths('notes','prod')
    (home/'state.json').write_text('{"padding":"'+'x'*(4*1024*1024)+'"}')
    with pytest.raises(ValueError, match='size'):
        manager.state(home,'notes','prod')
