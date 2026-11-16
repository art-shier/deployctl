"""Real retained-PostgreSQL lifecycle checks; disposable Linux Docker runner only."""
import copy
import json
import hashlib
import os
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
    projects = [project]
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
                expected_mounts = sorted(pg['Mounts'], key=lambda item: item['Destination'])
                actual_mounts = sorted(actual['Mounts'], key=lambda item: item['Destination'])
                assert actual_mounts == expected_mounts, f'database mounts changed: {expected_mounts!r} -> {actual_mounts!r}'
                assert json.loads(docker('volume','inspect',project+'-data'))[0] == volume, 'volume changed'
                assert docker('exec',pg['Id'],'psql','-U','postgres','-Atc',
                              'SELECT value FROM retained_probe') == 'survives', 'database contents changed'
            verify_restore(config, {'api':api,'postgres':postgres}, values, pg, base, projects)
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
            maintained = copy.deepcopy(config)
            maintained['services']['postgres']['resources'] = {'memory_limit':'512m'}
            maintenance_package = release(maintained, 'v1.0.3')
            dump = docker('exec',pg['Id'],'pg_dump','-U','postgres','--no-owner','postgres')
            request = {'services':['postgres'], 'backup_sha256':hashlib.sha256(dump.encode()).hexdigest()}
            preview = manager.plan(app,'test',maintenance_package,upgrade=True,managed_configuration=values,maintenance=request)
            request['plan_id'] = preview['maintenance']['plan_id']
            manager.deploy(app,'test',maintenance_package,upgrade=True,managed_configuration=values,maintenance=request)
            maintained_pg = container(project,'postgres')
            assert maintained_pg['Id'] != pg['Id']
            assert json.loads(docker('volume','inspect',project+'-data'))[0] == volume
            assert docker('exec',maintained_pg['Id'],'psql','-U','postgres','-Atc','SELECT value FROM retained_probe') == 'survives'
            assert container(project,'api')['State']['Running']
            print('PASS: reviewed persistent maintenance preserves database data and resumes applications')

    finally:
        # This script owns only this randomly named fixture project. Production
        # runtime never performs volume cleanup; disposable CI cleanup does.
        for project in projects:
            cleanup(project)


def cleanup(project):
    ids = docker('ps','-aq','--filter','label=com.docker.compose.project='+project).split()
    if ids: docker('rm','-f',*ids)
    for kind in ('network','volume'):
        names = docker(kind,'ls','--filter','label=io.team-deploy.project='+project,'--format','{{.Name}}').split()
        for name in names: docker(kind,'rm',name)


