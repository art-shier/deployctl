import copy
import pytest
from deployctl.multiservice_runtime import ServiceExecutor


class Driver:
    def __init__(self): self.events = []; self.fail = None
    def pull(self, name): self.events.append(('pull', name))
    def ensure_resources(self): self.events.append(('resources',))
    def up(self, name):
        self.events.append(('up', name))
        if self.fail == name: raise RuntimeError('startup failed')
    def healthy(self, name): self.events.append(('healthy', name))
    def restore(self, name, previous): self.events.append(('restore', name, previous['fingerprint']))
    def stop(self, name): self.events.append(('stop', name))
    def run_job(self, name): self.events.append(('job', name))


def setup():
    specs = {'api': {'lifecycle': 'application'}, 'postgres': {'lifecycle': 'persistent'}}
    prior = {'api': {'fingerprint': 'old-api'}, 'postgres': {'fingerprint': 'old-db'}}
    return specs, prior


def plan():
    return {'applicable': True, 'services': {'api': 'update', 'postgres': 'unchanged'},
            'volumes': {'data': 'reuse'}, 'order': ['postgres', 'api']}


def test_only_changed_service_is_pulled_and_restarted():
    driver = Driver(); journal = []; specs, prior = setup()
    ServiceExecutor(driver, lambda state: journal.append(copy.deepcopy(state))).apply(plan(), specs, {}, prior)
    assert driver.events == [('pull', 'api'), ('resources',), ('healthy', 'postgres'), ('up', 'api'), ('healthy', 'api')]
    assert journal[-1]['phase'] == 'succeeded'
    assert any(s.get('action') == {'kind': 'up', 'service': 'api'} for s in journal)


def test_persistent_change_is_rejected_before_any_docker_call():
    driver = Driver(); specs, prior = setup(); p = plan(); p['applicable'] = False
    with pytest.raises(ValueError): ServiceExecutor(driver, lambda s: None).apply(p, specs, {}, prior)
    assert driver.events == []


def test_failed_api_restores_only_api():
    driver = Driver(); driver.fail = 'api'; specs, prior = setup(); journal = []
    with pytest.raises(RuntimeError, match='startup failed'):
        ServiceExecutor(driver, lambda s: journal.append(copy.deepcopy(s))).apply(plan(), specs, {}, prior)
    assert ('restore', 'api', 'old-api') in driver.events
    assert not any(event[:2] == ('up', 'postgres') for event in driver.events)
    assert journal[-1]['phase'] == 'rolled-back'


def test_job_unknown_blocks_automatic_binary_rollback():
    driver = Driver(); specs, prior = setup(); journal = []
    p = plan(); p['order'] = ['postgres', 'migrate', 'api']
    def fail_job(name): raise RuntimeError('connection lost after data change')
    driver.run_job = fail_job
    with pytest.raises(RuntimeError):
        ServiceExecutor(driver, lambda s: journal.append(copy.deepcopy(s))).apply(
            p, specs, {'migrate': {'rollback_compatible': False}}, prior)
    assert journal[-1]['phase'] == 'recovery-required'
    assert not any(e[0] == 'restore' for e in driver.events)


def test_new_application_is_stopped_but_new_database_retained_on_failure():
    driver = Driver(); driver.fail = 'api'; specs, _ = setup(); p = plan()
    p['services'] = {'api': 'create', 'postgres': 'create'}
    with pytest.raises(RuntimeError): ServiceExecutor(driver, lambda s: None).apply(p, specs, {}, {})
    assert ('stop', 'api') in driver.events
    assert ('stop', 'postgres') not in driver.events
