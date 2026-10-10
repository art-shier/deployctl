from contextlib import redirect_stdout,redirect_stderr
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from deployctl.cli import main,parser
from deployctl.platform_credentials import Credentials
from test_static_runtime import resolution

@unittest.skipIf(os.name=='nt','Linux static CLI file deployment')
class StaticCLITests(unittest.TestCase):
    def setUp(self):
        parser()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.base.chmod(0o755);self.root=self.base/'managed';self.target=self.base/'public/a'
        self.config=self.base/'private'
        self.package=self.base/'v1.zip'
        with zipfile.ZipFile(self.package,'w') as archive:archive.writestr('index.html',b'one')

    def run_cli(self,*argv):
        out=io.StringIO();err=io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            code=main([*argv,'--root',str(self.root),'--config-root',str(self.config)])
        return code,out.getvalue(),err.getvalue()

    def test_unified_install_upgrade_and_offline_local_commands(self):
        with patch('deployctl.platform_credentials.Credentials.load',return_value=Credentials('https://ctl.test','private-test-token')),patch('deployctl.platform_client.PlatformClient') as api,patch('deployctl.runtime.DockerDriver.check',side_effect=AssertionError('Docker called')),patch('deployctl.cli.report_receipt'):
            client=api.return_value;client.server='https://ctl.test'
            client.resolve.return_value=resolution(self.package)
            client.download_release.return_value=self.package
            code,out,err=self.run_cli('install','project-a','--prod','--quiet','--target-dir',str(self.target))
            self.assertEqual(code,0,err);self.assertIn('deployed',out);self.assertEqual((self.target/'index.html').read_bytes(),b'one')
            second=self.base/'v2.zip'
            with zipfile.ZipFile(second,'w') as archive:archive.writestr('index.html',b'two')
            client.resolve.return_value=resolution(second,'v2.0.0');client.download_release.return_value=second
            code,out,err=self.run_cli('upgrade','project-a','--prod','--quiet');self.assertEqual(code,0,err)
            self.assertEqual((self.target/'index.html').read_bytes(),b'two')
        # These commands work without credentials or a reachable control plane.
        with patch('deployctl.platform_credentials.Credentials.load',side_effect=AssertionError('network credentials accessed')):
            code,out,err=self.run_cli('rollback','project-a','--prod');self.assertEqual(code,0,err)
            self.assertEqual((self.target/'index.html').read_bytes(),b'one')
            code,out,err=self.run_cli('status','project-a');self.assertEqual(code,0,err);self.assertIn('static',out)
            code,out,err=self.run_cli('logs','project-a');self.assertEqual(code,0,err);self.assertIn('install',out)
            code,out,err=self.run_cli('stop','project-a');self.assertEqual(code,1);self.assertIn('static',err)

    def test_static_rejects_docker_flags_before_download(self):
        with patch('deployctl.platform_credentials.Credentials.load',return_value=Credentials('https://ctl.test','private-test-token')),patch('deployctl.platform_client.PlatformClient') as api:
            client=api.return_value;client.resolve.return_value=resolution(self.package,target=self.target)
            client.download_release.side_effect=AssertionError('irrelevant flags triggered download')
            for flag,value in (('--env-var','A=b'),('--set','A=b'),('--unset-env','A'),('--port','8080'),('--bind','127.0.0.1')):
                with self.subTest(flag=flag):
                    command='upgrade' if flag=='--unset-env' else 'install'
                    code,out,err=self.run_cli(command,'project-a','--prod',flag,value)
                    self.assertEqual(code,1);self.assertIn('static',err)
