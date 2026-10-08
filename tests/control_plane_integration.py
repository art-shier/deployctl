"""Real API, PostgreSQL, authenticated Registry, CLI and Docker transactions.

Only run in disposable Linux CI. Never uses production configuration.
"""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials
from deployctl.release import build_release
from deployctl.contract import read_yaml
from deployctl.runtime import project_name


def run(*argv,input=None,env=None,check=True):
    result=subprocess.run([str(x) for x in argv],input=input,env=env,capture_output=True,text=True,cwd=ROOT,timeout=600)
    if check and result.returncode: raise RuntimeError(f'fixture command {argv[0]} failed (exit {result.returncode}); output intentionally private')
    return result


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def wait(test,seconds=45):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            if test():return
        except (OSError,RuntimeError):pass
        time.sleep(.2)
    raise RuntimeError('fixture readiness timed out')


def main():
    if sys.platform!='linux' or os.environ.get('CI')!='true' or not os.environ.get('CTL_FIXTURE_DATABASE_URL'):
        raise SystemExit('disposable Linux CI database fixture required')
    api_port=port();app_port=port();origin=f'http://127.0.0.1:{api_port}'
    suffix=uuid.uuid4().hex[:10];registry='ctl-platform-'+suffix
    app='notes-fixture-'+suffix;other='config-fixture-'+suffix;server=None
    with tempfile.TemporaryDirectory(prefix='ctl-platform-e2e-') as tmp:
        base=Path(tmp);keys=base/'keys';keys.mkdir(mode=0o700)
        try:
            binary=os.environ['CTL_SERVER_BINARY']
            run(binary,'init','--keys-dir',keys)
            run('docker','run','-d','--name',registry,'-p','127.0.0.1::5000','-v',f'{keys}/signing.crt:/cert.pem:ro',
                '-e','REGISTRY_AUTH=token','-e','REGISTRY_AUTH_TOKEN_REALM='+origin+'/registry/token',
                '-e','REGISTRY_AUTH_TOKEN_SERVICE=ctl-registry','-e','REGISTRY_AUTH_TOKEN_ISSUER=ctl',
                '-e','REGISTRY_AUTH_TOKEN_ROOTCERTBUNDLE=/cert.pem','registry:2.8.3')
            host=run('docker','port',registry,'5000/tcp').stdout.strip()
            server_env={**os.environ,'CTL_DATABASE_URL':os.environ['CTL_FIXTURE_DATABASE_URL'],'CTL_PUBLIC_ORIGIN':origin,
                        'CTL_LISTEN':f'127.0.0.1:{api_port}','CTL_KEYS_DIR':str(keys),'CTL_ARTIFACTS_DIR':str(base/'artifacts'),
                        'CTL_REGISTRY_HOST':host,'CTL_REGISTRY_INTERNAL_URL':'http://'+host}
            log=(base/'api.log').open('w')
            server=subprocess.Popen([binary],env=server_env,stdout=log,stderr=subprocess.STDOUT)
            owner=PlatformClient(Credentials(origin,(keys/'owner.token').read_text().strip()))
            wait(lambda:owner.json('GET','/api/v1/me').get('role')=='owner')
            def registry_ready():
                try:urlopen('http://'+host+'/v2/',timeout=2)
                except HTTPError as e:return e.code==401
            wait(registry_ready)
            owner.json('POST','/api/v1/groups',{'slug':'fixture-apps','name':'Fixture applications'})
            for project in (app,other):
                owner.json('POST','/api/v1/projects',{'slug':project,'group':'fixture-apps','name':project,'default_environment':'prod','image_repository':host+'/'+project})
            publisher=owner.json('POST','/api/v1/tokens',{'name':'ci','role':'publisher','groups':['fixture-apps']})['token']
            deployer_entry=owner.json('POST','/api/v1/tokens',{'name':'host','role':'deployer','groups':['fixture-apps'],'environments':['prod']})
            deployer=deployer_entry['token']
            publisher_config=base/'publisher/client.json';deployer_config=base/'deployer/client.json'
            Credentials.save(publisher_config,origin,publisher)
            token_file=base/'deployer.token';token_file.write_text(deployer);token_file.chmod(0o600)
            cli=lambda *args,**kw:run(sys.executable,'-m','deployctl',*args,**kw)
            cli('config','set','server',origin,'--client-config',deployer_config)
            cli('login','--token-file',token_file,'--client-config',deployer_config)
            assert json.loads(cli('whoami','--client-config',deployer_config).stdout)['groups']==['fixture-apps']
            assert {p['slug'] for p in json.loads(cli('projects','--client-config',deployer_config).stdout)}=={app,other}
            docker_config=base/'publisher-docker';docker_config.mkdir(mode=0o700)
            env={**os.environ,'DOCKER_CONFIG':str(docker_config)}
            run('docker','login','--username','ctl','--password-stdin',host,input=publisher+'\n',env=env)
            image_tag=host+'/'+app+':fixture'
            run('docker','build','-t',image_tag,ROOT/'examples/project-a',env=env)
            run('docker','push',image_tag,env=env)
            image=run('docker','image','inspect',image_tag,'--format','{{index .RepoDigests 0}}').stdout.strip()
            other_tag=host+'/'+other+':fixture'
            run('docker','tag',image_tag,other_tag)
            run('docker','push',other_tag,env=env)
            other_image=run('docker','image','inspect',other_tag,'--format','{{index .RepoDigests 0}}').stdout.strip()
            run('docker','image','rm',other_tag)
            # Remove local tag/digest before installing; managed CLI must authenticate its pull.
            run('docker','image','rm',image_tag)
            source=base/'source';source.mkdir()
            started=base/'pre-started';resume=base/'pre-resume'
            (source/'pre.sh').write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
