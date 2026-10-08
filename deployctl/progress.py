"""Safe stderr feedback for synchronous deployment work; never prints raw logs."""
from contextlib import contextmanager
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
        stopped = threading.Event()
        self._emit(f'[working] {label}')
        def heartbeat():
            while not stopped.wait(self.interval):
                self._emit(f'[waiting] {label} ({max(0, self.clock() - started):.0f}s elapsed; still running)')
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
