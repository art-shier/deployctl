"""Disposable Linux CI: real systemd Agent replacement and verified recovery.

The HTTP control plane returns no deployment tasks. Only release transport is
replaced with locally rebuilt fixture versions; production Host operations and
the bundled Agent process are exercised without changing any existing service.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'skills/team-deploy/assets/deployctl.pyz'
# Use the bundled dependencies with the trusted system Python, including under sudo.
sys.path.insert(0,str(BUNDLE))
from deployctl import bootstrap
from deployctl.node_agent import read_json
from deployctl.updater import Host, Updater, private_directory


class Center(BaseHTTPRequestHandler):
    node_enrollments=0
    updater_enrollments=0
    updater_invitations=0
    node_id=''
    def log_message(self,*args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path=='/api/v1/node/enroll':
            Center.node_enrollments+=1
            body={'node_id':Center.node_id,'token':'ctl_'+'a'*40}
        elif self.path=='/api/v1/node/updater-enrollment':
            Center.updater_invitations+=1
            body={'enrollment_token':'ctl_'+'b'*40}
        elif self.path=='/api/v1/updater/enroll':
            Center.updater_enrollments+=1
            body={'updater_id':'d'*32,'kind':'node','target_id':Center.node_id,'token':'ctl_'+'c'*40}
        elif self.path in ('/api/v1/node/heartbeat','/api/v1/updater/heartbeat'):
            body={'enabled':True,'instances':[]}
        elif self.path in ('/api/v1/node/poll','/api/v1/updater/poll'):
            time.sleep(.2)
            body={'task':None,'job':None}
        else:
            self.send_error(404);return
        raw=json.dumps(body).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60)


def candidate(source,output,version,fail_agent=False):
    data=io.BytesIO()
    data.write(b'#!/usr/bin/env python3\n')
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(data,'w',compression=zipfile.ZIP_DEFLATED) as staged:
        for entry in original.infolist():
            raw=original.read(entry.filename)
            if entry.filename=='deployctl/__init__.py':
                raw=("__version__ = '"+version+"'\n").encode()
            if fail_agent and entry.filename=='__main__.py':
                raw=b"import sys\nif sys.argv[1:3] == ['agent', 'run']: raise SystemExit(42)\n"+raw
            staged.writestr(entry,raw)
    raw=data.getvalue();output.write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest()
    Path(str(output)+'.sha256').write_text(digest+'\n')
    return digest


class LocalReleaseHost(Host):
    def __init__(self,config,artifact):
        super().__init__(config,runner=host_command)
        self.artifact=artifact
    def prepare(self,job,directory):
        if hashlib.sha256(self.artifact.read_bytes()).hexdigest()!=job['cli_sha256']:
            raise RuntimeError('fixture artifact changed')
        shutil.copyfile(self.artifact,directory/'deployctl.pyz')
        shutil.copyfile(Path(str(self.artifact)+'.sha256'),directory/'deployctl.pyz.sha256')


def host_command(argv,**kwargs):
    result=subprocess.run(argv,**kwargs)
    if result.returncode and argv[0]=='systemctl':
        detail=result.stderr.decode(errors='replace') if isinstance(result.stderr,bytes) else result.stderr
        print('Fixture systemctl failure:', ' '.join(argv), (detail or '')[:2048],file=sys.stderr)
    return result


class ControlFixture:
    def __init__(self,job): self.job=job;self.result=None
    def json(self,method,path,payload):
        if path.endswith('/heartbeat'):return {'enabled':True}
        if path.endswith('/poll'):return {'job':dict(self.job,status='leased')}
        if path.endswith('/start'):return dict(self.job,status='running')
        if path.endswith('/report'):
            if payload['status']!='running':self.result=payload
            return dict(self.job,**payload)
        raise AssertionError('unexpected fixture path')


def main():
    if sys.platform!='linux' or os.geteuid()!=0 or os.environ.get('CTL_DISPOSABLE_SYSTEMD_FIXTURE')!='1':
        raise SystemExit('requires explicit disposable Linux root systemd fixture')
    if not Path('/run/systemd/system').is_dir(): raise SystemExit('systemd unavailable')
    node_id=uuid.uuid4().hex
    Center.node_id=node_id
    unit_name='ctl-agent-fixture-'+node_id+'.service'
    updater_name='ctl-updater-fixture-'+node_id+'.service'
    unit=Path('/etc/systemd/system')/unit_name
    updater_unit=Path('/etc/systemd/system')/updater_name
    if unit.exists() or updater_unit.exists(): raise RuntimeError('fixture unit collision')
    center=ThreadingHTTPServer(('127.0.0.1',0),Center)
    thread=threading.Thread(target=center.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='ctl-updater-fixture-',dir='/root') as temp:
            root=Path(temp);root.chmod(0o700)
            agent_home=private_directory(root/'agent')
            origin=f'http://127.0.0.1:{center.server_address[1]}'
            old=root/'old.pyz';candidate(BUNDLE,old,'1.15.0')
            install=private_directory(root/'bin')
            bootstrap.install(artifact=old,install_dir=install)
            executable=install/'ctl'
            token_file=root/'node-enrollment.token';token_file.write_text('ctl_'+'e'*40);token_file.chmod(0o600)
            integrated_home=root/'integrated-updater'
            install_args=[str(executable),'agent','install','--server',origin,'--home',str(agent_home),
                '--root',str(root/'apps'),'--config-root',str(root/'config'),'--executable',str(executable),
                '--service-name',unit_name,'--updater-home',str(integrated_home),'--updater-service',updater_name]
            try:
                command(*install_args,'--token-file',str(token_file))
                identity_before=(agent_home/'node.json').read_bytes()
                updater_before=(integrated_home/'updater.json').read_bytes()
                token_file.unlink()
                config={'kind':'node','target_id':node_id,'server':origin,'cli_path':str(executable),
                        'agent_home':str(agent_home),'agent_service':unit_name}
                host=Host(config,runner=host_command)
                deadline=time.monotonic()+60
                while not host.readiness()[1]:
                    if time.monotonic()>deadline: raise RuntimeError('fixture Agent did not become ready')
                    time.sleep(1)
                original_pid=host.service_info()['MainPID']
                command(*install_args)
                if (agent_home/'node.json').read_bytes()!=identity_before or (integrated_home/'updater.json').read_bytes()!=updater_before:
                    raise RuntimeError('repeat integrated install rotated identity')
                if (Center.node_enrollments,Center.updater_invitations,Center.updater_enrollments)!=(1,1,1):
                    raise RuntimeError('repeat integrated install re-enrolled')
                command('systemctl','is-active','--quiet',updater_name)
                if read_json(integrated_home/'updater.json')['target_id']!=node_id:
                    raise RuntimeError('integrated updater target mismatch')
                if host.service_info()['MainPID']!=original_pid:
                    raise RuntimeError('healthy repeat unnecessarily restarted the Agent')
                migration=root/'migration.pyz';candidate(BUNDLE,migration,'1.16.0')
                bootstrap.install(artifact=migration,install_dir=install)
                if read_json(agent_home/'readiness.json')['version']!='v1.15.0':
                    raise RuntimeError('fixture did not preserve old running Agent after CLI replacement')
                command(*install_args)
                marker=read_json(agent_home/'readiness.json')
                if (not host.readiness()[1] or marker['version']!='v1.16.0'
                        or str(marker['pid'])!=host.service_info()['MainPID'] or str(marker['pid'])==original_pid):
                    raise RuntimeError('existing-node migration did not activate a fresh matching Agent')
                if (agent_home/'node.json').read_bytes()!=identity_before or (integrated_home/'updater.json').read_bytes()!=updater_before:
                    raise RuntimeError('existing-node migration rotated identity')
                if (Center.node_enrollments,Center.updater_invitations,Center.updater_enrollments)!=(1,1,1):
                    raise RuntimeError('existing-node migration re-enrolled')
                jobdir=private_directory(root/'job')
                digest=candidate(BUNDLE,jobdir/'deployctl.pyz','1.17.0')
                job={'kind':'node','version':'v1.17.0','from_version':'v1.16.0','cli_sha256':digest}
                host.backup(job,jobdir)
                host.install(job,jobdir,lambda stage:None)
                if not host.verify('v1.17.0',jobdir): raise RuntimeError('new Agent heartbeat not verified')
                host.restore(job,jobdir,lambda stage:None)
                if not host.verify('v1.16.0',jobdir): raise RuntimeError('restored Agent heartbeat not verified')
                bad=root/'broken.pyz';bad_digest=candidate(BUNDLE,bad,'1.18.0',fail_agent=True)
                identity={'updater_id':uuid.uuid4().hex,'kind':'node','target_id':node_id}
                broken_job={**identity,'id':uuid.uuid4().hex,'version':'v1.18.0','from_version':'v1.16.0',
                            'cli_sha256':bad_digest,'server_sha256':'b'*64}
                control=ControlFixture(broken_job)
                worker=Updater(root/'updater',identity,control,LocalReleaseHost(config,bad))
                worker.run_once()
                if not control.result or control.result['status']!='rolled_back':
                    raise RuntimeError('failed Agent startup did not automatically restore old Agent')
                if not host.readiness()[1]:raise RuntimeError('old Agent not ready after failed candidate')
                print('PASS: integrated node install/repeat and running old-Agent migration, independent active updater, real systemd graceful stop, installer replacement, fresh Agent heartbeat, manual restore and failed-candidate automatic rollback')
            finally:
                subprocess.run(['systemctl','disable','--now',updater_name],capture_output=True,timeout=180)
                subprocess.run(['systemctl','stop',unit_name],capture_output=True,timeout=180)
                unit.unlink(missing_ok=True)
                updater_unit.unlink(missing_ok=True)
                subprocess.run(['systemctl','daemon-reload'],capture_output=True,timeout=30)
                subprocess.run(['systemctl','reset-failed',unit_name],capture_output=True,timeout=30)
    finally:
        center.shutdown();center.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
