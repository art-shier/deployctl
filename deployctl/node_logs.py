"""Bounded node-local diagnostics. No arbitrary paths or remote shell commands."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import tempfile
import time
import uuid

from .contract import validate_name
from .runtime_snapshot import reject_links, validate_id

QUERY_BYTES = 256 * 1024
ARCHIVE_BYTES = 10 * 1024 * 1024
TASK_BYTES = 1024 * 1024
_LOCK = threading.RLock()


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def date(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid log timestamp')
    try:
        result = datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except ValueError:
        raise ValueError('invalid log timestamp') from None


def validate_query(request):
    validate_name(request.get('project'))
    validate_name(request.get('environment'), 'environment', 32)
    source = request.get('source', 'runtime')
    if source not in ('runtime', 'archives', 'archive', 'deployment', 'failure'):
        raise ValueError('invalid log source')
    if source == 'deployment':
        validate_id(request.get('task_id'))
    if source == 'archive':
        validate_id(request.get('archive_id'))
    tail = request.get('tail', 200)
    if type(tail) is not int or not 1 <= tail <= 2000:
        raise ValueError('invalid log tail')
    if not isinstance(request.get('search', ''), str) or len(request.get('search', '').encode()) > 256:
        raise ValueError('invalid log search')
    for key in ('since', 'until'):
        if request.get(key):
            date(request[key])
    if request.get('since') and request.get('until') and date(request['since']) > date(request['until']):
        raise ValueError('invalid log time range')


def select_text(text, request, limit=QUERY_BYTES):
    since = date(request['since']) if request.get('since') else None
    until = date(request['until']) if request.get('until') else None
    selected = []
    for line in text.splitlines(keepends=True):
        if request.get('search', '') not in line:
            continue
        if since or until:
            try:
                value = json.loads(line).get('timestamp') if line.startswith('{') else line.split(' ', 1)[0]
                observed = date(value)
            except (ValueError, AttributeError, TypeError):
                continue
            if (since and observed < since) or (until and observed > until):
                continue
        selected.append(line)
    tail = request.get('tail', 200)
    truncated = len(selected) > tail
    raw = ''.join(selected[-tail:]).encode('utf-8')
    if len(raw) > limit:
        raw = raw[-limit:]
        truncated = True
    return raw.decode('utf-8', errors='ignore'), truncated


def bounded_command(command, limit, timeout=10):
    """Drain output while retaining only a bounded suffix, including stderr logs."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    buffer = bytearray()
    total = 0
    def read():
        nonlocal total
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            buffer.extend(chunk)
            if len(buffer) > limit:
                del buffer[:-limit]
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=timeout)
        reader.join(timeout=1)
        if reader.is_alive() or code:
            raise RuntimeError('log_read_failed')
    except subprocess.TimeoutExpired:
        raise RuntimeError('log_read_timeout') from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=1)
        process.stdout.close()
    return bytes(buffer).decode('utf-8', errors='replace'), total > limit


