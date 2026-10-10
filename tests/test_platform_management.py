"""Management commands exercise the HTTP boundary and private local inputs."""
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
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

from deployctl.cli import main
from deployctl.platform_credentials import Credentials

TOKEN = 'private_management_fixture_token'
SECRET = 'private-password-$literal'
PROJECT = {'slug':'notes','group':'team','name':'Notes','description':'retain description',
           'repository':'https://github.com/example/notes','image_repository':'ctl.test/notes',
           'default_environment':'staging','created_at':'2026-10-09T00:00:00Z'}
GROUP = {'slug':'team','name':'Team','description':'retain group description','created_at':'2026-10-09T00:00:00Z'}


class ManagementTests(unittest.TestCase):
    def test_docker_management_remains_compatible_with_legacy_server_fields(self):
        self.legacy_project_fields=True
        self.assert_success('project','create','notes','--group','team')
        self.assert_success('project','update','notes','--name','Changed')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.config = self.folder/'credentials/client.json'
        self.hits = []
        self.project = copy.deepcopy(PROJECT)
        self.group = copy.deepcopy(GROUP)
        self.revision = 3
        self.fail_status = None
        self.invalid_response = False
        self.malformed_defaults = False
        self.environment_missing = False
        self.delete_status = 204
        self.legacy_project_fields = False
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def send(self,status,value=None):
                self.send_response(status); self.end_headers()
                if value is not None: self.wfile.write(json.dumps(value).encode())
            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
                fixture.hits.append((self.command,self.path,body,self.headers.get('Authorization')))
                if self.headers.get('Authorization') != 'Bearer '+TOKEN:
                    return self.send(403,{'error':SECRET})
                if fixture.fail_status:
                    return self.send(fixture.fail_status,{'error':SECRET})
                if fixture.invalid_response:
                    return self.send(200,{'unexpected':SECRET})
                if self.command == 'DELETE': return self.send(fixture.delete_status)
                if self.path in ('/api/v1/projects','/api/v1/projects/notes'):
                    if self.command in ('POST','PATCH'):
                        if fixture.legacy_project_fields and 'deployment_type' in body:
                            return self.send(400,{'code':'invalid_input'})
                        fixture.project.update(body)
                    return self.send(200 if self.command != 'POST' else 201,
                        [fixture.project] if self.path.endswith('/projects') and self.command=='GET' else fixture.project)
                if self.path == '/api/v1/projects/notes/group':
                    fixture.project['group']=body['group']; return self.send(200,fixture.project)
                if self.path in ('/api/v1/groups','/api/v1/groups/team'):
                    if self.command in ('POST','PATCH'): fixture.group.update(body)
                    return self.send(200 if self.command != 'POST' else 201,
                        [fixture.group] if self.path.endswith('/groups') and self.command=='GET' else fixture.group)
                if self.path.endswith('/environments'): return self.send(200,['prod','staging'])
                if '/environments/' in self.path:
                    if self.command == 'GET' and fixture.environment_missing: return self.send(404,{'error':SECRET})
                    if self.command == 'PUT':
                        expected = 0 if fixture.environment_missing else fixture.revision
                        if body['expected_revision'] != expected: return self.send(409,{'error':SECRET})
                        fixture.environment_missing=False;fixture.revision=expected+1
                    environment=self.path.split('/environments/')[1].split('?')[0]
                    value={'id':'a'*32,'environment':environment,'revision':fixture.revision,
                        'runtime_env':[{'key':'DB_PASSWORD','secret':True,'configured':True,'value':SECRET},
                                       {'key':'DB_HOST','secret':False,'configured':True,'value':'db.test'}],
                        'install_params':[],'created_at':'2026-10-09T00:00:00Z'}
                    if '/projects/' in self.path:
                        value.update(target_version='stable',deployment_defaults={},
                            inherited_runtime_env=[{'key':'GROUP_SECRET','secret':True,'configured':True,'value':SECRET}],
                            inherited_install_params=[],group_source={'slug':'team','id':'b'*32,'revision':2})
                        if fixture.malformed_defaults: value['deployment_defaults']={SECRET:SECRET}
                    return self.send(200,value)
                self.send(404,{'error':SECRET})
            do_GET=do_POST=do_PATCH=do_PUT=do_DELETE=handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.02},daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        Credentials.save(self.config,f'http://127.0.0.1:{self.server.server_port}',TOKEN)

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def invoke(self,*args):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            try: code=main([*args,'--client-config',str(self.config)])
            except SystemExit as exc: code=exc.code
        return code,out.getvalue(),err.getvalue()

    def assert_success(self,*args):
        code,out,err=self.invoke(*args)
        self.assertEqual(code,0,err)
        self.assertNotIn(TOKEN,out+err)
        return json.loads(out)

    def private_file(self,name,raw):
        file=self.folder/name;file.write_bytes(raw);file.chmod(0o600);return file

    def test_project_metadata_update_preserves_unselected_fields_and_omits_group(self):
        result=self.assert_success('project','update','notes','--name','Renamed')
        method,path,body,auth=self.hits[-1]
        self.assertEqual((method,path,auth),('PATCH','/api/v1/projects/notes','Bearer '+TOKEN))
        self.assertNotIn('group',body)
        self.assertEqual(body['description'],PROJECT['description'])
        self.assertEqual(body['default_environment'],'staging')
        self.assertEqual(result['name'],'Renamed')
        self.assertEqual(result['group'],'team')

    def test_project_create_move_and_confirmed_deletion(self):
        self.assert_success('project','create','notes','--group','team')
        self.assertEqual(self.hits[-1][2]['group'],'team')
        self.assertNotIn('image_repository',self.hits[-1][2])
        self.assert_success('project','move','notes','--group','other')
        self.assertEqual(self.hits[-1][1:3],('/api/v1/projects/notes/group',{'group':'other','expected_group':'team'}))
        count=len(self.hits)
        self.assertNotEqual(self.invoke('project','delete','notes','--confirm','other')[0],0)
        self.assertEqual(len(self.hits),count)
        self.assert_success('project','delete','notes','--confirm','notes')
        self.assertEqual(self.hits[-1][:2],('DELETE','/api/v1/projects/notes'))

    def test_group_metadata_crud_and_project_listing(self):
        self.assertEqual(self.assert_success('project','list')[0]['slug'],'notes')
        self.assertEqual(self.assert_success('project','show','notes')['group'],'team')
        self.assertEqual(self.assert_success('group','list')[0]['slug'],'team')
        self.assertEqual(self.assert_success('group','show','team')['name'],'Team')
        self.assert_success('group','create','team','--name','New team','--description',GROUP['description'])
        self.assert_success('group','update','team','--name','Renamed team')
        self.assertEqual(self.hits[-1][2]['description'],GROUP['description'])
        self.assert_success('group','delete','team','--confirm','team')
        self.assertEqual(self.hits[-1][:2],('DELETE','/api/v1/groups/team'))

    def test_masked_default_environment_reveal_and_group_explicit_environment(self):
        result=self.assert_success('project-config','get','notes')
        self.assertEqual(result['environment'],'staging')
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertNotIn('?',self.hits[-1][1])
        result=self.assert_success('project-config','get','notes','--prod','--reveal')
        self.assertEqual(result['runtime_env'][0]['value'],SECRET)
        self.assertTrue(self.hits[-1][1].endswith('/prod?reveal=true'))
        count=len(self.hits)
        self.assertNotEqual(self.invoke('group-config','get','team')[0],0)
        self.assertEqual(len(self.hits),count)
        result=self.assert_success('group-config','get','team','--prod')
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertEqual(self.assert_success('project-config','list','notes'),['prod','staging'])

    def test_secret_value_file_preserves_literal_whitespace_and_secret_class(self):
        value=self.private_file('password.txt',('  '+SECRET+'  \r\n').encode())
        result=self.assert_success('project-config','set','notes','DB_PASSWORD','--value-file',str(value),'--prod')
        body=self.hits[-1][2]
        self.assertEqual(body,{'expected_revision':3,'runtime_env':[{'key':'DB_PASSWORD','operation':'set','value':'  '+SECRET+'  '}]})
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertEqual(result['revision'],4)
        self.assert_success('project-config','set','notes','NEW_SECRET','value','--prod','--secret')
        self.assertTrue(self.hits[-1][2]['runtime_env'][0]['secret'])
        self.assert_success('project-config','unset','notes','PARAM','--kind','install','--prod')
        self.assertEqual(self.hits[-1][2]['install_params'],[{'key':'PARAM','operation':'remove'}])

    def test_apply_uses_expected_revision_and_does_not_retry_conflict(self):
        payload={'runtime_env':[{'key':'DB_PASSWORD','operation':'set','value':SECRET}],
                 'deployment_defaults':{'host_port':8085},'target_version':'stable'}
        source=self.private_file('config.json',json.dumps(payload).encode())
        code,out,err=self.invoke('project-config','apply','notes','--prod','--file',str(source),'--expected-revision','2')
        self.assertEqual(code,1)
        self.assertEqual(sum(method=='PUT' for method,*_ in self.hits),1)
        self.assertIn('409',err);self.assertIn('reload',err.lower())
        self.assertNotIn(SECRET,out+err)
        self.assertEqual(self.revision,3)
        self.assert_success('project-config','apply','notes','--prod','--file',str(source))
        self.assertEqual(self.hits[-1][2],{'expected_revision':3,**payload})

    def test_new_environment_and_group_configuration(self):
        self.environment_missing=True
        self.assert_success('group-config','set','team','DB_HOST','db.test','--env','new-env','--public')
        self.assertEqual(self.hits[-1][2],{'expected_revision':0,'runtime_env':[{'key':'DB_HOST','operation':'set','value':'db.test','secret':False}]})
        self.assert_success('group-config','unset','team','DB_HOST','--env','new-env')
        self.assertEqual(self.hits[-1][2]['expected_revision'],1)

    def test_untrusted_response_and_permission_errors_are_not_printed(self):
        self.invalid_response=True
        code,out,err=self.invoke('project','show','notes')
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertNotIn(SECRET,err)
        self.invalid_response=False;self.fail_status=403
        code,out,err=self.invoke('project-config','set','notes','DB_PASSWORD',SECRET,'--prod','--secret')
        self.assertEqual(code,1);self.assertNotIn(SECRET,out+err)
        self.assertIn('403',err)

    def test_malformed_defaults_do_not_echo_response_values_or_keys(self):
        self.malformed_defaults=True
        code,out,err=self.invoke('project-config','get','notes','--prod')
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertNotIn(SECRET,err)

    def test_delete_requires_the_archive_protocol_response(self):
        self.delete_status=200
        code,out,err=self.invoke('project','delete','notes','--confirm','notes')
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertNotIn(SECRET,err)

    def test_input_files_are_bounded_utf8_and_exactly_single_line(self):
        for raw in [b'\xff',b'x'*65537,b'x'*4097,b'literal\nsecond',b'literal\r\n\r\n']:
            source=self.private_file('invalid-value.txt',raw)
            count=len(self.hits)
            code,out,err=self.invoke('project-config','set','notes','DB_PASSWORD','--prod','--secret','--value-file',str(source))
            self.assertEqual(code,1);self.assertEqual(out,'');self.assertEqual(len(self.hits),count)
        source=self.private_file('defaults.json',json.dumps({'deployment_defaults':{SECRET:SECRET}}).encode())
        code,out,err=self.invoke('project-config','apply','notes','--prod','--file',str(source))
        self.assertEqual(code,1);self.assertNotIn(SECRET,out+err)

    def test_apply_matches_api_default_clearing_and_resource_limits(self):
        payload={'deployment_defaults':{'host_port':0,'bind_address':'','memory_limit':'','cpus':0}}
        source=self.private_file('clear-defaults.json',json.dumps(payload).encode())
        self.assert_success('project-config','apply','notes','--prod','--file',str(source))
        self.assertEqual(self.hits[-1][2],{'expected_revision':3,**payload})
        source.write_text(json.dumps({'deployment_defaults':{'cpus':65}}))
        count=len(self.hits)
        code,out,err=self.invoke('project-config','apply','notes','--prod','--file',str(source))
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertEqual(len(self.hits),count)

    def test_unsafe_names_and_invalid_apply_never_write(self):
        for slug in ['../notes','//evil.test','notes?reveal=true']:
            count=len(self.hits)
            self.assertNotEqual(self.invoke('project','show',slug)[0],0)
            self.assertEqual(len(self.hits),count)
        for raw in [b'{invalid',b'{"runtime_env":[],"runtime_env":[]}',
                    json.dumps({'runtime_env':[{'key':'DB_PASSWORD','operation':'set','value':SECRET,'unexpected':SECRET}]}).encode(),
                    json.dumps({'unexpected':SECRET}).encode()]:
            source=self.private_file('bad.json',raw)
            count=len(self.hits)
            code,out,err=self.invoke('project-config','apply','notes','--prod','--file',str(source))
            self.assertNotEqual(code,0);self.assertNotIn(SECRET,out+err)
            self.assertFalse(any(method=='PUT' for method,*_ in self.hits[count:]))

    @unittest.skipIf(os.name=='nt','POSIX private input files')
    def test_value_file_refuses_public_symlink_and_foreign_ownership(self):
        source=self.private_file('value.txt',SECRET.encode());source.chmod(0o644)
        self.assertNotEqual(self.invoke('project-config','set','notes','DB_PASSWORD','--prod','--value-file',str(source))[0],0)
        source.chmod(0o600);link=self.folder/'linked.txt';link.symlink_to(source)
        self.assertNotEqual(self.invoke('project-config','set','notes','DB_PASSWORD','--prod','--value-file',str(link))[0],0)
        if os.geteuid()==0:
            os.chown(source,10001,10001)
            self.assertNotEqual(self.invoke('project-config','set','notes','DB_PASSWORD','--prod','--value-file',str(source))[0],0)
        self.assertFalse(any(method=='PUT' for method,*_ in self.hits))

    def test_subprocess_uses_explicit_private_identity_without_home_mutation(self):
        env={**os.environ,'HOME':str(self.folder/'isolated-home'),'USERPROFILE':str(self.folder/'isolated-home')}
        result=subprocess.run([sys.executable,'-m','deployctl','project-config','set','notes','DB_PASSWORD',SECRET,'--secret','--prod','--client-config',str(self.config)],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(SECRET,result.stdout+result.stderr)
        self.assertNotIn(TOKEN,result.stdout+result.stderr)
        self.assertFalse((self.folder/'isolated-home/.ctl/client.json').exists())


if __name__=='__main__': unittest.main()
