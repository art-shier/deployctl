import copy
from pathlib import Path
import pytest
from deployctl import updater

ID = 'a' * 32
JOB = dict(id='b'*32, updater_id=ID, kind='node', target_id='c'*32,
           version='v1.16.0', from_version='v1.15.0', cli_sha256='d'*64,
           server_sha256='e'*64, status='leased')
IDENTITY = dict(updater_id=ID, kind='node', target_id='c'*32)

class Client:
    def __init__(self, job=None):
        self.job = copy.deepcopy(job or JOB)
        self.calls = []
        self.fail_start = False
        self.fail_report = False
    def json(self, method, path, value):
        self.calls.append((path, value))
        if path.endswith('/heartbeat'): return {'enabled': True}
        if path.endswith('/poll'): return {'job': self.job}
        if path.endswith('/start'):
            if self.fail_start: raise RuntimeError('connection lost')
            return dict(self.job, status='running')
        if self.fail_report: raise RuntimeError('connection lost')
        return dict(self.job, **value)

class Host:
    def __init__(self):
        self.events = []
        self.version = 'v1.15.0'
        self.fail = None
    def readiness(self): return self.version, True, ''
    def step(self, name):
        self.events.append(name)
        if self.fail == name: raise RuntimeError('secret must not escape')
    def prepare(self, job, directory): self.step('download')
    def backup(self, job, directory): self.step('backup')
    def install(self, job, directory, stage):
        stage('stopping'); self.step('stop')
        stage('installing'); self.step('install'); self.version = job['version']
        stage('restarting'); self.step('restart')
    def verify(self, version, directory):
        self.step('verify'); return self.version == version
    def restore(self, job, directory, stage):
        self.step('restore'); self.version = job['from_version']
    def reconcile(self, job, directory): return False

def worker(tmp_path, host=None, client=None, kind='node'):
    identity = dict(IDENTITY, kind=kind)
    job = dict(JOB, kind=kind)
    return updater.Updater(tmp_path, identity, client or Client(job), host or Host())

def test_success_journals_before_install_and_reports(tmp_path):
    w = worker(tmp_path); w.run_once()
    assert w.host.events == ['download','backup','stop','install','restart','verify']
    record = updater.read_json(tmp_path/'jobs'/JOB['id']/'journal.json')
    assert record['report']['status'] == 'succeeded' and record['acknowledged']
    assert w.client.calls[-1][1]['current_version'] == 'v1.16.0'

def test_start_connection_ambiguity_never_executes_or_replays(tmp_path):
    client = Client(); client.fail_start = True
    w = worker(tmp_path, client=client); w.run_once()
    assert w.host.events == []
    client.fail_start = False
    w.run_once()
    assert w.host.events == []
    assert w.client.calls[-1][1]['status'] == 'needs_attention'

def test_health_failure_restores_and_verifies(tmp_path):
    host = Host()
    def verify(version, directory):
        host.events.append('verify'); return version == 'v1.15.0'
    host.verify = verify
    w = worker(tmp_path, host); w.run_once()
    assert host.events[-3:] == ['verify','restore','verify']
    assert w.client.calls[-1][1]['status'] == 'rolled_back'

def test_failed_recovery_requires_attention(tmp_path):
    host = Host(); host.fail = 'restore'; host.verify = lambda *args: False
    w = worker(tmp_path, host); w.run_once()
    assert w.client.calls[-1][1]['status'] == 'needs_attention'

def test_backup_failure_never_stops_agent(tmp_path):
    host = Host(); host.fail = 'backup'
    w = worker(tmp_path, host); w.run_once()
    assert host.events == ['download','backup']
    assert w.client.calls[-1][1]['result_code'] == 'backup_failed'

def test_terminal_report_network_loss_retries_without_install(tmp_path):
    client = Client(); client.fail_report = True
    w = worker(tmp_path, client=client); w.run_once()
    events = w.host.events.copy(); client.fail_report = False; w.run_once()
    assert w.host.events == events
    assert w.client.calls[-1][1]['status'] == 'succeeded'

def test_restart_never_replays_destructive_stage(tmp_path):
    w = worker(tmp_path)
    directory = tmp_path/'jobs'/JOB['id']; directory.mkdir(parents=True,mode=0o700)
    updater.write_json(directory/'journal.json', dict(job=JOB, stage='installing', acknowledged=False))
    w.run_once()
    assert w.host.events == []
    assert w.client.calls[-1][1]['status'] == 'needs_attention'

def test_center_reconciles_install_after_outage_without_replay(tmp_path):
    w = worker(tmp_path, kind='center'); w.host.version='v1.16.0'; w.host.reconcile=lambda *args: True
    directory=tmp_path/'jobs'/JOB['id']; directory.mkdir(parents=True,mode=0o700)
    updater.write_json(directory/'journal.json', dict(job=dict(JOB,kind='center'), stage='installing', acknowledged=False))
    w.run_once()
    assert w.host.events == [] and w.client.calls[-1][1]['status'] == 'succeeded'

