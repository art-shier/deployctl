"""Real retained-PostgreSQL lifecycle checks; disposable Linux Docker runner only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployctl.release import build_release
from deployctl.runtime import Manager, project_name


def docker(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout.strip()


def pinned(tag):
    docker('pull', '--platform', 'linux/amd64', tag)
    return json.loads(docker('image', 'inspect', tag))[0]['RepoDigests'][0]


def container(project, service):
    identity = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project='+project,
                      '--filter', 'label=com.docker.compose.service='+service)
    assert identity and '\n' not in identity, 'missing or ambiguous fixture container'
    return json.loads(docker('inspect', identity))[0]


def main():
    if sys.platform != 'linux':
        raise SystemExit('Requires real Linux Docker; skipped execution is not a pass')
    postgres, api = pinned('postgres:18-alpine'), pinned('python:3.12-alpine')
    app = 'stack-ci-'+uuid.uuid4().hex[:12]
    project = project_name(app, 'test')
    config = {'schema_version':2, 'application':app, 'platform':'linux/amd64',
        'volumes':{'data':{'retain':True}}, 'services':{
            'postgres':{'lifecycle':'persistent', 'image':postgres, 'mirror':'project',
                'permission_profile':'database-init', 'volumes':{'data':'/var/lib/postgresql'},
                'environment':{'POSTGRES_PASSWORD_FILE':{'literal':'/run/secrets/password'}},
                'secret_files':{'password':'/run/secrets/password'},
                'health':{'exec':['pg_isready','-U','postgres']}},
            'api':{'lifecycle':'application', 'image':api, 'mirror':'project',
                'command':['python','-c','import time; time.sleep(3600)'],
                'depends_on':{'postgres':'healthy'},
                'environment':{'REVISION':{'literal':'one'}},
                'health':{'exec':['python','-c','pass']}}}}
    values = {'config':{}, 'secrets':{'password':uuid.uuid4().hex}}
    try:
        with tempfile.TemporaryDirectory(prefix='ctl-stack-ci-') as folder:
            base = Path(folder); manager = Manager(base/'runtime', base/'config')
            def release(source, version):
                return build_release(source, {'api':api,'postgres':postgres}, version, base/version)
            first = release(config, 'v1.0.0')
            manager.deploy(app,'test',first,managed_configuration=values)
            pg = container(project,'postgres'); api_before = container(project,'api')['Id']
            volume = json.loads(docker('volume','inspect',project+'-data'))[0]
            docker('exec', pg['Id'], 'psql','-U','postgres','-v','ON_ERROR_STOP=1','-c',
                   "CREATE TABLE retained_probe (value text); INSERT INTO retained_probe VALUES ('survives');")
            def unchanged_database():
                actual = container(project,'postgres')
                assert actual['Id'] == pg['Id'], 'database container recreated'
                assert actual['Mounts'] == pg['Mounts'], 'database mounts changed'
                assert json.loads(docker('volume','inspect',project+'-data'))[0] == volume, 'volume changed'
                assert docker('exec',pg['Id'],'psql','-U','postgres','-Atc',
                              'SELECT value FROM retained_probe') == 'survives', 'database contents changed'
            changed = copy.deepcopy(config)
            changed['services']['api']['environment']['REVISION']['literal'] = 'two'
            second = release(changed, 'v1.0.1')
            plan = manager.plan(app,'test',second,upgrade=True,managed_configuration=values)
            assert plan['services'] == {'api':'update','postgres':'unchanged'}
            manager.deploy(app,'test',second,upgrade=True,managed_configuration=values)
            assert container(project,'api')['Id'] != api_before
            unchanged_database()
            manager.rollback(app,'test'); unchanged_database()
            assert 'REVISION=one' in container(project,'api')['Config']['Env']
            bad = copy.deepcopy(changed)
            bad['services']['api']['command'] = ['python','-c','raise SystemExit(1)']
            bad['services']['api']['health']['startup_timeout_seconds'] = 2
            try:
                manager.deploy(app,'test',release(bad,'v1.0.2'),upgrade=True,managed_configuration=values)
            except RuntimeError:
                pass
            else:
                raise AssertionError('unhealthy application deployment unexpectedly succeeded')
            unchanged_database()
            assert 'REVISION=one' in container(project,'api')['Config']['Env']
            manager.operate(app,'test','stop'); unchanged_database()
            manager.operate(app,'test','restart'); unchanged_database()
            home, _ = manager.paths(app,'test')
            assert manager.state(home,app,'test')['transaction'] is None
            print('PASS: real app upgrade, rollback, failed upgrade and stop/restart preserve PostgreSQL container, mounts, volume and SQL data')
    finally:
        # This script owns only this randomly named fixture project. Production
        # runtime never performs volume cleanup; disposable CI cleanup does.
        ids = docker('ps','-aq','--filter','label=com.docker.compose.project='+project).split()
        if ids: docker('rm','-f',*ids)
        for kind in ('network','volume'):
            names = docker(kind,'ls','--filter','label=io.team-deploy.project='+project,'--format','{{.Name}}').split()
            for name in names: docker(kind,'rm',name)


if __name__ == '__main__':
    main()
