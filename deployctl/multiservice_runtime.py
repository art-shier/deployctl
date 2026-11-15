"""Journaled, targeted execution of a validated application deployment plan.

The caller holds the application/environment lock, validates observed identities
and supplies a persistent save callback. No blanket Compose cleanup is allowed.
"""
import copy
import uuid


class ServiceExecutor:
    def __init__(self, driver, save):
        self.driver = driver
        self.save = save

    def apply(self, plan, services, jobs, previous):
        if not plan.get('applicable'):
            raise ValueError('deployment requires maintenance or recovery before apply')
        changes = {name: action for name, action in plan['services'].items() if action != 'unchanged'}
        if any(action not in ('create', 'update', 'start') for action in changes.values()):
            raise ValueError('unsupported service action')
        if any(services[name]['lifecycle'] == 'persistent' and action == 'update' for name, action in changes.items()):
            raise ValueError('persistent service requires explicit maintenance')
        journal = {'id': uuid.uuid4().hex, 'phase': 'prepared', 'action': None,
                   'previous': copy.deepcopy(previous), 'attempted': [], 'completed': [],
                   'jobs': {}, 'rollback_barrier': False}

        def record(phase, kind=None, name=None):
            journal['phase'] = phase
            journal['action'] = {'kind': kind, 'service': name} if kind else None
            self.save(copy.deepcopy(journal))

        record('prepared')
        try:
            # All images, including jobs, are pulled before runtime mutations.
            for name in sorted(set(changes) | set(jobs)):
                record('pulling', 'pull', name); self.driver.pull(name)
            record('resources', 'ensure-resources')
            self.driver.ensure_resources()
            for name in plan['order']:
                if name in jobs:
                    journal['jobs'][name] = 'unknown'
                    if not jobs[name].get('rollback_compatible', False):
                        journal['rollback_barrier'] = True
                    record('job-running', 'job', name)
                    self.driver.run_job(name)
                    journal['jobs'][name] = 'succeeded'
                    record('job-succeeded')
                    continue
                if name in changes:
                    journal['attempted'].append(name)
                    action = 'start' if changes[name] == 'start' else 'up'
                    record('applying', action, name)
                    if action == 'start': self.driver.start(name)
                    else: self.driver.up(name)
                record('checking', 'healthy', name)
                self.driver.healthy(name)
                journal['completed'].append(name)
                record('checked')
            record('succeeded')
            return journal
        except Exception:
            if journal['rollback_barrier']:
                record('recovery-required')
                raise
            try:
                for name in reversed(journal['attempted']):
                    if services[name]['lifecycle'] == 'persistent':
                        continue
                    if name in previous:
                        record('rolling-back', 'restore', name)
                        self.driver.restore(name, previous[name])
                    else:
                        record('rolling-back', 'stop', name)
                        self.driver.stop(name)
                record('rolled-back')
            except Exception as recovery:
                record('recovery-required')
                raise RuntimeError('deployment and rollback failed; inspect pending transaction') from recovery
            raise