@pytest.mark.parametrize('value', ['latest','v1.14.9','v2.0.0','v1.16.0-rc1','v01.16.0','https://evil'])
def test_reject_unpinned_or_incompatible_versions(value):
    with pytest.raises(ValueError): updater.release_version(value)

@pytest.mark.parametrize('field,value', [('kind','center'),('target_id','f'*32),('updater_id','f'*32),('version','v1.15.0'),('cli_sha256','evil')])
def test_task_validation_pins_identity_and_strict_upgrade(field,value):
    with pytest.raises(ValueError): updater.validate_job(dict(JOB, **{field:value}), IDENTITY)

def test_cli_hidden_enrollment_no_token_argument():
    from deployctl.cli import parser
    args=parser().parse_args(['updater','enroll','--server','https://example.com','--kind','node','--target-id','c'*32])
    assert args.agent_service == 'ctl-agent.service'
    assert args.server_home == '/opt/ctl-platform'
    assert not hasattr(args,'token')

def test_agent_readiness_only_written_after_valid_heartbeat(tmp_path):
    import os
    from deployctl.node_agent import Agent
    from deployctl import __version__
    class HeartbeatClient:
        fail = True
        def json(self,*args):
            if self.fail: raise RuntimeError('network unavailable')
            return {'enabled':True,'instances':[]}
    client=HeartbeatClient(); agent=Agent(tmp_path,IDENTITY['target_id'],client,None)
    with pytest.raises(RuntimeError): agent.heartbeat(sample=False)
    assert not (tmp_path/'readiness.json').exists()
    client.fail=False; agent.heartbeat(sample=False)
    marker=updater.read_json(tmp_path/'readiness.json')
    assert marker['pid']==os.getpid() and marker['version']=='v'+__version__
    assert marker['node_id']==IDENTITY['target_id'] and marker['heartbeat_ns']>0


def managed_host(tmp_path,monkeypatch):
    import hashlib,json
    # Portable fake host: temp ancestry is allowed, while leaf safety is retained.
    def test_trusted(path,private=False,owners=(0,)):
        path=Path(path).absolute(); updater.reject_links(path)
        if path.exists() and __import__('os').name!='nt':
            if path.stat().st_mode & (0o077 if private else 0o022): raise ValueError('unsafe fixture permissions')
        return path
    monkeypatch.setattr(updater,'trusted',test_trusted)
    bindir=tmp_path/'bin'; bindir.mkdir(mode=0o700)
    content=b'old managed executable'; digest=hashlib.sha256(content).hexdigest()
    for name in ('ctl','deployctl'):
        (bindir/name).write_bytes(content); (bindir/name).chmod(0o755)
    metadata=dict(schema_version=1,repository='art-shier/deployctl',version='v1.15.0',
        files={'ctl':digest,'deployctl':digest},directory=str(bindir),primary_name='deployctl',commands=['deployctl','ctl'])
    updater.write_json(bindir/'.deployctl-install.json',metadata)
    agent_home=tmp_path/'agent'; agent_home.mkdir(mode=0o700)
    updater.write_json(agent_home/'node.json',dict(node_id=IDENTITY['target_id'],server='https://example.com'))
    unit=tmp_path/'ctl-agent.service'; unit.write_text('trusted fixture')
    config=dict(IDENTITY,cli_path=str(bindir/'ctl'),agent_home=str(agent_home),server='https://example.com')
    calls=[]; state={'active':'active','pid':'42'}
    def runner(argv,**kwargs):
        from types import SimpleNamespace
        calls.append(argv)
        if argv[1]=='stop': state.update(active='inactive',pid='0')
        if argv[1]=='start': state.update(active='active',pid='43')
        output=''
        if argv[1]=='show':
            output=f"FragmentPath={unit}\nUser=root\nExecStart={{ path={bindir/'ctl'} ; argv[]={bindir/'ctl'} agent run --home {agent_home} ; }}\nActiveState={state['active']}\nMainPID={state['pid']}\nKillSignal=15\nKillMode=mixed\n"
        return SimpleNamespace(returncode=0,stdout=output.encode())
    # Windows shlex treats drive backslashes differently; paths used by systemd are POSIX.
    monkeypatch.setattr(updater.Host,'service_info',lambda self:dict(ActiveState=state['active'],MainPID=state['pid']))
    monkeypatch.setattr(updater.bootstrap,'artifact_version',lambda path:'1.15.0')
    host=updater.Host(config,runner)
    return host,metadata,calls,state


def test_node_not_ready_without_matching_fresh_heartbeat(tmp_path,monkeypatch):
    import time
    host,_,_,_=managed_host(tmp_path,monkeypatch)
    assert host.readiness()[1] is False
    updater.write_json(host.agent_home/'readiness.json',dict(version='v1.15.0',node_id=IDENTITY['target_id'],pid=41,enabled=True,heartbeat_ns=time.time_ns()))
    assert host.readiness()[1] is False
    updater.write_json(host.agent_home/'readiness.json',dict(version='v1.15.0',node_id=IDENTITY['target_id'],pid=42,enabled=True,heartbeat_ns=time.time_ns()))
    assert host.readiness()[1] is True


