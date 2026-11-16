import copy
from test_stack_manager import runtime, install, package


def test_service_observation_contains_no_configuration(runtime, tmp_path):
    from deployctl.stack_observation import observe_stack
    from deployctl.stack_manager import StackManager
    manager, engine = runtime
    state = install(manager, package(tmp_path))
    home, folder = manager.paths('lifelog','prod')
    detail = observe_stack(StackManager(manager,home,folder,'lifelog','prod'),state)
    assert detail['services']['postgres']['health']=='healthy'
    assert detail['services']['postgres']['lifecycle']=='persistent'
    assert not detail['recovery_required']
    assert 'admin-secret' not in str(detail) and 'snapshot' not in str(detail)
    engine.volumes.clear()
    failed = observe_stack(StackManager(manager,home,folder,'lifelog','prod'),state)
    assert failed['recovery_required']
    assert failed['services']['postgres']['health']=='unknown'


def test_node_logs_selects_registered_service_and_redacts_its_secrets(runtime, tmp_path):
    from unittest.mock import patch
    from deployctl.node_logs import NodeLogs
    manager, engine = runtime
    install(manager,package(tmp_path))
    logs = NodeLogs(manager.root,manager.config_root)
    with patch('deployctl.runtime.Manager',return_value=manager):
        selected = logs.containers('lifelog','prod','postgres')
    assert selected[0]['id']==engine.containers['postgres']['Id']
    with patch('deployctl.node_logs.bounded_command',return_value=('password=admin-secret',False)):
        text, clipped = logs.runtime_text(selected[0],{'tail':200})
    assert 'admin-secret' not in text and '[REDACTED]' in text
