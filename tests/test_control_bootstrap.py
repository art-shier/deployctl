import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

path=Path(__file__).resolve().parents[1]/'control-deploy/bootstrap_config.py'
spec=importlib.util.spec_from_file_location('control_bootstrap',path)
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_repeated_generation_keeps_password_and_database(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'platform'
            if os.name=='nt': self.skipTest('production bootstrap requires POSIX paths')
            first=bootstrap.prepare(home,'fixture-image',origin='http://127.0.0.1:8080',registry_host='127.0.0.1:5000')
            dsn=(home/'keys/database.url').read_bytes()
            second=bootstrap.prepare(home,'next-image')
            self.assertEqual(first,second);self.assertEqual(dsn,(home/'keys/database.url').read_bytes())
            self.assertEqual((home/'compose.env').stat().st_mode&0o777,0o600)
            self.assertIn('next-image',(home/'compose.env').read_text())
            with self.assertRaises(ValueError):bootstrap.prepare(home,'fixture-image',origin='https://other.test')

    @unittest.skipIf(os.name=='nt','Linux bootstrap permissions')
    def test_insecure_input_and_database_switch_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'platform'
            with self.assertRaises(ValueError):bootstrap.prepare(home,'fixture',origin='http://public.test')
            bootstrap.prepare(home,'fixture',origin='https://ctl.test')
            dsn=Path(temp)/'db.url';dsn.write_text('postgres://fixture@localhost/other');dsn.chmod(0o600)
            with self.assertRaises(ValueError):bootstrap.prepare(home,'fixture',database_url_file=dsn)
            public=Path(temp)/'public';public.mkdir(mode=0o755)
            with self.assertRaises(ValueError):bootstrap.prepare(public,'fixture')

if __name__=='__main__':unittest.main()