def test_exact_private_backup_restores_both_commands_and_metadata(tmp_path,monkeypatch):
    import os
    host,metadata,calls,state=managed_host(tmp_path,monkeypatch)
    directory=tmp_path/'job'; directory.mkdir(mode=0o700)
    before={name:(host.executable.parent/name).read_bytes() for name in (*metadata['commands'],'.deployctl-install.json')}
    host.backup(JOB,directory)
    for name in metadata['commands']: (host.executable.parent/name).write_bytes(b'changed')
    host.restore(JOB,directory,lambda stage:None)
    assert {name:(host.executable.parent/name).read_bytes() for name in before}==before
    assert [c[1] for c in calls]==['stop','start']
    assert updater.read_json(directory/'restart.json')['after_ns']>0


def test_tampered_managed_alias_rejected_before_backup(tmp_path,monkeypatch):
    host,_,calls,_=managed_host(tmp_path,monkeypatch)
    (host.executable.parent/'deployctl').write_bytes(b'modified')
    with pytest.raises(ValueError): host.backup(JOB,tmp_path)
    assert calls==[]


def test_corrupt_backup_prevents_partial_restore(tmp_path,monkeypatch):
    host,_,calls,_=managed_host(tmp_path,monkeypatch)
    directory=tmp_path/'job'; directory.mkdir(mode=0o700); host.backup(JOB,directory)
    (directory/'backup'/'ctl').write_bytes(b'corrupt')
    before=host.executable.read_bytes()
    with pytest.raises(ValueError): host.restore(JOB,directory,lambda stage:None)
    assert host.executable.read_bytes()==before and calls[-1][1]=='stop'


def test_fresh_marker_must_be_after_restart_and_match_process(tmp_path,monkeypatch):
    host,_,_,_=managed_host(tmp_path,monkeypatch)
    directory=tmp_path/'job'; directory.mkdir(mode=0o700); updater.write_json(directory/'restart.json',{'after_ns':100})
    monkeypatch.setattr(updater.time,'monotonic',iter([0,121,0,121,0]).__next__)
    for marker in [dict(heartbeat_ns=99,pid=42),dict(heartbeat_ns=101,pid=99)]:
        updater.write_json(host.agent_home/'readiness.json',dict(marker,version='v1.15.0',node_id=IDENTITY['target_id'],enabled=True))
        assert not host.verify('v1.15.0',directory)
    updater.write_json(host.agent_home/'readiness.json',dict(heartbeat_ns=101,pid=42,version='v1.15.0',node_id=IDENTITY['target_id'],enabled=True))
    assert host.verify('v1.15.0',directory)


def test_recovery_acknowledged_attention_still_heartbeats(tmp_path):
    w=worker(tmp_path); directory=tmp_path/'jobs'/JOB['id']; directory.mkdir(parents=True,mode=0o700)
    updater.write_json(directory/'journal.json',dict(job=JOB,stage='installing',acknowledged=True,
        report=dict(status='needs_attention',stage='interrupted',result_code='interrupted',current_version='v1.15.0')))
    w.client.job=None; w.run_once()
    assert any(path.endswith('/heartbeat') for path,_ in w.client.calls)

def test_center_backup_failure_prevents_installer(tmp_path):
    host=Host(); host.fail='backup'
    w=worker(tmp_path,host,kind='center'); w.run_once()
    assert host.events==['download','backup']
    assert w.client.calls[-1][1]['status']=='failed'


def test_center_installer_failure_never_rolls_back_database(tmp_path):
    host=Host(); host.fail='install'
    w=worker(tmp_path,host,kind='center'); w.run_once()
    assert 'restore' not in host.events
    assert w.client.calls[-1][1]['status']=='needs_attention'


def test_center_pending_reconciliation_does_not_reinstall(tmp_path):
    w=worker(tmp_path,kind='center')
    directory=tmp_path/'jobs'/JOB['id']; directory.mkdir(parents=True,mode=0o700)
    updater.write_json(directory/'journal.json',dict(job=dict(JOB,kind='center'),stage='installing',acknowledged=False))
    w.run_once()
    assert w.host.events==[] and w.client.calls[-1][1]['status']=='needs_attention'


def test_download_rejects_prerelease_before_artifact_fetch(tmp_path,monkeypatch):
    host=updater.Host(dict(IDENTITY,cli_path=str(tmp_path/'ctl')))
    monkeypatch.setattr(updater.bootstrap,'fetch',lambda *args:pytest.fail('prerelease must be rejected before network access'))
    with pytest.raises(ValueError): host.prepare(dict(JOB,version='v1.16.0-rc1'),tmp_path)


def test_download_uses_only_fixed_official_url_and_pinned_digest(tmp_path,monkeypatch):
    import json
    host=updater.Host(dict(IDENTITY,cli_path=str(tmp_path/'ctl')))
    calls=[]
    def fetch(request,limit):
        calls.append(request.full_url)
        return b'tampered artifact'
    monkeypatch.setattr(updater.bootstrap,'fetch',fetch)
    with pytest.raises(ValueError): host.prepare(dict(JOB,url='https://evil',command='rm -rf /'),tmp_path)
    assert calls==['https://github.com/art-shier/deployctl/releases/download/v1.16.0/deployctl.pyz']
    assert not (tmp_path/'deployctl.pyz').exists()


