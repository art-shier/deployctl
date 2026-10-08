"""Safe stderr feedback for synchronous deployment work; never prints raw logs."""
from contextlib import contextmanager
import json
import re
import sys
import threading
import time


class Progress:
    def __init__(self, stream=None, enabled=True, interval=5, clock=time.monotonic):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = enabled
        self.interval = interval
        self.clock = clock
        self._lock = threading.RLock()
        self._inline = False
        self._width = 0
        self._last_download = None
        self._image_layers = {}
        self._last_image_at = None

    def _emit(self, text, inline=False):
        if not self.enabled:
            return
        with self._lock:
            try:
                if inline:
                    self.stream.write('\r' + text.ljust(self._width))
                    self._width = len(text)
                else:
                    self.stream.write(('\n' if self._inline else '') + text + '\n')
                    self._width = 0
                self._inline = inline
                self.stream.flush()
            except (OSError, ValueError):
                self.enabled = False

    @contextmanager
    def stage(self, label):
        if not self.enabled:
            yield
            return
        started = self.clock()
        self._image_layers = {}
        self._last_image_at = None
        stopped = threading.Event()
        self._emit(f'[working] {label}')
        def heartbeat():
            while not stopped.wait(self.interval):
                now = self.clock()
                last = self._last_image_at
                if last is not None and now - last < self.interval:
                    continue
                detail = 'still running' if last is None else f'no new image progress for {max(0, now - last):.0f}s'
                self._emit(f'[waiting] {label} ({max(0, now - started):.0f}s elapsed; {detail})')
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        status = 'done'
        try:
            yield
        except BaseException as error:
            status = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
            raise
        finally:
            stopped.set()
            worker.join()
            self._emit(f'[{status}] {label} ({max(0, self.clock() - started):.1f}s)')

    def downloaded(self, current, total, force=False):
        if not self.enabled:
            return
        now = self.clock()
        with self._lock:
            if (not force and self._last_download is not None and current != 0 and current != total
                    and now - self._last_download < self.interval):
                return
            self._last_download = now
            try:
                tty = self.stream.isatty()
            except (OSError, ValueError, AttributeError):
                tty = False
            if total:
                percent = min(100, int(current * 100 / total))
                text = f'Release download: {current} / {total} bytes ({percent}%)'
                if tty:
                    filled = percent // 5
                    text = '[' + '#' * filled + '-' * (20 - filled) + '] ' + text
            else:
                text = f'Release download: {current} bytes (total size unknown)'
            self._emit(text, inline=tty)

    def image_event(self, raw):
        """Render only known Compose pull states and numeric counters, never raw logs."""
        if not self.enabled or len(raw) > 16384:
            return
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if not isinstance(event, dict) or event.get('tail') or event.get('dry-run'):
            return
        identifier, state = event.get('id'), event.get('text')
        if not isinstance(identifier, str) or not isinstance(state, str):
            return
        if identifier == 'app':
            if state not in ('Pulling', 'Pulled'):
                return
            text = f'Image: {state}'
        else:
            if not re.fullmatch(r'[a-f0-9]{12,64}', identifier):
                return
            if state not in ('Preparing', 'Waiting', 'Pulling fs layer', 'Downloading',
                             'Download complete', 'Extracting', 'Verifying Checksum',
                             'Already exists', 'Pull complete'):
                return
            text = f'Image layer {identifier[:12]}: {state}'
        current, total = event.get('current', 0), event.get('total', 0)
        if any(type(value) is not int or not 0 <= value <= 2**63-1 for value in (current, total)):
            return
        numeric = state in ('Downloading', 'Extracting')
        if numeric:
            if total:
                percent = min(100, current * 100 // total)
                text += f' {self._size(current)} / {self._size(total)} ({percent}%)'
            else:
                text += f' {self._size(current)} (total size unknown)'
        now = self.clock()
        with self._lock:
            previous = self._image_layers.get(identifier)
            if previous and previous[0] == state:
                if previous[2:] == (current, total):
                    return
                self._last_image_at = now
                if now - previous[1] < min(1, self.interval) and not (total and current >= total):
                    return
            self._last_image_at = now
            self._image_layers[identifier] = (state, now, current, total)
            try:
                tty = self.stream.isatty()
            except (OSError, ValueError, AttributeError):
                tty = False
            self._emit(text, inline=tty and numeric)

    @staticmethod
    def _size(value):
        if value < 1024:
            return f'{value} bytes'
        for unit in ('KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'):
            value /= 1024
            if value < 1024:
                return f'{value:.1f} {unit}'
