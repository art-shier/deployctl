"""Durable one-shot job reconciliation; never blindly repeat a data restore."""
import copy
import hashlib
import json
import time
from .contract import validate_name


class JobRunner:
    def __init__(self, driver, state, save):
        self.driver, self.state, self.save = driver, state, save
        self.clock = time.monotonic
        self.sleep = time.sleep

    def _record(self, name, execution, status):
        execution['status'] = status
        self.state[name] = copy.deepcopy(execution)
        self.save(copy.deepcopy(self.state))

    def run(self, name, spec, target_volume, backup_identity, configuration_identity=''):
        validate_name(name, 'job')
        if not target_volume or not backup_identity:
            raise ValueError('job requires target volume and source identity')
        identity = hashlib.sha256(json.dumps({'name': name, 'spec': spec, 'target': target_volume,
            'source': backup_identity, 'configuration': configuration_identity}, sort_keys=True).encode()).hexdigest()
        prior = self.state.get(name)
        execution = {'id': identity, 'container': 'job-' + identity[:32], 'target': target_volume,
                     'source': backup_identity, 'status': 'starting'}
        if prior:
            if prior['id'] != identity:
                # A different attempt does not authorize overwriting the prior target.
                raise ValueError('bootstrap identity changed; explicit verified recovery required')
            if prior['status'] == 'succeeded': return copy.deepcopy(prior)
            if prior['status'] not in ('starting', 'running'):
                raise RuntimeError('job outcome requires recovery; no automatic retry')
            execution = copy.deepcopy(prior)
            current = self.driver.inspect_job(execution['container'])
            if current is None:
                self._record(name, execution, 'unknown')
                raise RuntimeError('job outcome unknown; inspect target before recovery')
        else:
            # Journal before start so a crash cannot be mistaken for a fresh restore.
            self._record(name, execution, 'starting')
            if self.driver.inspect_job(execution['container']) is not None:
                self._record(name, execution, 'unknown')
                raise RuntimeError('unexpected existing job container; outcome unknown')
            try:
                self.driver.start_job(execution['container'], spec)
            except Exception:
                # Creation may have succeeded even if its response was lost.
                self._record(name, execution, 'unknown')
                raise
            self._record(name, execution, 'running')
        deadline = self.clock() + spec['timeout_seconds']
        try:
            while True:
                current = self.driver.inspect_job(execution['container'])
                if current is None:
                    self._record(name, execution, 'unknown')
                    raise RuntimeError('job container disappeared; outcome unknown')
                if not current.get('running'):
                    code = current.get('exit_code')
                    if type(code) is not int:
                        self._record(name, execution, 'unknown')
                        raise RuntimeError('job exit status unknown')
                    self._record(name, execution, 'succeeded' if code == 0 else 'failed')
                    if code != 0: raise RuntimeError('job failed; explicit recovery required')
                    return copy.deepcopy(execution)
                if self.clock() >= deadline:
                    self._stop_verified(name, execution)
                    raise RuntimeError('job timed out; writer stopped; recovery required')
                self.sleep(.2)
        except (KeyboardInterrupt, SystemExit):
            self._stop_verified(name, execution)
            raise

    def _stop_verified(self, name, execution):
        self.driver.stop_job(execution['container'])
        current = self.driver.inspect_job(execution['container'])
        if current is None or current.get('running'):
            self._record(name, execution, 'unknown')
            raise RuntimeError('job writer termination unknown; keep deployment locked for recovery')
        self._record(name, execution, 'failed')