def test_stop_must_complete_before_installer_runs(tmp_path,monkeypatch):
    host,_,calls,state=managed_host(tmp_path,monkeypatch)
    # A systemctl stop success alone cannot prove the old process is gone.
    host.command=lambda *args,**kwargs: ''
    monkeypatch.setattr(updater.bootstrap,'install',lambda **kwargs:pytest.fail('must not replace while running'))
    with pytest.raises(RuntimeError): host.install(JOB,tmp_path,lambda stage:None)


def test_root_only_setup_even_when_windows_has_admin(monkeypatch):
    monkeypatch.setattr(updater.sys,'platform','win32')
    with pytest.raises(ValueError,match='Linux root'): updater.require_root()


def test_recovery_from_running_server_without_local_journal_never_executes(tmp_path):
    client=Client(dict(JOB,status='running')); w=worker(tmp_path,client=client)
    w.run_once()
    assert w.host.events==[]
    assert w.client.calls[-1][1]['status']=='needs_attention'

@pytest.mark.parametrize('name',['other.service','ctl-updater;reboot.service','ctl-updater/evil.service'])
def test_updater_service_name_allowlist(name):
    with pytest.raises(ValueError): updater.validate_service_name(name)


def test_install_refuses_unrelated_existing_service(tmp_path,monkeypatch):
    unit=tmp_path/'ctl-updater.service'; unit.write_text('unrelated administrator service')
    monkeypatch.setattr(updater,'service_unit',lambda home:'expected updater unit')
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:unit)
    with pytest.raises(ValueError,match='existing'): updater.install_service(tmp_path,{},'ctl-updater.service')
    assert unit.read_text()=='unrelated administrator service'


def test_separate_updater_service_names_and_idempotent_install(tmp_path,monkeypatch):
    from types import SimpleNamespace
    unit=tmp_path/'ctl-updater-center.service'; unit.write_text('expected updater unit')
    monkeypatch.setattr(updater,'service_unit',lambda home:'expected updater unit')
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:unit)
    calls=[]
    monkeypatch.setattr(updater,'Host',lambda config:SimpleNamespace(command=lambda argv,**kwargs:calls.append(argv)))
    updater.install_service(tmp_path,{},'ctl-updater-center.service')
    assert calls[-2]==['systemctl','enable','--now','ctl-updater-center.service']
    assert unit.read_text()=='expected updater unit'


@pytest.mark.skipif(__import__('os').name=='nt', reason='installer enforces Linux absolute paths')
def test_center_installer_refuses_changed_state_inside_lock(tmp_path,monkeypatch):
    import contextlib
    sb=updater.server_bundle
    image='ghcr.io/art-shier/ctl-server@sha256:'+'a'*64
    package=sb.build_bundle(Path(__file__).resolve().parents[1],tmp_path/'bundle',image,'v1.16.0')
    home=tmp_path/'center'; home.mkdir(mode=0o700); (home/'server-state.json').write_text('{}')
    original=dict(schema_version=1,current=dict(version='v1.15.0',sha256='1'*64,image=image),pending=None)
    changed=dict(original,current=dict(original['current'],version='v1.15.1'))
    monkeypatch.setattr(sb,'require_runtime',lambda:None)
    monkeypatch.setattr(sb,'trusted_parents',lambda path:None)
    monkeypatch.setattr(sb,'private',lambda path,*args:path)
    monkeypatch.setattr(sb,'locked',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(sb,'acquire_release',lambda *args,**kwargs:package)
    monkeypatch.setattr(sb,'read_state',lambda path:changed)
    monkeypatch.setattr(sb,'run_bootstrap',lambda *args:pytest.fail('changed center must not deploy'))
    with pytest.raises(ValueError,match='changed'):
        sb.deploy_server(source=package,home=home,upgrade=True,expected_previous=original)


def test_updater_install_parser_accepts_separate_service_name():
    from deployctl.cli import parser
    args=parser().parse_args(['updater','install','--service-name','ctl-updater-center.service'])
    assert args.service_name=='ctl-updater-center.service'


def test_platform_keys_are_bounded_regular_files(tmp_path):
    path=tmp_path/'database.url'; path.write_bytes(b'postgresql://private')
    path.chmod(0o600)
    if __import__('os').name!='nt' and __import__('os').getuid()!=0:
        with pytest.raises(ValueError): updater.read_platform_key(path)
        return
    assert updater.read_platform_key(path)==b'postgresql://private'
    path.write_bytes(b'x'*(1024*1024+1))
    with pytest.raises(ValueError): updater.read_platform_key(path)


def test_center_snapshot_detects_changed_configuration_before_deploy(tmp_path,monkeypatch):
    home=tmp_path/'center'; home.mkdir(mode=0o700); (home/'keys').mkdir(mode=0o700)
    directory=tmp_path/'job'; backup=directory/'backup'; backup.mkdir(parents=True,mode=0o700); (backup/'keys').mkdir(mode=0o700)
    for name in ('instance.json','compose.env','server-state.json'):
        (home/name).write_bytes(b'original'); (home/name).chmod(0o600)
        (backup/name).write_bytes(b'original'); (backup/name).chmod(0o600)
    updater.write_json(backup/'complete.json',{'version':'v1.15.0'})
    host=updater.Host(dict(IDENTITY,kind='center',cli_path=str(tmp_path/'ctl'),server_home=str(home)))
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:Path(path))
    monkeypatch.setattr(host,'center_identity',lambda:None)
    host.check_center_snapshot(directory)
    (home/'compose.env').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'): host.check_center_snapshot(directory)