class NodeLogs:
    def __init__(self, root, config_root=None):
        self.root = Path(root)
        self.config_root = Path(config_root) if config_root is not None else None

    def folder(self, project, environment):
        validate_name(project)
        validate_name(environment, 'environment', 32)
        folder = self.root / project / environment / 'logs'
        reject_links(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        return folder

    def read(self, path, limit):
        reject_links(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('log must be a regular file')
            if info.st_size > limit:
                handle.seek(-limit, os.SEEK_END)
            return handle.read(limit).decode('utf-8', errors='replace'), info.st_size > limit

    def prune(self, folder):
        for pattern, count in (('archive-*.json', 30), ('task-*.jsonl', 100)):
            files = sorted((p for p in folder.glob(pattern) if p.is_file() and not p.is_symlink()),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for i, path in enumerate(files):
                if i >= count or path.stat().st_mtime < time.time() - 7 * 86400:
                    path.unlink()
                    if pattern.startswith('archive'):
                        path.with_suffix('.log').unlink(missing_ok=True)
            # Interrupted writes never become visible metadata, but may leave files.
        for path in folder.iterdir():
            if (path.name.startswith('.pending-') or (path.name.startswith('archive-') and path.suffix == '.log' and not path.with_suffix('.json').exists())):
                if not path.is_symlink() and path.is_file() and path.stat().st_mtime < time.time() - 7 * 86400:
                    path.unlink()

    def event(self, task, stage, message, level='info'):
        validate_id(task['id'])
        with _LOCK:
            folder = self.folder(task['project'], task['environment'])
            path = folder / ('task-' + task['id'] + '.jsonl')
            reject_links(path)
            row = json.dumps(dict(timestamp=timestamp(), level=level, task_id=task['id'],
                                  stage=stage, message=message[:4096]), ensure_ascii=False) + '\n'
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            with os.fdopen(fd, 'a+b') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('task log must be a regular file')
                if os.name != 'nt':
                    os.fchmod(handle.fileno(), 0o600)
                handle.seek(max(0, info.st_size - 256))
                ended = b'"stage": "log_truncated"' in handle.read(256)
                if not ended:
                    if info.st_size + len(row.encode()) > TASK_BYTES - 256:
                        row = json.dumps(dict(timestamp=timestamp(), level='warning',
                            stage='log_truncated', message='Task log size limit reached')) + '\n'
                    if info.st_size + len(row.encode()) <= TASK_BYTES:
                        handle.write(row.encode())
            self.prune(folder)

    def containers(self, project, environment):
        from .runtime import project_name
        output, truncated = bounded_command(['docker', 'ps', '-a', '--filter',
            'label=com.docker.compose.project=' + project_name(project, environment), '--filter',
            'label=com.docker.compose.service=app', '--no-trunc', '--format',
            '{{.ID}} {{.Label "io.team-deploy.version"}}'], 16384)
        if truncated:
            raise RuntimeError('too_many_containers')
        result = []
        for line in output.splitlines():
            parts = line.split(' ', 1)
            if not re.fullmatch('[a-f0-9]{64}', parts[0]):
                raise ValueError('invalid container identity')
            result.append(dict(id=parts[0], version=parts[1] if len(parts) > 1 else ''))
        if len(result) > 4:
            raise RuntimeError('too_many_containers')
        return result

    def runtime_text(self, container, request, limit=QUERY_BYTES):
        command = ['docker', 'logs', '--timestamps', '--tail', str(request.get('tail', 200))]
        for key in ('since', 'until'):
            if request.get(key):
                command.extend(['--' + key, request[key]])
        command.append(container['id'])
        return bounded_command(command, limit)

    def atomic_write(self, path, raw):
        fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                if os.name != 'nt':
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def archive(self, project, environment):
        for container in self.containers(project, environment):
            text, truncated = self.runtime_text(container, {'tail': 'all'}, ARCHIVE_BYTES)
            folder = self.folder(project, environment)
            identity = uuid.uuid4().hex
            # Reserve metadata space; cap UTF-8 bytes after replacement decoding.
            raw = text.encode('utf-8')
            budget = ARCHIVE_BYTES - 4096
            if len(raw) > budget:
                raw = raw[-budget:].decode('utf-8', errors='ignore').encode('utf-8')
                truncated = True
            record = dict(id=identity, version=container['version'], container_id=container['id'],
                          captured_at=timestamp(), truncated=truncated)
            metadata = json.dumps(record, ensure_ascii=False).encode('utf-8')
            if len(metadata) > 4096:
                raise ValueError('invalid archive metadata')
            path = folder / ('archive-' + identity + '.json')
            try:
                self.atomic_write(path.with_suffix('.log'), raw)
                # Publishing metadata is the commit; readers never see partial JSON.
                self.atomic_write(path, metadata)
            except BaseException:
                path.with_suffix('.log').unlink(missing_ok=True)
                raise
            with _LOCK:
                self.prune(folder)

    def archive_metadata(self, path):
        raw, clipped = self.read(path, 4096)
        if clipped:
            raise ValueError('invalid archive metadata')
        item = json.loads(raw)
        if item['id'] != path.stem.removeprefix('archive-') or not path.with_suffix('.log').is_file():
            raise ValueError('incomplete archive')
        return {k: item[k] for k in ('id', 'version', 'container_id', 'captured_at', 'truncated')}

    def failure_hook(self, home, request):
        # Derive the only allowed directory from the managed project identity.
        # Never follow an arbitrary path supplied through an exception message.
        if self.config_root is None:
            return '', False
        try:
            raw, clipped = self.read(home / 'last-failure.json', 4096)
            if clipped:
                return '', False
            failure = json.loads(raw)
            if not isinstance(failure, dict):
                return '', False
            phase = failure.get('phase')
            if phase not in ('pre_install', 'post_install'):
                return '', False
            match = re.fullmatch(r'(?:pre_install|post_install) failed \([^\n]{1,160}\); protected log: ([^\n]{1,2048})', failure.get('reason', ''))
            if not match:
                return '', False
            supplied = Path(match[1])
            if not re.fullmatch(r'[a-f0-9]{32}-' + phase + r'-[a-f0-9]{32}\.log', supplied.name):
                return '', False
            expected = self.config_root / request['project'] / request['environment'] / 'hook-logs' / supplied.name
            if supplied != expected.absolute():
                return '', False
            text, clipped = self.read(expected, 64 * 1024)
            return '\n[Installation hook diagnostics]\n' + text, clipped
        except (OSError, ValueError, TypeError):
            # Missing/corrupt hook diagnostics must not hide the application log.
            return '', False

    def query(self, request):
        validate_query(request)
        result = dict(text='', truncated=False, sources=[], error='')
        folder = self.folder(request['project'], request['environment'])
        source = request.get('source', 'runtime')
        try:
            with _LOCK:
                self.prune(folder)
            # File contents are read outside the event lock. Listing reads only metadata.
            if source == 'archives':
                for path in folder.glob('archive-*.json'):
                    try:
                        result['sources'].append(self.archive_metadata(path))
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                result['sources'].sort(key=lambda item: item['captured_at'], reverse=True)
                return result
            if source == 'archive':
                path = folder / ('archive-' + request['archive_id'] + '.json')
                record = self.archive_metadata(path)
                text, clipped = self.read(path.with_suffix('.log'), ARCHIVE_BYTES)
                clipped = clipped or record['truncated']
            elif source == 'deployment':
                text, clipped = self.read(folder / ('task-' + request['task_id'] + '.jsonl'), TASK_BYTES)
                clipped = clipped or '"stage": "log_truncated"' in text[-256:]
            elif source == 'failure':
                text, clipped = self.read(folder.parent / 'last-failure.log', QUERY_BYTES)
                hook, hook_clipped = self.failure_hook(folder.parent, request)
                text += hook
                clipped = clipped or hook_clipped
            if source == 'runtime':
                containers = self.containers(request['project'], request['environment'])
                if not containers:
                    result['error'] = 'container_unavailable'
                    return result
                # Runtime query targets the managed app container, never a supplied ID.
                if len(containers) != 1:
                    raise RuntimeError('ambiguous_container')
                text, clipped = self.runtime_text(containers[0], request)
                # Docker tail may have omitted earlier lines; make that boundary explicit.
                clipped = clipped or len(text.splitlines()) >= request.get('tail', 200)
            result['text'], limited = select_text(text, request)
            result['truncated'] = clipped or limited
        except FileNotFoundError:
            result['error'] = 'log_unavailable'
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            result['error'] = 'log_read_failed'
        return result
