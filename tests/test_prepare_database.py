import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_database', ROOT/'control-deploy/prepare_database.py')
database = None
if (ROOT/'control-deploy/prepare_database.py').exists():
    database = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(database)
spec = importlib.util.spec_from_file_location('bootstrap_config_for_database', ROOT/'control-deploy/bootstrap_config.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

def snapshot():
    return {'project': 'shier', 'environment': 'prod', 'values': {
        'db_address': 'db.example.test', 'db_port': '5432', 'db_username': 'administrator', 'db_password': 'do-not-use',
        'ctl_db_name': 'ctl', 'ctl_db_username': 'ctl_app', 'ctl_db_password': 'p@ss:/?#$ value'}}

class DatabasePreparationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(database, 'ConfigHub database preparation is not implemented')

    def test_dedicated_account_and_encoding_without_shared_password_fallback(self):
        self.assertEqual(database.database_url(snapshot()),
            'postgresql://ctl_app:p%40ss%3A%2F%3F%23%24%20value@db.example.test:5432/ctl?sslmode=require&connect_timeout=10')
        for key in ('ctl_db_password', 'ctl_db_username'):
            value = snapshot(); del value['values'][key]
            with self.assertRaises(ValueError): database.database_url(value)

    def test_wrong_scope_bad_target_and_insecure_tls_rejected(self):
        for key, value in [('db_address','host/other'),('db_port','65536'),('ctl_db_name','postgres'),('ctl_db_sslmode','disable')]:
            config = snapshot(); config['values'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): database.database_url(config)
        config = snapshot(); config['project'] = 'other'
        with self.assertRaises(ValueError): database.database_url(config)

    @unittest.skipIf(os.name == 'nt', 'Linux ConfigHub executable')
    def test_actual_cli_transport_generates_config_and_masks_failed_secret_output(self):
        with tempfile.TemporaryDirectory() as temp:
            cli=Path(temp)/'confighub'
            cli.write_text('#!'+sys.executable+'\nimport json,sys\nassert sys.argv[1:]==["--server","https://config.test","export","--project","shier","--env","prod","--format","json"]\nprint('+repr(json.dumps(snapshot()))+')\n')
            cli.chmod(0o700)
            with patch.dict(os.environ,{'PATH':temp+os.pathsep+os.environ['PATH']}):
                value=database.fetch_config('shier','prod','https://config.test')
                self.assertEqual(database.database_url(value),
                    'postgresql://ctl_app:p%40ss%3A%2F%3F%23%24%20value@db.example.test:5432/ctl?sslmode=require&connect_timeout=10')
                cli.write_text('#!'+sys.executable+'\nimport sys\nprint("private-response-secret",file=sys.stderr)\nsys.exit(1)\n')
                with self.assertRaises(ValueError) as caught:database.fetch_config('shier','prod','https://config.test')
                self.assertNotIn('private-response-secret',str(caught.exception))

    @unittest.skipIf(os.name == 'nt' or not hasattr(os,'geteuid') or os.geteuid()!=0, 'Root ownership boundaries')
    def test_container_owned_keys_supported_but_mutable_ancestors_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            home,output=Path(temp)/'platform',Path(temp)/'database.url'
            bootstrap.prepare(home,'fixture')
            state=home/'server-state.json';state.write_text(json.dumps({'schema_version':1,'current':None,'pending':{'version':'v1.7.0'}}));state.chmod(0o600)
            os.chown(home/'keys/database.url',10001,10001);os.chown(home/'keys',10001,10001)
            with patch.object(database.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='',stderr='')):
                database.prepare(snapshot(),output,home,repair=True)
            self.assertEqual((home/'keys/database.url').stat().st_uid,10001)
            self.assertEqual((home/'keys/database.url').stat().st_mode & 0o777,0o600)
            ancestor=Path(temp)/'untrusted';ancestor.mkdir(mode=0o700);os.chown(ancestor,10001,10001)
            with self.assertRaises(ValueError):database.prepare(snapshot(),ancestor/'database.url',Path(temp)/'other')

    @unittest.skipIf(os.name == 'nt', 'Linux private files and bootstrap')
    def test_new_install_generates_private_url_and_bootstrap_uses_external_database(self):
        with tempfile.TemporaryDirectory() as temp:
            home, output = Path(temp)/'platform', Path(temp)/'config/database.url'
            database.prepare(snapshot(), output, home)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            cfg = bootstrap.prepare(home, 'fixture', api_port=8084, database_url_file=output)
            self.assertTrue(cfg['external_database'])
            self.assertEqual(cfg['api_port'],8084)
            self.assertEqual((home/'keys/database.url').read_bytes(),output.read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Linux initial install recovery')
    def test_failed_initial_install_switch_preserves_keys_port_and_pending_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            home, output = Path(temp)/'platform', Path(temp)/'database.url'
            before = bootstrap.prepare(home, 'fixture', api_port=8084)
            key = home/'keys/owner.token'; key.write_text('unchanged-owner'); key.chmod(0o600)
            state = home/'server-state.json'
            state.write_text(json.dumps({'schema_version':1,'current':None,'pending':{'version':'v1.7.0','sha256':'a'*64,'image':'fixture'}}))
            state.chmod(0o600); old_state=state.read_bytes()
            with patch.object(database.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='',stderr='')):
                database.prepare(snapshot(),output,home,repair=True)
            after=bootstrap.prepare(home,'fixture',api_port=8084,database_url_file=output)
            self.assertTrue(after['external_database'])
            self.assertEqual(after['api_port'],8084)
            self.assertEqual(after['registry_secret'],before['registry_secret'])
            self.assertEqual(key.read_text(),'unchanged-owner')
            self.assertEqual(state.read_bytes(),old_state)
            database.prepare(snapshot(),output,home,repair=True)

    @unittest.skipIf(os.name == 'nt', 'Linux recovery and file protections')
    def test_data_success_running_container_and_missing_opt_in_block_switch(self):
        for condition in ('data','success','container','no_opt_in'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as temp:
                home, output=Path(temp)/'platform',Path(temp)/'database.url'
                bootstrap.prepare(home,'fixture')
                state=home/'server-state.json'
                state.write_text(json.dumps({'schema_version':1,'current':{'version':'v1.7.0'} if condition=='success' else None,'pending':{'version':'v1.7.0'}}));state.chmod(0o600)
                if condition=='data':
                    (home/'database').mkdir();(home/'database/PG_VERSION').write_text('16')
                old={name:(home/name).read_bytes() for name in ('instance.json','server-state.json','keys/database.url')}
                process=subprocess.CompletedProcess([],0,stdout='abcdef123456\n' if condition=='container' else '',stderr='')
                with patch.object(database.subprocess,'run',return_value=process),self.assertRaises(ValueError):
                    database.prepare(snapshot(),output,home,repair=condition!='no_opt_in')
                self.assertFalse(output.exists())
                for name,raw in old.items():self.assertEqual((home/name).read_bytes(),raw)

    @unittest.skipIf(os.name == 'nt', 'Linux symlink and permission checks')
    def test_output_symlink_and_public_file_refused_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'existing';destination.write_text('retain');destination.chmod(0o600)
            linked=Path(temp)/'link';linked.symlink_to(destination)
            with self.assertRaises(ValueError): database.prepare(snapshot(),linked,Path(temp)/'platform')
            self.assertEqual(destination.read_text(),'retain')
            destination.chmod(0o644)
            with self.assertRaises(ValueError): database.prepare(snapshot(),destination,Path(temp)/'platform')
            self.assertEqual(destination.read_text(),'retain')

    @unittest.skipIf(os.name == 'nt', 'Linux instance-file protection')
    def test_output_cannot_overwrite_instance_state_or_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'platform';home.mkdir(mode=0o700)
            state=home/'server-state.json';state.write_text('retain-state');state.chmod(0o600)
            with self.assertRaises(ValueError): database.prepare(snapshot(),state,home)
            self.assertEqual(state.read_text(),'retain-state')

    @unittest.skipIf(os.name == 'nt', 'Linux ancestor permissions')
    def test_mutable_parent_created_during_output_setup_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'new/database.url';original=Path.mkdir
            def mkdir(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if path==output.parent:path.chmod(0o777)
                return result
            with patch.object(Path,'mkdir',mkdir),self.assertRaises(ValueError):
                database.prepare(snapshot(),output,Path(temp)/'platform')
            self.assertFalse(output.exists())

if __name__=='__main__': unittest.main()