def test_service_identity_refuses_wrong_executable(tmp_path,monkeypatch):
    from types import SimpleNamespace
    unit=tmp_path/'agent.service'; unit.write_text('fixture')
    host=updater.Host(dict(IDENTITY,cli_path='/usr/local/bin/ctl'))
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:unit)
    host.runner=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=(f'FragmentPath={unit}\nUser=root\nExecStart={{ path=/bin/evil ; argv[]=/bin/evil agent run ; }}\nKillSignal=15\nKillMode=mixed\n').encode())
    with pytest.raises(ValueError,match='executable'): host.service_info()


@pytest.mark.skipif(__import__('os').name=='nt',reason='POSIX symlink and ownership fixture')
def test_unsafe_symlink_and_writable_managed_path_rejected(tmp_path):
    import os
    path=tmp_path/'alias'; path.symlink_to('/etc/passwd')
    with pytest.raises(ValueError): updater.trusted(path)
    path.unlink(); path.write_bytes(b'file'); path.chmod(0o666)
    with pytest.raises(ValueError): updater.trusted(path)

def center_host(tmp_path,monkeypatch,external=False,fail=False,keys_owner=0,keys_mode=None):
    import os
    from types import SimpleNamespace
    home=tmp_path/'center'; home.mkdir(mode=0o700); (home/'keys').mkdir(mode=0o700)
    state=dict(schema_version=1,current=dict(version='v1.15.0',sha256='1'*64,image='ghcr.io/art-shier/ctl-server@sha256:'+'2'*64),pending=None)
    for name,value in [('server-state.json',state),('instance.json',{'external_database':external,'api_port':8080,'origin':'https://example.com'})]: updater.write_json(home/name,value)
    for path,raw in [(home/'compose.env',b'PRIVATE=secret'),(home/'keys'/'database.url',b'postgresql://user:private-password@db/ctl')]:
        path.write_bytes(raw); path.chmod(0o600)
    # A non-root CI runner cannot chown its fixture to the production key owner.
    # Override only this one directory's owner; retain real metadata everywhere
    # else, and retain its real mode unless a permission-negative case overrides it.
    original_stat=Path.stat
    def fixture_stat(path,*args,**kwargs):
        info=original_stat(path,*args,**kwargs)
        if path==home/'keys':
            values=list(info); values[4]=keys_owner
            if keys_mode is not None: values[0]=keys_mode
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path,'stat',fixture_stat)
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:Path(path))
    monkeypatch.setattr(updater,'read_platform_key',updater.read_private)
    monkeypatch.setattr(updater.server_bundle,'read_state',updater.read_json)
    monkeypatch.setattr(updater.server_bundle,'read_instance',lambda path:updater.read_json(path/'instance.json'))
    monkeypatch.setattr(updater.server_bundle,'check_project',lambda path:None)
    monkeypatch.setattr(updater.server_bundle,'installed_content',lambda *args:None)
    monkeypatch.setattr(updater,'pg_dump_executable',lambda:'/usr/bin/pg_dump')
    calls=[]
    def runner(argv,**kwargs):
        calls.append((argv,kwargs))
        if not fail: kwargs['stdout'].write(b'PGDMP verified database fixture')
        return SimpleNamespace(returncode=1 if fail else 0,stdout=b'')
    host=updater.Host(dict(IDENTITY,kind='center',target_id='center',server='https://example.com',cli_path=str(tmp_path/'ctl'),server_home=str(home)),runner)
    return host,calls


def test_external_database_credentials_only_in_private_environment(tmp_path,monkeypatch):
    host,calls=center_host(tmp_path,monkeypatch,external=True)
    jobdir=tmp_path/'job'; jobdir.mkdir(mode=0o700)
    host.backup(dict(JOB,kind='center'),jobdir)
    argv,options=calls[-1]
    assert argv==['/usr/bin/pg_dump','--format=custom','--no-owner','--no-acl']
    assert options['env']['PGDATABASE']=='postgresql://user:private-password@db/ctl'
    assert 'private-password' not in ' '.join(argv)
    assert updater.read_json(jobdir/'backup'/'complete.json')['database_bytes']>5
    assert (jobdir/'backup'/'keys'/'database.url').read_bytes()==(host.server_home/'keys'/'database.url').read_bytes()


