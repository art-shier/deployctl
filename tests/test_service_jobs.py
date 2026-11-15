import copy
import pytest
from deployctl.service_jobs import JobRunner


class JobDriver:
    def __init__(self): self.current = None; self.starts = 0; self.stops = 0
    def inspect_job(self, name): return self.current
    def start_job(self, name, spec):
        self.starts += 1; self.current = {'running': False, 'exit_code': 0}
    def stop_job(self, name): self.stops += 1; self.current['running'] = False


def spec(): return {'image': 'registry.example/job@sha256:' + 'a'*64, 'trigger': 'bootstrap',
                   'command': ['restore'], 'timeout_seconds': 1, 'rollback_compatible': False}


def test_restore_success_bound_to_volume_not_invocation():
    driver = JobDriver(); saved = []; state = {}
    runner = JobRunner(driver, state, lambda x: saved.append(copy.deepcopy(x)))
    first = runner.run('restore', spec(), 'volume-1', 'backup-1')
    assert first['status'] == 'succeeded'
    assert saved[0]['restore']['status'] == 'starting'
    assert runner.run('restore', spec(), 'volume-1', 'backup-1') == first
    assert driver.starts == 1
    with pytest.raises(ValueError, match='bootstrap'):
        runner.run('restore', spec(), 'volume-1', 'backup-2')
    assert driver.starts == 1


def test_missing_container_after_started_is_unknown_not_retry():
    driver = JobDriver(); state = {}; runner = JobRunner(driver, state, lambda x: None)
    runner.run('restore', spec(), 'volume-1', 'backup-1')
    state['restore']['status'] = 'running'; driver.current = None
    with pytest.raises(RuntimeError, match='unknown'): runner.run('restore', spec(), 'volume-1', 'backup-1')
    assert driver.starts == 1
    assert state['restore']['status'] == 'unknown'


def test_existing_finished_container_reconciles_without_restarting():
    driver = JobDriver(); state = {}; runner = JobRunner(driver, state, lambda x: None)
    runner.run('restore', spec(), 'volume-1', 'backup-1')
    state['restore']['status'] = 'running'
    assert runner.run('restore', spec(), 'volume-1', 'backup-1')['status'] == 'succeeded'
    assert driver.starts == 1


def test_timeout_stops_writer_before_recording_failure():
    driver = JobDriver(); state = {}; runner = JobRunner(driver, state, lambda x: None)
    runner.run('restore', spec(), 'volume-1', 'backup-1')
    state['restore']['status'] = 'running'; driver.current = {'running': True}
    ticks = iter([0, 2]); runner.clock = lambda: next(ticks)
    with pytest.raises(RuntimeError, match='timed out'): runner.run('restore', spec(), 'volume-1', 'backup-1')
    assert driver.stops == 1
    assert state['restore']['status'] == 'failed'


def test_failed_restore_never_automatically_retries():
    driver = JobDriver(); state = {}; runner = JobRunner(driver, state, lambda x: None)
    runner.run('restore', spec(), 'volume-1', 'backup-1')
    state['restore']['status'] = 'failed'
    with pytest.raises(RuntimeError): runner.run('restore', spec(), 'volume-1', 'backup-1')
    assert driver.starts == 1
