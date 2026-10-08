"""Real Compose event fixtures and subprocess pipes; no Docker secrets are echoed."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from deployctl.progress import Progress
from deployctl.runtime import DockerDriver


class ImageProgressTests(unittest.TestCase):
    def event(self, progress, **values):
        progress.image_event(json.dumps(values))

    def test_layer_bytes_and_states_are_derived_from_compose_events(self):
        output = io.StringIO()
        progress = Progress(stream=output)
        self.event(progress, id='abc123def456', parent_id='app', text='Downloading',
                   status='untrusted progress text', current=1048576, total=2097152, percent=99)
        self.assertIn('abc123def456', output.getvalue())
        self.assertIn('1.0 MiB / 2.0 MiB (50%)', output.getvalue())
        self.assertNotIn('99%', output.getvalue())
        self.event(progress, id='abc123def456', text='Download complete')
        self.event(progress, id='abc123def456', text='Extracting', current=1024, total=4096)
        self.event(progress, id='abc123def456', text='Pull complete')
        for state in ('Download complete', 'Extracting', 'Pull complete'):
            self.assertIn(state, output.getvalue())
        self.assertIn('1.0 KiB / 4.0 KiB (25%)', output.getvalue())
        self.assertNotIn('untrusted progress text', output.getvalue())

    def test_unknown_size_cache_and_invalid_events_never_invent_percentage_or_echo_raw_logs(self):
        output = io.StringIO()
        progress = Progress(stream=output)
        self.event(progress, id='abc123def456', text='Downloading', current=1234, percent=80)
        self.event(progress, id='abc123def456', text='Already exists')
        self.assertIn('total size unknown', output.getvalue())
        self.assertIn('Already exists', output.getvalue())
        self.assertNotIn('%', output.getvalue())
        for raw in ('private-token-marker', '[]', '{bad-json',
                    json.dumps({'id':'abc123def456','text':'Error','status':'private-token-marker'}),
                    json.dumps({'id':'private-token-marker','text':'Downloading','current':1,'total':2}),
                    json.dumps({'id':'abc123def456','text':'Downloading','current':True,'total':2}),
                    json.dumps({'id':'abc123def456','text':'Downloading','current':-1,'total':2})):
            before = output.getvalue()
            progress.image_event(raw)
            self.assertEqual(output.getvalue(), before)
        self.assertNotIn('private-token-marker', output.getvalue())

    def test_byte_updates_are_throttled_per_layer_but_transitions_are_immediate(self):
        output = io.StringIO()
        now = [0.]
        progress = Progress(stream=output, interval=1, clock=lambda: now[0])
        self.event(progress, id='abc123def456', text='Downloading', current=1, total=100)
        self.event(progress, id='abc123def456', text='Downloading', current=2, total=100)
        self.assertNotIn('(2%)', output.getvalue())
        self.event(progress, id='def123abc456', text='Downloading', current=2, total=100)
        self.assertIn('def123abc456', output.getvalue())
        self.event(progress, id='abc123def456', text='Downloading', current=100, total=100)
        self.assertIn('(100%)', output.getvalue())
        self.event(progress, id='abc123def456', text='Extracting', current=1, total=100)
        self.assertIn('Extracting', output.getvalue())
        now[0] = 2
        self.event(progress, id='abc123def456', text='Extracting', current=25, total=100)
        self.assertIn('(25%)', output.getvalue())

    def test_quiet_and_broken_display_do_not_stop_processing_events(self):
        output = io.StringIO()
        self.event(Progress(stream=output, enabled=False), id='app', text='Pulled')
        self.assertEqual(output.getvalue(), '')
        class Broken:
            def write(self, value): raise BrokenPipeError()
        self.event(Progress(stream=Broken()), id='app', text='Pulled')


@unittest.skipUnless(os.name == 'posix', 'deployment subprocess lifecycle requires Linux')
class PullProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.output = io.StringIO()
        self.progress = Progress(stream=self.output, interval=.01)
        self.driver = DockerDriver()
        self.driver.progress = self.progress

    def tearDown(self): self.tmp.cleanup()

    def script(self, code):
        script = self.base/'child.py'
        script.write_text(code)
        return [sys.executable, '-u', str(script)]

    def test_output_is_visible_before_process_exits_and_docker_config_is_preserved(self):
        ready = threading.Event()
        class Output(io.StringIO):
            def write(inner, value):
                result = super().write(value)
                if '(50%)' in value: ready.set()
                return result
        output = Output()
        self.driver.progress = Progress(stream=output)
        marker = self.base/'continue'
        command = self.script('import json,os,pathlib,time\n'
            'assert os.environ["DOCKER_CONFIG"] == "private-config-directory"\n'
            'print(json.dumps({"id":"abc123def456","text":"Downloading","current":1,"total":2}),flush=True)\n'
            f'while not pathlib.Path({str(marker)!r}).exists(): time.sleep(.01)\n'
            'print(json.dumps({"id":"app","text":"Pulled"}),flush=True)\n')
        errors = []
        def pull():
            try: self.driver._stream_pull(command, {}, timeout=3)
            except BaseException as exc: errors.append(exc)
        self.driver.docker_config = 'private-config-directory'
        worker = threading.Thread(target=pull)
        worker.start()
        try:
            self.assertTrue(ready.wait(2), 'pull output was buffered until command completion')
            self.assertTrue(worker.is_alive())
        finally:
            marker.touch();worker.join(4)
        self.assertEqual(errors, [])
        self.assertIn('Pulled', output.getvalue())
        self.assertNotIn('private-config-directory', output.getvalue())

    def test_failed_command_cannot_report_success_or_echo_registry_credentials(self):
        command = self.script('import sys\nprint("private-token-marker",file=sys.stderr)\nsys.exit(7)\n')
        with self.assertRaisesRegex(RuntimeError, 'exit 7') as caught:
            with self.progress.stage('Pulling image'):
                self.driver._stream_pull(command, timeout=2)
        self.assertIn('[failed]', self.output.getvalue())
        self.assertNotIn('[done]', self.output.getvalue())
        self.assertNotIn('private-token-marker', self.output.getvalue()+str(caught.exception))

    def test_timeout_terminates_child_and_does_not_leak_output(self):
        marker = self.base/'pid'
        command = self.script(f'import pathlib,time,os\npathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(10)\n')
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.driver._stream_pull(command, timeout=.2)
        self.assertTrue(marker.exists())
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()), 0)

    def test_keyboard_interrupt_stops_pull_process_before_returning(self):
        marker = self.base/'pid'
        command = self.script(f'import pathlib,time,os\npathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(10)\n')
        real_wait = subprocess.Popen.wait
        def interrupt(process, *args, **kwargs):
            if kwargs.get('timeout') == 3:
                # Wait on a real subprocess side effect before interrupting the caller.
                import time
                deadline = time.monotonic()+2
                while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
                raise KeyboardInterrupt()
            return real_wait(process, *args, **kwargs)
        with patch.object(subprocess.Popen, 'wait', interrupt):
            with self.assertRaises(KeyboardInterrupt): self.driver._stream_pull(command, timeout=3)
        self.assertTrue(marker.exists())
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()), 0)

    def test_oversized_untrusted_lines_are_discarded_and_following_event_is_read(self):
        command = self.script('import json\nprint("private-token-marker"*100000)\n'
            'print(json.dumps({"id":"app","text":"Pulled"}))\n')
        self.driver._stream_pull(command, timeout=3)
        self.assertIn('Pulled', self.output.getvalue())
        self.assertNotIn('private-token-marker', self.output.getvalue())

    def test_quiet_pull_uses_same_cancellation_lifecycle_and_remains_silent(self):
        marker = self.base/'quiet-pid'
        docker = self.base/'docker'
        docker.write_text(f'#!{sys.executable}\nimport sys,os,time,pathlib\n'
            'if "config" in sys.argv: sys.exit(0)\n'
            f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n'
            'time.sleep(10)\n')
        docker.chmod(0o700)
        self.driver.progress = Progress(stream=self.output, enabled=False)
        real_wait = subprocess.Popen.wait
        def interrupt(process, *args, **kwargs):
            if 'pull' in process.args and kwargs.get('timeout') == 600:
                import time
                deadline=time.monotonic()+2
                while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
                raise KeyboardInterrupt()
            return real_wait(process,*args,**kwargs)
        with patch.object(subprocess.Popen,'wait',interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.driver.pull(self.base,'fixture',{**os.environ,'PATH':str(self.base)+os.pathsep+os.environ['PATH']})
        self.assertTrue(marker.exists())
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()),0)
        self.assertEqual(self.output.getvalue(),'')