def test_builtin_database_backup_uses_fixed_verified_compose(tmp_path,monkeypatch):
    host,calls=center_host(tmp_path,monkeypatch)
    jobdir=tmp_path/'job'; jobdir.mkdir(mode=0o700); host.backup(dict(JOB,kind='center'),jobdir)
    argv,_=calls[-1]
    assert argv[:4]==['docker','compose','--project-name','ctl-platform']
    assert argv[argv.index('--profile'):]==['--profile','database','exec','-T','database','pg_dump','-U','ctl','-d','ctl','--format=custom','--no-owner','--no-acl']
    assert 'pg_dump' in argv and str(host.server_home/'compose.env') in argv


def test_database_dump_failure_leaves_evidence_without_complete_marker(tmp_path,monkeypatch):
    host,_=center_host(tmp_path,monkeypatch,fail=True)
    jobdir=tmp_path/'job'; jobdir.mkdir(mode=0o700)
    with pytest.raises(RuntimeError): host.backup(dict(JOB,kind='center'),jobdir)
    assert (jobdir/'backup'/'server-state.json').exists()
    assert not (jobdir/'backup'/'complete.json').exists()


@pytest.mark.parametrize('owner,mode',[(1001,0o40700),(0,0o40720),(10001,0o40702)])
def test_center_backup_rejects_untrusted_key_directory_before_dump(tmp_path,monkeypatch,owner,mode):
    from types import SimpleNamespace
    host,calls=center_host(tmp_path,monkeypatch,keys_owner=owner,keys_mode=mode)
    # Exercise the production POSIX guard on Windows too. The rejected branch
    # must not reach any later OS operation or database command.
    monkeypatch.setattr(updater,'os',SimpleNamespace(name='posix'))
    jobdir=tmp_path/'job'; jobdir.mkdir(mode=0o700)
    with pytest.raises(ValueError,match='unsafe center keys directory'):
        host.backup(dict(JOB,kind='center'),jobdir)
    assert calls==[]
    assert not (jobdir/'backup'/'database.dump').exists()
    assert not (jobdir/'backup'/'complete.json').exists()

@pytest.mark.parametrize('field',['command','url','cli_path','agent_service'])
def test_remote_tasks_cannot_supply_commands_or_paths(field):
    with pytest.raises(ValueError): updater.validate_job(dict(JOB,**{field:'untrusted'}),IDENTITY)


def test_staged_journal_interruption_is_never_executed(tmp_path):
    w=worker(tmp_path)
    staging=tmp_path/'jobs'/'.stage-orphan'; staging.mkdir(mode=0o700)
    updater.write_json(staging/'journal.json',dict(job=JOB,stage='starting',acknowledged=False))
    w.client.job=None; w.run_once()
    assert w.host.events==[]

def test_install_checks_started_updater_service(tmp_path,monkeypatch):
    from types import SimpleNamespace
    unit=tmp_path/'ctl-updater.service'
    monkeypatch.setattr(updater,'service_unit',lambda home:'expected unit')
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:unit)
    calls=[]
    monkeypatch.setattr(updater,'Host',lambda config:SimpleNamespace(command=lambda argv,**kwargs:calls.append(argv)))
    updater.install_service(tmp_path,{},'ctl-updater.service')
    assert calls[-1]==['systemctl','is-active','--quiet','ctl-updater.service']


def test_enroll_copies_private_bootstrap_and_validates_bound_identity(tmp_path,monkeypatch):
    import zipfile
    from types import SimpleNamespace
    cli=tmp_path/'ctl'
    with zipfile.ZipFile(cli,'w') as archive: archive.writestr('__main__.py','print("1.15.0")')
    home=tmp_path/'updater'
    args=SimpleNamespace(home=str(home),kind='center',target_id='center',server='https://example.com',
        cli_path=str(cli),agent_home=str(tmp_path/'agent'),agent_service='ctl-agent.service',server_home=str(tmp_path/'server'))
    monkeypatch.setattr(updater,'require_root',lambda:None)
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs:Path(path))
    monkeypatch.setattr(updater,'installer_identity',lambda path:{'version':'v1.15.0'})
    monkeypatch.setattr(updater,'Host',lambda config:SimpleNamespace(center_state=lambda:{'pending':None,'current':{'version':'v1.14.0'}}))
    response=dict(updater_id=ID,token='private_token_123456789',kind='center',target_id='center')
    calls=[]
    class EnrollmentClient:
        def json(self,method,path,payload): calls.append((path,payload)); return response
    monkeypatch.setattr(updater,'PlatformClient',lambda credentials:EnrollmentClient())
    assert updater.enroll(args,'enrollment_secret_123456')==ID
    copied=(home/'bootstrap.pyz').read_bytes(); cli.write_bytes(b'replaced CLI')
    assert (home/'bootstrap.pyz').read_bytes()==copied
    config=updater.read_json(home/'updater.json')
    assert config['token']==response['token']
    assert 'enrollment_secret' not in (home/'updater.json').read_text()
    assert calls[0][0]=='/api/v1/updater/enroll'


