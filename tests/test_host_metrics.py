import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deployctl.host_metrics import HostMetrics
from deployctl.node_agent import Agent

class HostMetricsTests(unittest.TestCase):
    def test_cpu_delta_memory_available_and_deployment_filesystem(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'stat').write_text('cpu  10 0 10 80 0 0 0 0 10 0\n',encoding='ascii')
            (root/'meminfo').write_text('MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n',encoding='ascii')
            sampler=HostMetrics(root/'missing'/'deployments',root)
            first=sampler.sample()
            self.assertIsNone(first['cpu_percent'])
            self.assertEqual(first['memory'],{'total_bytes':1024000,'available_bytes':409600})
            self.assertGreater(first['disk']['total_bytes'],0)
            self.assertEqual(first['disk']['path'],str(root/'missing'/'deployments'))
            (root/'stat').write_text('cpu  30 0 20 150 0 0 0 0 30 0\n',encoding='ascii')
            self.assertEqual(sampler.sample()['cpu_percent'],30)
            (root/'stat').write_text('cpu  1 0 0 1\n',encoding='ascii')
            self.assertIsNone(sampler.sample()['cpu_percent'])

    def test_unavailable_metrics_are_null_not_zero(self):
        with tempfile.TemporaryDirectory() as temp, patch('deployctl.host_metrics.shutil.disk_usage',side_effect=OSError):
            result=HostMetrics(temp,Path(temp)).sample()
            for key in ('cpu_percent','memory','disk'):
                self.assertIsNone(result[key])

    def test_heartbeat_negotiates_before_sending_metrics(self):
        class Client:
            supported=False
            bodies=[]
            def json(self,method,path,body):
                self.bodies.append(body)
                return {'enabled':True,'instances':[],'metrics_supported':self.supported}
        with tempfile.TemporaryDirectory() as temp:
            client=Client()
            worker=Agent(Path(temp)/'agent','a'*32,client,object())
            worker.heartbeat(sample=False)
            self.assertNotIn('metrics',client.bodies[-1])
            client.supported=True
            worker.heartbeat(sample=False)
            self.assertNotIn('metrics',client.bodies[-1])
            worker.heartbeat(sample=False)
            self.assertIn('metrics',client.bodies[-1])