[[ -z ${DOCKER_CONFIG:-} && -z ${CTL_OWNER_TOKEN:-} ]]
touch "$DEPLOYCTL_PARAM_PRE_STARTED"
while [[ ! -e $DEPLOYCTL_PARAM_PRE_RESUME ]];do sleep .1;done
printf 'TEXT=pre-generated\n' > "$DEPLOYCTL_CONFIG_DIR/config.env"
''')
            (source/'post.sh').write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
[[ $TEXT == "$DEPLOYCTL_PARAM_EXPECTED_TEXT" ]]
[[ $DEPLOYCTL_PARAM_ADMIN == managed-admin ]]
''')
            cfg=read_yaml(ROOT/'examples/project-a/deploy/deployment.yaml')
            cfg['application']=app;cfg['health']['startup_timeout_seconds']=8
            cfg['hooks']={'pre_install':{'script':'pre.sh','refresh_config':True},'post_install':{'script':'post.sh'}}
            package=build_release(cfg,image,'v1.0.0',base/'packages',project_root=source)
            cli('publish',app,'--version','v1.0.0','--package',package,'--channel','stable','--client-config',publisher_config)
            publisher_client=PlatformClient(Credentials(origin,publisher))
            recovered=publisher_client.recover_release(app,'v1.0.0',base/'delivery-retry','')
            assert recovered['image']==image and (base/'delivery-retry'/package.name).read_bytes()==package.read_bytes()
            # Lost publication response can be retried without rebuilding or reading prod config.
            cli('publish',app,'--version','v1.0.0','--package',base/'delivery-retry'/package.name,'--channel','stable','--client-config',publisher_config)
            owner.json('PUT',f'/api/v1/projects/{app}/environments/prod',{'expected_revision':1,
                'runtime_env':[{'key':'TEXT','operation':'set','value':'pinned-managed'}],
                'install_params':[{'key':k,'operation':'set','value':v} for k,v in {
                    'ADMIN':'managed-admin','EXPECTED_TEXT':'pinned-managed','PRE_STARTED':str(started),'PRE_RESUME':str(resume)}.items()],
                'deployment_defaults':{'host_port':app_port,'memory_limit':'128m','cpus':.5}})
            errors=[]
            def modify_mid_install():
                try:
                    wait(started.exists)
                    owner.json('PUT',f'/api/v1/projects/{app}/environments/prod',{'expected_revision':2,
                        'runtime_env':[{'key':'TEXT','operation':'set','value':'next-managed'}],
                        'install_params':[{'key':'EXPECTED_TEXT','operation':'set','value':'next-managed'}],
                        'deployment_defaults':{'host_port':app_port+1,'memory_limit':'256m','cpus':1}})
                except Exception as e:errors.append(e)
                finally:resume.touch()
            worker=threading.Thread(target=modify_mid_install,daemon=True);worker.start()
            root=base/'apps';config_root=base/'config'
            flags=['--root',root,'--config-root',config_root,'--client-config',deployer_config]
            installation = cli('install',app,'--prod','--env-var','TEXT=cli-local','--set','ADMIN=cli-admin',*flags)
            assert '[working] Downloading and verifying release package' in installation.stderr
            assert '[done] Pulling image' in installation.stderr
            assert 'Image: Pulled' in installation.stderr, 'real authenticated Compose events were not streamed'
            assert '[done] Final readiness check' in installation.stderr
            assert '[done] Saving deployment state' in installation.stderr
            assert publisher not in installation.stderr and deployer not in installation.stderr
            assert 'cli-local' not in installation.stderr and 'cli-admin' not in installation.stderr
            worker.join();assert not errors
            state_path=root/app/'prod/state.json'
            first=json.loads(state_path.read_text());current=first['current']
            snapshot=config_root/app/'prod/runtime'/current['configuration']
            assert json.loads((snapshot/'.env.json').read_text())['TEXT']=='pinned-managed','resolution drifted during pre'
            assert json.loads((snapshot/'overrides.json').read_text())=={'TEXT':'cli-local'},'managed layer leaked into local overrides'
            installed_meta=json.loads((snapshot/'.management.json').read_text())
            assert installed_meta['revision_id']!=owner.json('GET',f'/api/v1/projects/{app}/environments/prod')['id']
            cli('upgrade',app,'--prod',*flags)
            second=json.loads(state_path.read_text())
            assert second['binding']['port']==app_port,'unspecified upgrade must preserve host port'
            snap2=config_root/app/'prod/runtime'/second['current']['configuration']
            assert json.loads((snap2/'.env.json').read_text())['TEXT']=='next-managed'
            assert read_yaml(snap2/'compose.yaml')['services']['app']['mem_limit']=='256m'
            owner.json('PUT',f'/api/v1/projects/{app}/environments/prod',{'expected_revision':3,
                'runtime_env':[{'key':'TEXT','operation':'remove'}],
                'install_params':[{'key':'EXPECTED_TEXT','operation':'set','value':'cli-local'}]})
            cli('upgrade',app,'--prod',*flags)
            third=json.loads(state_path.read_text())
            snap3=config_root/app/'prod/runtime'/third['current']['configuration']
            assert json.loads((snap3/'.env.json').read_text())['TEXT']=='cli-local','deleting managed key did not fall back'
            (source/'fail.sh').write_text('exit 7\n')
            broken=copy.deepcopy(cfg);broken['hooks']['post_install']['script']='fail.sh'
            bad=build_release(broken,image,'v1.1.0',base/'packages',project_root=source)
            cli('publish',app,'--version','v1.1.0','--package',bad,'--channel','stable','--client-config',publisher_config)
            failed=cli('upgrade',app,'--prod',*flags,check=False)
            assert failed.returncode==1,'post failure reported success'
            restored=json.loads(state_path.read_text());assert restored['current']==third['current'] and restored['transaction'] is None
            receipts=owner.json('GET',f'/api/v1/projects/{app}/receipts')
            assert sum(r['success'] for r in receipts)==3 and any(not r['success'] for r in receipts)
            # The same saved login installs an independent project/repository.
            other_cfg=copy.deepcopy(cfg);other_cfg['application']=other;other_cfg.pop('hooks')
            other_package=build_release(other_cfg,other_image,'v1.0.0',base/'packages',project_root=source)
            cli('publish',other,'--version','v1.0.0','--package',other_package,'--channel','stable','--client-config',publisher_config)
            cli('install',other,'--prod','--port',port(),*flags)
            other_state_path=root/other/'prod/state.json';other_state=json.loads(other_state_path.read_text())
            assert other_state['current']['version']=='v1.0.0' and other_state['transaction'] is None
            moved=owner.json('GET',f'/api/v1/projects/{other}')
            moved['group']='default';owner.json('PATCH',f'/api/v1/projects/{other}',moved)
            assert cli('upgrade',other,'--prod',*flags,check=False).returncode==1
            assert json.loads(other_state_path.read_text())==other_state
            credential_id=deployer_entry['credential']['id']
            owner.json('DELETE','/api/v1/tokens/'+credential_id)
            assert cli('upgrade',app,'--prod',*flags,check=False).returncode==1
            assert json.loads(state_path.read_text())['current']==third['current']
            # Keep a short scoped Registry bearer for legacy pulls while API is offline.
            pull_client=PlatformClient(Credentials(origin,publisher))
            with pull_client.registry_config(image) as temporary_config:
                offline_env={**os.environ,'DOCKER_CONFIG':temporary_config}
                server.terminate();server.wait(timeout=15);server=None
                legacy_cfg=copy.deepcopy(cfg);legacy_cfg.pop('hooks')
                legacy=build_release(legacy_cfg,image,'v2.0.0',base/'packages',project_root=source)
                cli('install',app,'--env','legacy','--release',legacy,'--root',root,'--config-root',config_root,env=offline_env)
            run('docker','rm','-f',registry)
            # Both platform and Registry are down; cached immutable image + snapshot suffice.
            cli('rollback',app,'--prod','--root',root,'--config-root',config_root)
            rolled=json.loads(state_path.read_text())
            assert rolled['current']==second['current']
            print('PASS: one group login installs two independent projects, moving a project denies upgrade without altering state; authenticated push/publish, immutable revision, precedence, resources, failed post restoration, receipts, revocation, legacy install and cached offline rollback')
        finally:
            if server:server.terminate();server.wait(timeout=15)
            for application in (app,other):
                for environment in ('prod','legacy'):
                    project=project_name(application,environment)
                    ids=run('docker','ps','-aq','--filter','label=com.docker.compose.project='+project,check=False).stdout.split()
                    if ids:run('docker','rm','-f',*ids,check=False)
                    run('docker','network','rm',project+'_default',check=False)
            run('docker','rm','-f','-v',registry,check=False)

if __name__=='__main__':main()