def test_center_origin_drift_refuses_all_operations(tmp_path,monkeypatch):
    host,calls=center_host(tmp_path,monkeypatch)
    host.config['server']='https://center-a.example'
    cfg=updater.read_json(host.server_home/'instance.json'); cfg['origin']='https://center-b.example'
    updater.write_json(host.server_home/'instance.json',cfg)
    with pytest.raises(ValueError,match='origin'): host.center_state()
    assert host.readiness()[1] is False
    directory=tmp_path/'job'; directory.mkdir(mode=0o700)
    with pytest.raises(ValueError,match='origin'): host.backup(dict(JOB,kind='center'),directory)
    with pytest.raises(ValueError,match='origin'): host.install(dict(JOB,kind='center'),directory,lambda stage:None)
    with pytest.raises(ValueError,match='origin'): host.reconcile(dict(JOB,kind='center'),directory)
    assert calls==[]


def test_enroll_center_origin_mismatch_rejected_before_token_exchange(tmp_path,monkeypatch):
    import zipfile
    from types import SimpleNamespace
    host,_=center_host(tmp_path,monkeypatch)
    cli=tmp_path/'ctl'
    with zipfile.ZipFile(cli,'w') as archive: archive.writestr('__main__.py','print("1.15.0")')
    args=SimpleNamespace(home=str(tmp_path/'updater'),kind='center',target_id='center',server='https://foreign.example',
        cli_path=str(cli),agent_home=str(tmp_path/'agent'),agent_service='ctl-agent.service',server_home=str(host.server_home))
    monkeypatch.setattr(updater,'require_root',lambda:None)
    monkeypatch.setattr(updater,'installer_identity',lambda path:{'version':'v1.15.0'})
    monkeypatch.setattr(updater,'PlatformClient',lambda credentials:pytest.fail('must not exchange credential for another local center'))
    with pytest.raises(ValueError,match='origin'): updater.enroll(args,'enrollment_secret_123456')
    assert not (Path(args.home)/'updater.json').exists()

def test_failed_agent_zero_pid_can_restore_and_reset_start_limit(tmp_path,monkeypatch):
    host,metadata,calls,state=managed_host(tmp_path,monkeypatch)
    directory=tmp_path/'job'; directory.mkdir(mode=0o700); host.backup(JOB,directory)
    old=host.executable.read_bytes(); host.executable.write_bytes(b'broken new binary')
    state.update(active='failed',pid='0')
    original_command=host.command
    def command(argv,**kwargs):
        if argv[1]=='stop': calls.append(argv); return ''
        return original_command(argv,**kwargs)
    host.command=command
    host.restore(JOB,directory,lambda stage:None)
    assert host.executable.read_bytes()==old
    assert [argv[1] for argv in calls]==['stop','reset-failed','start']

def enrollment_fixture(tmp_path,monkeypatch):
    import zipfile
    from types import SimpleNamespace
    host,_=center_host(tmp_path,monkeypatch)
    cli=tmp_path/'ctl'
    with zipfile.ZipFile(cli,'w') as archive: archive.writestr('__main__.py','print("1.15.0")')
    args=SimpleNamespace(home=str(tmp_path/'updater'),kind='center',target_id='center',server='https://example.com',
        cli_path=str(cli),agent_home=str(tmp_path/'agent'),agent_service='ctl-agent.service',server_home=str(host.server_home))
    monkeypatch.setattr(updater,'require_root',lambda:None)
    monkeypatch.setattr(updater,'installer_identity',lambda path:{'version':'v1.15.0'})
    response=dict(updater_id=ID,token='private_token_123456789',kind='center',target_id='center')
    client=SimpleNamespace(json=lambda *args:response)
    monkeypatch.setattr(updater,'PlatformClient',lambda credentials:client)
    return args,client


def test_acknowledged_attention_history_does_not_prevent_server_approved_rotation(tmp_path,monkeypatch):
    args,client=enrollment_fixture(tmp_path,monkeypatch)
    updater.enroll(args,'enrollment_secret_123456')
    directory=Path(args.home)/'jobs'/JOB['id']; directory.mkdir(parents=True,mode=0o700)
    updater.write_json(directory/'journal.json',dict(job=dict(JOB,kind='center',target_id='center'),acknowledged=True,report={'status':'needs_attention'}))
    (directory.parent/'.stage-incomplete').mkdir(mode=0o700)
    assert updater.enroll(args,'second_enrollment_secret_123456')==ID
    assert (directory/'journal.json').exists()


def test_rejected_rotation_preserves_existing_bootstrap_and_identity(tmp_path,monkeypatch):
    import zipfile
    args,client=enrollment_fixture(tmp_path,monkeypatch)
    updater.enroll(args,'enrollment_secret_123456')
    home=Path(args.home); before={name:(home/name).read_bytes() for name in ('updater.json','bootstrap.pyz')}
    with zipfile.ZipFile(args.cli_path,'w') as archive: archive.writestr('__main__.py','print("candidate bootstrap")')
    def rejection(*args): raise RuntimeError('unresolved server job')
    client.json=rejection
    with pytest.raises(RuntimeError): updater.enroll(args,'second_enrollment_secret_123456')
    assert {name:(home/name).read_bytes() for name in before}==before