def verify_restore(source, images, values, source_pg, base, projects):
    from deployctl.service_jobs import JobRunner
    dump = docker('exec',source_pg['Id'],'pg_dump','-U','postgres','--no-owner','--no-acl','postgres') + '\n'
    digest = hashlib.sha256(dump.encode()).hexdigest()
    config = copy.deepcopy(source); config['application'] += '-restore'
    app = config['application']; project = project_name(app,'test'); projects.append(project)
    config['volumes']['restore-scratch'] = {'retain':True}
    config['jobs'] = {'restore':{'image':images['postgres'],'trigger':'bootstrap',
        'permission_profile':'database-init',
        'depends_on':{'postgres':'healthy'}, 'timeout_seconds':60,
        'volumes':{'restore-scratch':'/var/lib/postgresql'},
        'environment':{'PGHOST':{'literal':'postgres'},'PGUSER':{'literal':'postgres'},
                       'PGPASSWORD':{'secret':'password'},'BACKUP_SHA256':{'literal':digest}},
        'secret_files':{'dump':'/run/secrets/backup.sql'},
        'command':['sh','-ec',
            'test "$(sha256sum /run/secrets/backup.sql | cut -d " " -f1)" = "$BACKUP_SHA256"; echo backup-sha-verified; '
            'test "$(psql -Atc "SELECT count(*) FROM pg_tables WHERE schemaname=\'public\'")" = 0; echo empty-target-verified; '
            'psql -v ON_ERROR_STOP=1 -f /run/secrets/backup.sql; '
            'test "$(psql -Atc "SELECT value FROM retained_probe")" = survives']}}
    config['services']['api']['depends_on'] = {'restore':'succeeded'}
    images = dict(images,restore=images['postgres'])
    package = build_release(config,images,'v1.0.0',base/'restore-packages')
    restore_values = copy.deepcopy(values); restore_values['secrets']['dump'] = dump
    manager = Manager(base/'restore-runtime',base/'restore-config')
    pid = os.fork()
    if pid == 0:
        # Simulate SIGKILL between Docker exit and durable success acknowledgement.
        original = JobRunner._record
        def crash_before_ack(runner, name, execution, status):
            if status == 'succeeded': os._exit(79)
            return original(runner,name,execution,status)
        JobRunner._record = crash_before_ack
        try:
            manager.deploy(app,'test',package,managed_configuration=restore_values,job_sources={'restore':digest})
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(1)
        os._exit(2)
    _, status = os.waitpid(pid,0)
    if os.waitstatus_to_exitcode(status) != 79:
        identity = container(project,'restore')['Id']
        logs = subprocess.run(['docker','logs',identity],capture_output=True,text=True)
        output = (logs.stdout+logs.stderr).replace(values['secrets']['password'],'[REDACTED]')
        print(output,flush=True)
        raise AssertionError('fixture did not reach post-restore crash point')
    home, _ = manager.paths(app,'test'); state = manager.state(home,app,'test')
    assert state['jobs']['restore']['status'] == 'running'
    before = container(project,'restore')['Id']; postgres = container(project,'postgres')['Id']
    result = manager.recover(app,'test',state['transaction']['journal']['id'])
    assert result['transaction'] is None and result['jobs']['restore']['status'] == 'succeeded'
    assert container(project,'restore')['Id'] == before
    assert container(project,'postgres')['Id'] == postgres
    assert docker('exec',postgres,'psql','-U','postgres','-Atc','SELECT value FROM retained_probe') == 'survives'
    updated = build_release(config,images,'v1.0.1',base/'restore-upgrade')
    manager.deploy(app,'test',updated,upgrade=True,managed_configuration=values)
    assert container(project,'restore')['Id'] == before, 'completed restore repeated on upgrade'
    assert container(project,'postgres')['Id'] == postgres
    print('PASS: verified pg_dump restore, process loss before acknowledgement, recovery without repeat, upgrade without old backup credentials')
    # Simulate loss only in this disposable fixture; retain the scratch volume
    # and source containers to verify the recovery operation never cleans them.
    scratch = json.loads(docker('volume','inspect',project+'-restore-scratch'))[0]
    docker('rm','-f',postgres)
    docker('volume','rm',project+'-data')
    request = {'plan_id':None}
    preview = manager.plan(app,'test',updated,upgrade=True,managed_configuration=restore_values,
                           job_sources={'restore':digest},restoration=request)
    generation_project = project[:50]+'-g'+preview['generation']
    projects.append(generation_project)
    request['plan_id'] = preview['plan_id']
    restored = manager.deploy(app,'test',updated,upgrade=True,managed_configuration=restore_values,
                               job_sources={'restore':digest},restoration=request)
    assert restored['generation'] == preview['generation'] and restored['transaction'] is None
    new_pg = container(generation_project,'postgres')['Id']
    assert docker('exec',new_pg,'psql','-U','postgres','-Atc','SELECT value FROM retained_probe') == 'survives'
    assert not container(project,'api')['State']['Running']
    assert json.loads(docker('volume','inspect',project+'-restore-scratch'))[0] == scratch
    try: manager.rollback(app,'test')
    except ValueError as exc: assert 'cross-generation' in str(exc)
    else: raise AssertionError('unsafe cross-generation rollback accepted')
    next_package = build_release(config,images,'v1.0.2',base/'restored-upgrade')
    manager.deploy(app,'test',next_package,upgrade=True,managed_configuration=values)
    assert container(generation_project,'postgres')['Id'] == new_pg
    print('PASS: lost-volume isolated restoration, retained old resources, no stale rollback, stable database through next upgrade')



if __name__ == '__main__':
    main()
