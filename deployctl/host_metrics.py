"""Best-effort Linux host snapshots; no subprocesses or sampling sleeps."""
import os
from pathlib import Path
import shutil


class HostMetrics:
    def __init__(self, deployment_root, proc=Path('/proc')):
        self.root = Path(deployment_root)
        self.proc = Path(proc)
        self.previous_cpu = None

    def sample(self):
        result = {'cpu_cores': os.cpu_count() or 0, 'cpu_percent': None,
                  'memory': None, 'disk': None}
        try:
            fields = (self.proc / 'stat').read_text(encoding='ascii').splitlines()[0].split()
            if fields[0] != 'cpu' or len(fields) < 5:
                raise ValueError('invalid CPU counters')
            values = [int(v) for v in fields[1:9]]  # guest counters are already included
            total, idle = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
            previous, self.previous_cpu = self.previous_cpu, (total, idle)
            if previous and total > previous[0] and idle >= previous[1]:
                result['cpu_percent'] = round(max(0, min(100, 100 * (1 - (idle - previous[1]) / (total - previous[0])))), 1)
        except (OSError, ValueError, IndexError):
            self.previous_cpu = None
        try:
            memory = {line.split(':')[0]: int(line.split()[1]) * 1024
                      for line in (self.proc / 'meminfo').read_text(encoding='ascii').splitlines()}
            total, available = memory['MemTotal'], memory['MemAvailable']
            if total > 0 and 0 <= available <= total:
                result['memory'] = {'total_bytes': total, 'available_bytes': available}
        except (OSError, ValueError, IndexError, KeyError):
            pass
        try:
            path = self.root
            while not path.exists() and path != path.parent:
                path = path.parent
            usage = shutil.disk_usage(path)
            result['disk'] = {'path': str(self.root), 'total_bytes': usage.total, 'available_bytes': usage.free}
        except (OSError, ValueError):
            pass
        return result