def test_rotation_rejects_different_origin_before_exchange(tmp_path,monkeypatch):
    args,client=enrollment_fixture(tmp_path,monkeypatch)
    updater.enroll(args,'enrollment_secret_123456')
    config=updater.read_json(Path(args.home)/'updater.json'); config['server']='https://other.example'
    updater.write_json(Path(args.home)/'updater.json',config)
    client.json=lambda *args:pytest.fail('different identity must not exchange token')
    with pytest.raises(ValueError,match='identity'): updater.enroll(args,'second_enrollment_secret_123456')


def test_journal_parent_synced_before_start(tmp_path,monkeypatch):
    synced=[]
    monkeypatch.setattr(updater,'fsync_directory',lambda path:synced.append(Path(path)))
    client=Client(); original=client.json
    def request(method,path,payload):
        if path.endswith('/start'):
            assert tmp_path in synced
            assert tmp_path/'jobs' in synced
        return original(method,path,payload)
    client.json=request
    w=worker(tmp_path,client=client); w.run_once()
    assert w.host.events

def pg_dump_link_fixture(tmp_path,monkeypatch,*,target_mode=0o755,link_uid=0,parent_mode=0o755,loop=False):
    import stat
    from types import SimpleNamespace
    command=tmp_path/'usr'/'bin'/'pg_dump'
    target=tmp_path/'usr'/'share'/'postgresql-common'/'pg_wrapper'
    def info(path,*args,**kwargs):
        if path==command: return SimpleNamespace(st_uid=link_uid,st_mode=stat.S_IFLNK|0o777)
        if path==target: return SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|target_mode)
        return SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|(parent_mode if path==target.parent else 0o755))
    monkeypatch.setattr(Path,'lstat',info)
    monkeypatch.setattr(updater.os,'readlink',lambda path:command.name if loop else '../share/postgresql-common/pg_wrapper')
    monkeypatch.setattr(updater.shutil,'which',lambda *args,**kwargs:str(command))
    return command,target


def test_pg_dump_trusted_distro_link_preserves_invocation_name(tmp_path,monkeypatch):
    command,target=pg_dump_link_fixture(tmp_path,monkeypatch)
    assert updater.pg_dump_executable()==str(command)
    assert updater.pg_dump_executable()!=str(target)


@pytest.mark.parametrize('changes',[{'target_mode':0o777},{'target_mode':0o644},{'link_uid':1001},{'parent_mode':0o777},{'loop':True}])
def test_pg_dump_rejects_untrusted_link_target_or_ancestors(tmp_path,monkeypatch,changes):
    pg_dump_link_fixture(tmp_path,monkeypatch,**changes)
    with pytest.raises(ValueError): updater.pg_dump_executable()


def test_external_database_readiness_uses_same_trusted_resolver(tmp_path,monkeypatch):
    host,_=center_host(tmp_path,monkeypatch,external=True)
    calls=[]
    def reject(): calls.append('resolved'); raise ValueError('unsafe PostgreSQL client')
    monkeypatch.setattr(updater,'pg_dump_executable',reject)
    assert host.readiness()==('v1.15.0',False,'backup_unavailable')
    directory=tmp_path/'job'; directory.mkdir(mode=0o700)
    with pytest.raises(ValueError): host.backup(dict(JOB,kind='center'),directory)
    assert calls==['resolved','resolved']


def test_key_backup_directory_synced_before_complete_marker(tmp_path,monkeypatch):
    host,_=center_host(tmp_path,monkeypatch)
    directory=tmp_path/'job'; directory.mkdir(mode=0o700)
    events=[]; original=updater.write_json
    monkeypatch.setattr(updater,'fsync_directory',lambda path:events.append(('sync',Path(path))))
    def write(path,value):
        if path.name=='complete.json':
            assert ('sync',directory/'backup'/'keys') in events
            assert (directory/'backup'/'keys'/'database.url').exists()
            events.append(('complete',path))
        return original(path,value)
    monkeypatch.setattr(updater,'write_json',write)
    host.backup(dict(JOB,kind='center'),directory)
    assert events[-1][0]=='complete'


def test_inactive_agent_restart_does_not_reset_unloaded_unit(tmp_path,monkeypatch):
    host,_,calls,state=managed_host(tmp_path,monkeypatch)
    state.update(active='inactive',pid='0')
    original=host.command
    def command(argv,**kwargs):
        if argv[1]=='reset-failed': raise RuntimeError('unit not loaded after inactive GC')
        return original(argv,**kwargs)
    host.command=command
    host.restart_agent(tmp_path)
    assert [argv[1] for argv in calls]==['start']
    assert state['active']=='active'


def test_failed_agent_restart_stops_if_reset_start_limit_fails(tmp_path,monkeypatch):
    host,_,calls,state=managed_host(tmp_path,monkeypatch)
    state.update(active='failed',pid='0')
    def command(argv,**kwargs):
        calls.append(argv)
        if argv[1]=='reset-failed': raise RuntimeError('cannot clear failed/startlimit state')
    host.command=command
    with pytest.raises(RuntimeError): host.restart_agent(tmp_path)
    assert [argv[1] for argv in calls]==['reset-failed']
