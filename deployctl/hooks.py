"""Optional host Bash hooks with isolated environments and bounded logs."""

import hashlib
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .contract import HOOK_PATHS, validate_hook_manifest
from .release import read_hook_script
from .runtime_snapshot import reject_links, verify_snapshot

LOG_LIMIT = 64 * 1024
STARTUP_CONTROLS = {'BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'}


class HookFailure(RuntimeError):
    def __init__(self, phase, log_path, exit_code=None, timeout=False, diagnostic=False):
        self.phase, self.log_path = phase, Path(log_path)
        self.exit_code, self.timeout = exit_code, timeout
        reason = 'timed out' if timeout else f'exit {exit_code}'
        if diagnostic:
            reason = 'hook log could not be written'
        super().__init__(f'{phase} failed ({reason}); protected log: {log_path}')


def validate_hook_values(values):
    if STARTUP_CONTROLS.intersection(values):
        raise ValueError('host hooks forbid Bash startup control variables in runtime configuration')


def hook_environment(snapshot, context):
    validate_hook_values(snapshot.values)
    # Deliberately do not inherit the operator's download credentials or Python/Bash controls.
    environment = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
    if os.name != 'nt':
        import pwd
        account = pwd.getpwuid(os.getuid())
        environment.update({'HOME': account.pw_dir, 'USER': account.pw_name, 'LOGNAME': account.pw_name})
    environment.update(snapshot.values)
    environment.update(context)
    environment.update({'DEPLOYCTL_ENV_FILE': str(snapshot.directory / '.env.json'),
                        'DEPLOYCTL_PARAMS_FILE': str(snapshot.directory / '.install-params.json')})
    environment.update({f'DEPLOYCTL_PARAM_{key}': value for key, value in snapshot.install_params.items()})
    return environment


def cleanup_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Always send KILL to the group, even when the shell exited but descendants remain.
    time.sleep(.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def drain_process(process, timeout):
    tail = bytearray()
    deadline = time.monotonic() + timeout
    os.set_blocking(process.stdout.fileno(), False)
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            if time.monotonic() >= deadline:
                return bytes(tail), True
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                raw = os.read(key.fileobj.fileno(), 8192)
                if not raw:
                    selector.unregister(key.fileobj)
                tail.extend(raw)
                del tail[:-LOG_LIMIT]
            if process.poll() is not None:
                # Drain currently available bytes; a background child must not hold us here.
                while True:
                    try:
                        raw = os.read(process.stdout.fileno(), 8192)
                    except BlockingIOError:
                        break
                    if not raw:
                        break
                    tail.extend(raw)
                    del tail[:-LOG_LIMIT]
                return bytes(tail), False


def write_log(path, raw):
    reject_links(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw[-LOG_LIMIT:])
    path.chmod(0o600)


class HookRunner:
    def check(self):
        if os.name == 'nt' or not Path('/bin/bash').is_file():
            raise RuntimeError('installation hooks require Linux /bin/bash')

    def run(self, phase, descriptor, release_dir, snapshot, context):
        self.check()
        validate_hook_manifest({phase: descriptor})
        verify_snapshot(snapshot)
        raw = read_hook_script(release_dir, descriptor['path'])
        if hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ValueError('hook script integrity check failed')
        environment = hook_environment(snapshot, context)
        logs = snapshot.directory.parent.parent / 'hook-logs'
        reject_links(logs)
        logs.mkdir(mode=0o700, exist_ok=True)
        logs.chmod(0o700)
        # Independent executions (e.g. retries) never overwrite earlier hook diagnostics.
        import uuid
        log_path = logs / f'{snapshot.id}-{phase}-{uuid.uuid4().hex}.log'
        process = subprocess.Popen(['/bin/bash', '--noprofile', '--norc',
                                    str(Path(release_dir).absolute() / HOOK_PATHS[phase])],
                                   cwd=release_dir, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            tail, timed_out = drain_process(process, descriptor['timeout_seconds'])
        finally:
            try:
                cleanup_group(process)
            finally:
                process.stdout.close()
        verify_snapshot(snapshot)
        if hashlib.sha256(read_hook_script(release_dir, descriptor['path'])).hexdigest() != descriptor['sha256']:
            raise ValueError('hook script changed during execution')
        try:
            write_log(log_path, tail)
        except OSError as exc:
            raise HookFailure(phase, log_path, process.returncode, timed_out, diagnostic=True) from exc
        if timed_out or process.returncode:
            raise HookFailure(phase, log_path, process.returncode, timed_out)
