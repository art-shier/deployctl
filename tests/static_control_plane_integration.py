"""Disposable Linux PostgreSQL/API fixture for the complete static delivery path."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from control_plane_integration import ROOT, run, port, wait
from deployctl.platform_client import PlatformClient,PlatformError
from deployctl.platform_credentials import Credentials
from scripts.package_static import package_static
from scripts.static_delivery import recover_static_release

def main():
    if sys.platform!='linux' or os.environ.get('CI')!='true' or not os.environ.get('CTL_FIXTURE_DATABASE_URL'):
        raise SystemExit('disposable Linux CI database fixture required')
    project='static-fixture-'+uuid.uuid4().hex[:8];origin=f'http://127.0.0.1:{port()}';server=None
    with tempfile.TemporaryDirectory(prefix='ctl-static-e2e-') as temporary:
        base=Path(temporary);base.chmod(0o755);keys=base/'keys'
        try:
            binary=os.environ['CTL_SERVER_BINARY'];run(binary,'init','--keys-dir',keys)
            env={**os.environ,'CTL_DATABASE_URL':os.environ['CTL_FIXTURE_DATABASE_URL'],'CTL_PUBLIC_ORIGIN':origin,
                 'CTL_LISTEN':origin.removeprefix('http://'),'CTL_KEYS_DIR':str(keys),'CTL_ARTIFACTS_DIR':str(base/'artifacts'),
                 'CTL_REGISTRY_INTERNAL_URL':'http://127.0.0.1:1','CTL_REGISTRY_HOST':'127.0.0.1:1'}
            with (base/'api.log').open('w') as log:
                server=subprocess.Popen([binary],env=env,stdout=log,stderr=subprocess.STDOUT)
                owner=PlatformClient(Credentials(origin,(keys/'owner.token').read_text().strip()))
                wait(lambda:owner.json('GET','/api/v1/me').get('role')=='owner')
                owner.json('POST','/api/v1/projects',{'slug':project,'name':'Static fixture','deployment_type':'static'})
                publisher=owner.json('POST','/api/v1/tokens',{'name':'static CI','role':'publisher','groups':['default']})['token']
                deployer=owner.json('POST','/api/v1/tokens',{'name':'static host','role':'deployer','groups':['default'],'environments':['prod']})['token']
                pub_config=base/'publisher/client.json';host_config=base/'host/client.json'
                Credentials.save(pub_config,origin,publisher);Credentials.save(host_config,origin,deployer)
                pub=PlatformClient(Credentials(origin,publisher));commit='a'*40
                assert recover_static_release(pub,project,'v1.0.0',base/'recovery',commit) is None
                cli=lambda *args,**kwargs:run(sys.executable,'-m','deployctl',*args,**kwargs)
                build=base/'build';build.mkdir();(build/'index.html').write_text('version one');(build/'old.txt').write_text('old')
                v1=base/'v1.tar.gz';info=package_static(build,v1)
                cli('publish',project,'--version','v1.0.0','--package',v1,'--commit',commit,'--channel','stable','--client-config',pub_config)
                resumed=recover_static_release(pub,project,'v1.0.0',base/'recovery',commit)
                restored=base/'recovery'/f'{project}-v1.0.0.tar.gz'
                assert resumed['sha256']==info.sha256 and restored.read_bytes()==v1.read_bytes()
                try:recover_static_release(pub,project,'v1.0.0',base/'wrong','b'*40)
                except ValueError:pass
                else:raise AssertionError('different commit must not rebuild/overwrite')
                cli('publish',project,'--version','v1.0.0','--package',restored,'--commit',commit,'--channel','stable','--client-config',pub_config)
                target=base/'www'/project
                owner.json('PUT',f'/api/v1/projects/{project}/environments/prod',{'expected_revision':1,'deployment_defaults':{'target_dir':str(target)}})
                flags=['--root',base/'deployments','--config-root',base/'configs','--client-config',host_config]
                cli('install',project,'--prod',*flags,env={**os.environ,'DOCKER_HOST':'unix:///does-not-exist'})
                assert (target/'index.html').read_text()=='version one'
                assert sorted(p.name for p in target.iterdir())==['index.html','old.txt']
                (build/'index.html').write_text('version two');(build/'old.txt').unlink();(build/'new.txt').write_text('new')
                v2=base/'v2.zip';package_static(build,v2,'zip')
                cli('publish',project,'--version','v2.0.0','--package',v2,'--commit','b'*40,'--channel','stable','--client-config',pub_config)
                cli('upgrade',project,'--prod',*flags)
                assert (target/'index.html').read_text()=='version two' and not (target/'old.txt').exists()
                receipts=owner.json('GET',f'/api/v1/projects/{project}/receipts')
                assert len(receipts)==2 and all(r['success'] for r in receipts)
                for client,path in ((pub,f'/api/v1/projects/{project}/resolve'),(PlatformClient(Credentials(origin,deployer)),f'/api/v1/projects/{project}/environments/prod')):
                    try:client.json('POST' if path.endswith('/resolve') else 'GET',path,{'environment':'prod'} if path.endswith('/resolve') else None)
                    except PlatformError as error:assert error.status==403
                    else:raise AssertionError('typed project must preserve existing permissions')
                server.terminate();server.wait(timeout=15);server=None
                local_flags=['--root',base/'deployments','--config-root',base/'configs']
                cli('rollback',project,'--prod',*local_flags)
                assert (target/'index.html').read_text()=='version one' and (target/'old.txt').exists() and not (target/'new.txt').exists()
                assert 'v1.0.0' in cli('status',project,'--prod',*local_flags).stdout
                assert 'rollback' in cli('logs',project,'--prod',*local_flags).stdout
                print('PASS: real static publish/recovery, source conflict, scoped authorization, tar.gz/ZIP install/upgrade, receipts, offline rollback/status/logs; Registry unreachable')
        finally:
            if server is not None:server.terminate();server.wait(timeout=15)

if __name__=='__main__':main()
