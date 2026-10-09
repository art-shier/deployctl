"""Actual CLI and API permission checks using a disposable PostgreSQL server.

Also exercised by the Docker end-to-end workflow; no production credentials.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials


def verify_management(owner, origin, base):
    """Use independent resources so deployment fixtures keep their revisions."""
    suffix = uuid.uuid4().hex[:8]
    group, project, outside = ('manage-'+suffix, 'managed-'+suffix, 'outside-'+suffix)
    owner_config = base/'management-owner/client.json'
    Credentials.save(owner_config, origin, owner.token)

    def command(config, *args, success=True):
        result = subprocess.run([sys.executable, '-m', 'deployctl', *map(str,args),
                                 '--client-config', str(config)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        if (result.returncode == 0) != success:
            raise AssertionError(f'management command {args[:2]} unexpected exit {result.returncode}; output private')
        return json.loads(result.stdout) if success else result

    command(owner_config, 'group', 'create', group, '--name', 'Management fixture')
    command(owner_config, 'group', 'update', group, '--description', 'Retained metadata')
    external_project = command(owner_config, 'project', 'create', outside, '--group', 'default')
    command(owner_config, 'project', 'create', 'denied-'+suffix, '--group', group)
    pub = owner.json('POST','/api/v1/tokens',{'name':'management fixture','role':'publisher',
                    'groups':[group],'excluded_projects':['denied-'+suffix],'environments':['prod']})
    deploy = owner.json('POST','/api/v1/tokens',{'name':'deployment fixture','role':'deployer',
                       'groups':[group],'environments':['prod']})
    pub_config, deploy_config = base/'management-publisher/client.json', base/'management-deployer/client.json'
    Credentials.save(pub_config, origin, pub['token'])
    Credentials.save(deploy_config, origin, deploy['token'])
    created = command(pub_config, 'project','create',project,'--group',group,'--default-env','prod')
    assert created['group'] == group
    assert [g['slug'] for g in command(pub_config,'group','list')] == [group]
    edited = command(pub_config,'project','update',project,'--name','Updated by publisher')
    assert edited['name'] == 'Updated by publisher' and edited['group'] == group
    for key, value, kind in [('GROUP_SECRET','group-test-secret','runtime'),
                             ('GROUP_ADMIN','group-install-secret','install')]:
        private = base/(key+'.txt'); private.write_text(value); private.chmod(0o600)
        saved = command(owner_config,'group-config','set',group,key,'--prod','--kind',kind,'--secret','--value-file',private)
        assert value not in json.dumps(saved)
    private = base/'management-password.txt'; private.write_text('project-test-secret'); private.chmod(0o600)
    saved = command(pub_config,'project-config','set',project,'DB_PASSWORD','--prod','--secret','--value-file',private)
    assert 'project-test-secret' not in json.dumps(saved)
    masked = command(pub_config,'project-config','get',project)
    assert all(secret not in json.dumps(masked) for secret in ('project-test-secret','group-test-secret','group-install-secret'))
    revealed = command(pub_config,'project-config','get',project,'--prod','--reveal')
    assert revealed['runtime_env'][0]['value'] == 'project-test-secret'
    assert revealed['inherited_runtime_env'][0]['value'] == 'group-test-secret'
    assert revealed['inherited_install_params'][0]['value'] == 'group-install-secret'
    patch = base/'management-patch.json'
    patch.write_text(json.dumps({'expected_revision':saved['revision'],
                     'install_params':[{'key':'ADMIN_EMAIL','operation':'set','value':'fixture@example.test'}],
                     'deployment_defaults':{'host_port':18084},'target_version':'stable'})); patch.chmod(0o600)
    applied = command(pub_config,'project-config','apply',project,'--prod','--file',patch)
    assert applied['revision'] == saved['revision']+1
    assert command(pub_config,'project-config','get',project,'--prod')['deployment_defaults']['host_port'] == 18084
    command(pub_config,'project-config','apply',project,'--prod','--file',patch,success=False)
    command(pub_config,'project-config','unset',project,'ADMIN_EMAIL','--prod','--kind','install')
    assert command(pub_config,'project-config','list',project) == ['prod']
    denied = [
        ('project-config','get',outside,'--prod','--reveal'),
        ('project-config','get',project,'--env','stage','--reveal'),
        ('project-config','set',project,'TEXT','denied','--env','stage'),
        ('group-config','get',group,'--prod','--reveal'),
        ('group','create','forbidden-'+suffix),
        ('project','create','escape-'+suffix,'--group','default'),
        ('project','create','alias-'+suffix,'--group',group,'--image-repository',external_project['image_repository']),
        ('project','update',project,'--image-repository',external_project['image_repository']),
        ('project','create','denied-'+suffix,'--group',group),
        ('project','move',project,'--group','default'),
        ('project','delete',project,'--confirm',project),
        ('group','delete',group,'--confirm',group),
    ]
    for args in denied: command(pub_config,*args,success=False)
    for args in [('project-config','get',project,'--prod'),
                 ('project-config','set',project,'TEXT','denied','--prod'),
                 ('project','update',project,'--name','denied'),
                 ('project','create','deployer-new-'+suffix,'--group',group)]:
        command(deploy_config,*args,success=False)
    direct = owner.json('POST','/api/v1/tokens',{'name':'direct fixture','role':'publisher','projects':[project]})
    direct_config = base/'management-direct/client.json'; Credentials.save(direct_config,origin,direct['token'])
    command(direct_config,'project','create','sibling-'+suffix,'--group',group,success=False)
    command(owner_config,'project','move',project,'--group','default')
    command(pub_config,'project-config','get',project,'--prod','--reveal',success=False)
    assert command(direct_config,'project-config','get',project,'--prod','--reveal')['runtime_env'][0]['value'] == 'project-test-secret'
    command(owner_config,'project','move',project,'--group',group)
    command(owner_config,'group','delete',group,'--confirm',group,success=False)
    command(owner_config,'group','delete','default','--confirm','default',success=False)
    command(owner_config,'project','delete',project,'--confirm',outside,success=False)
    command(owner_config,'project','delete',project,'--confirm',project)
    command(owner_config,'project','show',project,success=False)
    command(direct_config,'project-config','get',project,'--prod','--reveal',success=False)
    command(owner_config,'project','create',project,'--group',group,success=False)
    command(owner_config,'project','delete','denied-'+suffix,'--confirm','denied-'+suffix)
    command(owner_config,'group','delete',group,'--confirm',group)
    command(owner_config,'group','create',group,success=False)
    command(owner_config,'project','delete',outside,'--confirm',outside)
    # Archived scopes remain readable/removable by owners, and credential text is never printed.
    retained = owner.json('GET','/api/v1/tokens')
    assert any(t['id'] == direct['credential']['id'] and project in t.get('projects',[]) for t in retained)
    for entry in (pub,deploy,direct): owner.request('DELETE','/api/v1/tokens/'+entry['credential']['id'])
    print('PASS: actual CLI publisher project/config management, explicit own/inherited secret reveal, scope/environment isolation, unchanged deployer, owner CRUD/archive and reserved slugs')


def main():
    if not os.environ.get('CTL_FIXTURE_DATABASE_URL') or not os.environ.get('CTL_SERVER_BINARY'):
        raise SystemExit('explicit disposable database and server binary required')
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='ctl-management-e2e-') as directory:
        base=Path(directory); keys=base/'keys'; keys.mkdir(mode=0o700)
        binary=os.environ['CTL_SERVER_BINARY']
        subprocess.run([binary,'init','--keys-dir',str(keys)],check=True,capture_output=True)
        env={**os.environ,'CTL_DATABASE_URL':os.environ['CTL_FIXTURE_DATABASE_URL'],
             'CTL_PUBLIC_ORIGIN':origin,'CTL_LISTEN':f'127.0.0.1:{port}','CTL_KEYS_DIR':str(keys),
             'CTL_ARTIFACTS_DIR':str(base/'artifacts'),'CTL_REGISTRY_HOST':f'127.0.0.1:{port}',
             'CTL_REGISTRY_INTERNAL_URL':'http://127.0.0.1:59999'}
        with (base/'server.log').open('w') as log:
            server=subprocess.Popen([binary],env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                owner=PlatformClient(Credentials(origin,(keys/'owner.token').read_text().strip()))
                deadline=time.monotonic()+30
                while True:
                    try:
                        if owner.json('GET','/api/v1/me')['role']=='owner': break
                    except (RuntimeError,OSError): pass
                    if time.monotonic()>deadline: raise RuntimeError('disposable management API readiness timed out')
                    time.sleep(.1)
                verify_management(owner,origin,base)
            finally:
                server.terminate(); server.wait(timeout=15)


if __name__=='__main__': main()
