"""Integrated installation preserves local identities and service boundaries."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployctl import cli, node_agent, updater


def test_new_install_and_repair_commands_derive_role_defaults():
    args = cli.parser().parse_args(['agent', 'install', '--server', 'https://center.test'])
    assert args.updater_home == '/var/lib/ctl-updater-node'
    assert args.updater_service == 'ctl-updater-node.service'
    args = cli.parser().parse_args(['updater', 'setup', '--kind', 'center'])
    assert args.home is None and args.service_name is None
    assert not hasattr(args, 'token_file')
    args = cli.parser().parse_args(['server','install','--cli-path','/opt/ctl/bin/ctl', '--updater-home','/var/lib/custom-updater','--updater-service','ctl-updater-custom.service'])
    assert args.cli_path == '/opt/ctl/bin/ctl'


def setup_fixture(tmp_path, monkeypatch, kind='node'):
    import zipfile
    monkeypatch.setattr(updater, 'require_root', lambda: None)
    monkeypatch.setattr(updater, 'trusted', lambda path, **kwargs: Path(path).absolute())
    agent = tmp_path/'agent'; agent.mkdir()
    node_agent.write_json(agent/'node.json', dict(server='https://center.test', node_id='a'*32, token='node_private_credential'))
    home = tmp_path/'updater'
    executable = tmp_path/'ctl'
    with zipfile.ZipFile(executable,'w') as archive: archive.writestr('payload','trusted installed bytes')
    monkeypatch.setattr(updater, 'installer_identity', lambda *args: {'version':'v1.15.0'})
    monkeypatch.setattr(updater.Host, 'service_info', lambda self: {})
    args = SimpleNamespace(kind=kind, home=str(home), cli_path=str(executable), agent_home=str(agent),
                           agent_service='ctl-agent.service', server_home=str(tmp_path/'center'), service_name=None)
    monkeypatch.setattr(updater, 'install_service', lambda *args: None)
    return args


def test_setup_revalidates_installer_identity_before_enrollment(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    def invalid(*args): raise ValueError('managed executable has changed')
    monkeypatch.setattr(updater, 'installer_identity', invalid)
    with pytest.raises(ValueError, match='changed'):
        updater.setup(args)


def test_setup_uses_saved_node_without_prompt_and_reuses_updater_identity(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    calls = []
    class Client:
        def __init__(self, credentials):
            assert credentials.server == 'https://center.test'
            assert credentials.token == 'node_private_credential'
        def json(self, method, path, body):
            calls.append((path, body))
            return {'enrollment_token': 'temporary_enrollment_credential'}
    monkeypatch.setattr(updater, 'PlatformClient', Client)
    def enroll(args, token, **kwargs):
        assert token == 'temporary_enrollment_credential'
        updater.private_directory(args.home)
        updater.write_json(Path(args.home)/'updater.json', dict(kind='node', target_id='a'*32,
            server=args.server, cli_path=args.cli_path, agent_home=args.agent_home,
            agent_service=args.agent_service, server_home=args.server_home,
            updater_id='b'*32, token='dedicated_updater_credential', python=str(Path(updater.sys.executable).resolve())))
        (Path(args.home)/'bootstrap.pyz').write_bytes(b'private bootstrap')
        return 'b'*32
    monkeypatch.setattr(updater, 'enroll', enroll)
    updater.setup(args)
    before = (Path(args.home)/'updater.json').read_bytes()
    updater.setup(args)
    assert calls == [('/api/v1/node/updater-enrollment', {})]
    assert (Path(args.home)/'updater.json').read_bytes() == before
    assert b'node_private_credential' not in before


def test_setup_rejects_saved_updater_path_mismatch_before_enrollment(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    updater.private_directory(args.home)
    updater.write_json(Path(args.home)/'updater.json', dict(kind='node', target_id='a'*32,
        server='https://center.test', cli_path='/different/ctl'))
    with pytest.raises(ValueError, match='configuration'):
        updater.setup(args)


def test_setup_refuses_unfinished_journal_without_mutating_identity(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    journal = Path(args.home)/'jobs'/('c'*32)
    updater.private_directory(journal)
    updater.write_json(journal/'journal.json', {'stage':'installing'})
    with pytest.raises(ValueError, match='unfinished'):
        updater.setup(args)


@pytest.mark.parametrize('failure', ['runtime','timeout'])
def test_center_cli_reports_partial_success_when_updater_setup_fails(monkeypatch, capsys, failure):
    import subprocess
    from deployctl import server_bundle
    monkeypatch.setattr(server_bundle, 'deploy_server', lambda *args, **kwargs: {'current': {'version':'v1.15.0'}})
    def fail(*args, **kwargs):
        if failure == 'timeout': raise subprocess.TimeoutExpired(['systemctl'],60)
        raise RuntimeError('secret-owner-token')
    monkeypatch.setattr(updater, 'setup', fail)
    assert cli.main(['server', 'install']) == 1
    output = capsys.readouterr()
    assert 'center installed' in output.err and 'automatic updates not configured' in output.err
    assert 'secret-owner-token' not in output.err


def test_agent_install_keeps_existing_identity_and_repairs_without_prompt(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    args = cli.parser().parse_args(['agent', 'install', '--home', args.agent_home,
        '--executable', args.cli_path, '--root', str(tmp_path/'deployments'),
        '--config-root', str(tmp_path/'config'), '--updater-home', args.home])
    unit = tmp_path/'ctl-agent.service'
    original_trusted = updater.trusted
    monkeypatch.setattr(updater, 'trusted', lambda path, **kwargs: unit if str(path).replace('\\','/').endswith('/etc/systemd/system/ctl-agent.service') else original_trusted(path, **kwargs))
    monkeypatch.setattr(updater, 'installer_identity', lambda *args: {'version':'v1.15.0'})
    monkeypatch.setattr(node_agent, 'service_unit', lambda *args: '[Service]\nExecStart=installed agent\n')
    events = []
    monkeypatch.setattr(updater.Host, 'command', lambda self, argv, **kwargs: events.append(argv))
    monkeypatch.setattr(updater, 'setup', lambda args: events.append(('updater', args.agent_home, args.agent_service)))
    monkeypatch.setattr(node_agent.getpass, 'getpass', lambda *args: pytest.fail('prompted for existing identity'))
    before = (Path(args.home)/'node.json').read_bytes()
    node_agent.install(args)
    node_agent.install(args)
    assert (Path(args.home)/'node.json').read_bytes() == before
    assert unit.read_text() == '[Service]\nExecStart=installed agent\n'
    assert events[-1] == ('updater', args.home, 'ctl-agent.service')
    unit.write_text('different service')
    with pytest.raises(ValueError, match='existing'):
        node_agent.install(args)


def test_agent_installed_partial_failure_retains_identity_and_unit(tmp_path, monkeypatch):
    args = setup_fixture(tmp_path, monkeypatch)
    args = cli.parser().parse_args(['agent','install','--home',args.agent_home,'--executable',args.cli_path])
    unit = tmp_path/'ctl-agent.service'
    original_trusted = updater.trusted
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs: unit if str(path).replace('\\','/').endswith('/etc/systemd/system/ctl-agent.service') else original_trusted(path,**kwargs))
    monkeypatch.setattr(updater,'installer_identity',lambda *args: {'version':'v1.15.0'})
    monkeypatch.setattr(node_agent,'service_unit',lambda *args: 'service')
    monkeypatch.setattr(updater.Host,'command',lambda *args,**kwargs: '')
    def fail(*args): raise RuntimeError('private credential')
    monkeypatch.setattr(updater,'setup',fail)
    with pytest.raises(RuntimeError,match='Agent installed and started; automatic updates not configured') as error:
        node_agent.install(args)
    assert 'private credential' not in str(error.value)
    assert unit.exists() and (Path(args.home)/'node.json').exists()


def test_center_setup_enrolls_over_loopback_and_never_persists_owner(tmp_path, monkeypatch):
    import zipfile
    args = setup_fixture(tmp_path, monkeypatch, kind='center')
    monkeypatch.setattr(updater.server_bundle, 'private', lambda path: path)
    center = Path(args.server_home); center.mkdir()
    updater.write_json(center/'instance.json', {'origin':'https://public-dns-unavailable.test','api_port':8091,'external_database':False})
    keys = center/'keys'; keys.mkdir()
    (keys/'owner.token').write_text('private_local_owner_credential'); (keys/'owner.token').chmod(0o600)
    with zipfile.ZipFile(args.cli_path,'w') as archive: archive.writestr('payload','trusted installed bytes')
    monkeypatch.setattr(updater, 'installer_identity', lambda *args: {'version':'v1.15.0'})
    monkeypatch.setattr(updater.Host, 'center_state', lambda self: {'pending':None,'current':{'version':'v1.15.0'}})
    calls=[]
    class Client:
        def __init__(self, credentials): self.credentials = credentials
        def json(self, method, path, body):
            calls.append((self.credentials.server,self.credentials.token,path,body))
            if path.endswith('/enrollments'): return {'enrollment_token':'one_use_updater_enrollment'}
            return {'kind':'center','target_id':'center','updater_id':'b'*32,'token':'dedicated_updater_credential'}
    monkeypatch.setattr(updater, 'PlatformClient', Client)
    updater.setup(args)
    config = updater.read_json(Path(args.home)/'updater.json')
    assert config['server'] == 'https://public-dns-unavailable.test'
    assert config['token'] == 'dedicated_updater_credential'
    assert [entry[:3] for entry in calls] == [
        ('http://127.0.0.1:8091','private_local_owner_credential','/api/v1/system-updates/enrollments'),
        ('http://127.0.0.1:8091','one_use_updater_enrollment','/api/v1/updater/enroll')]
    assert b'private_local_owner_credential' not in (Path(args.home)/'updater.json').read_bytes()
    assert (Path(args.home)/'bootstrap.pyz').read_bytes() == Path(args.cli_path).read_bytes()


def test_new_agent_install_prompts_original_token_once_then_recovers_setup(tmp_path, monkeypatch):
    local = setup_fixture(tmp_path, monkeypatch)
    args = cli.parser().parse_args(['agent','install','--server','https://center.test',
        '--home',local.agent_home,'--executable',local.cli_path])
    identity_path=Path(args.home)/'node.json'; identity_path.unlink()
    unit=tmp_path/'ctl-agent.service'
    original_trusted=updater.trusted
    monkeypatch.setattr(updater,'trusted',lambda path,**kwargs: unit if str(path).replace('\\','/').endswith('/etc/systemd/system/ctl-agent.service') else original_trusted(path,**kwargs))
    monkeypatch.setattr(node_agent,'service_unit',lambda *args: 'fixture service')
    monkeypatch.setattr(updater.Host,'command',lambda *args,**kwargs: '')
    prompts=[]; enrollments=[]
    def prompt(label): prompts.append(label); return 'original_node_enrollment'
    monkeypatch.setattr(node_agent.getpass,'getpass',prompt)
    class Client:
        server='https://center.test'
        def __init__(self, credentials): assert credentials.token=='original_node_enrollment'
        def json(self, method, path, body):
            enrollments.append(path)
            return {'node_id':'a'*32,'token':'permanent_node_credential'}
    monkeypatch.setattr(node_agent,'PlatformClient',Client)
    def failed_setup(args): raise RuntimeError('systemd unavailable')
    monkeypatch.setattr(updater,'setup',failed_setup)
    with pytest.raises(RuntimeError,match='Agent installed and started'):
        node_agent.install(args)
    before=identity_path.read_bytes()
    monkeypatch.setattr(updater,'setup',lambda args: None)
    node_agent.install(args)
    assert len(prompts)==1 and enrollments==['/api/v1/node/enroll']
    assert identity_path.read_bytes()==before


def test_agent_install_requires_root_before_creating_state(tmp_path, monkeypatch):
    args=cli.parser().parse_args(['agent','install','--home',str(tmp_path/'absent')])
    def refuse(): raise ValueError('require Linux root')
    monkeypatch.setattr(updater,'require_root',refuse)
    with pytest.raises(ValueError,match='root'): node_agent.install(args)
    assert not Path(args.home).exists()
