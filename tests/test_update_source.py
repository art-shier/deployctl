import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock
import pytest
from deployctl import bootstrap, server_bundle, updater
from scripts import sync_update_source as publisher

STAMP='2026-11-06T00:00:00Z'

def catalog(cli=b'cli',server=b'server'):
    return {'schema_version':1,'generated_at':STAMP,'releases':[{'version':'v1.17.0','published_at':STAMP,'cli_sha256':hashlib.sha256(cli).hexdigest(),'server_sha256':hashlib.sha256(server).hexdigest()}]}

@pytest.mark.parametrize('bad',['http://mirror.example','https://u:p@mirror.example','https://mirror.example/a/../b','https://mirror.example/?key=secret','https://mirror.example/a%2fb','https://mirror.example/$VAR'])
def test_source_rejects_unsafe_url(bad):
    with pytest.raises(ValueError): bootstrap.update_source(bad)

def test_cli_and_center_use_only_configured_source(monkeypatch):
    seen=[]
    def fetch(request,limit):
        seen.append(request.full_url)
        assert not request.has_header('Authorization')
        if request.full_url.endswith('/update-index.json'): return json.dumps(catalog()).encode()
        if request.full_url.endswith('.sha256'): return hashlib.sha256(b'cli').hexdigest().encode()
        return b'cli'
    monkeypatch.setenv('CTL_UPDATE_SOURCE','https://mirror.example/ctl')
    monkeypatch.setattr(bootstrap,'fetch',fetch)
    monkeypatch.setattr(server_bundle,'fetch',fetch)
    assert bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY,'latest','must-not-leak')[0]==b'cli'
    assert bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY,'v1.17.0','must-not-leak')[0]==b'cli'
    url,version,digest=server_bundle.resolve_server_release()
    assert url=='https://mirror.example/ctl/v1.17.0/ctl-platform-v1.17.0.tar.gz'
    assert digest==hashlib.sha256(b'server').hexdigest()
    assert all(url.startswith('https://mirror.example/ctl/') for url in seen)

def test_mirror_failure_never_falls_back_to_github(monkeypatch):
    seen=[]
    def fetch(request,limit):
        seen.append(request.full_url)
        raise RuntimeError('offline')
    monkeypatch.setattr(bootstrap,'fetch',fetch)
    with pytest.raises(RuntimeError): bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY,'latest','',source='https://mirror.example')
    assert seen==['https://mirror.example/update-index.json']

def test_updater_downloads_pinned_package_from_center(monkeypatch,tmp_path):
    raw=b'center-proxied-cli'
    host=updater.Host({'cli_path':'/bin/ctl','kind':'node','server':'https://center.example','token':'fixture'})
    host.package_proxy=True
    client=Mock();client.request.return_value=raw
    monkeypatch.setattr(updater,'PlatformClient',lambda _:client)
    monkeypatch.setattr(updater,'Credentials',lambda *args:args)
    monkeypatch.setattr(bootstrap,'fetch',lambda *args:pytest.fail('must not contact external source'))
    monkeypatch.setattr(bootstrap,'artifact_version',lambda path:'1.17.0')
    job={'id':'a'*32,'version':'v1.17.0','cli_sha256':hashlib.sha256(raw).hexdigest()}
    host.prepare(job,tmp_path)
    client.request.assert_called_once_with('GET','/api/v1/updater/jobs/'+'a'*32+'/package',limit=bootstrap.MAX_DOWNLOAD)
    assert (tmp_path/'deployctl.pyz').read_bytes()==raw
    client.request.side_effect=RuntimeError('offline')
    with pytest.raises(RuntimeError):host.prepare(job,tmp_path)

def test_publication_exposes_index_last_and_preserves_previous_on_failure(monkeypatch,tmp_path):
    assets={'deployctl.pyz':b'cli','ctl-platform-v1.17.0.tar.gz':b'server','install.sh':b'installer','team-deploy-skill-v1.17.0.zip':b'skill'}
    for name,raw in assets.items():
        (tmp_path/name).write_bytes(raw)
        (tmp_path/(name+'.sha256')).write_text(hashlib.sha256(raw).hexdigest()+'  '+name+'\n')
    (tmp_path/'update-index.json').write_text(json.dumps(catalog()))
    remote={};puts=[]
    def fetch(request,limit):
        key=request.full_url.removeprefix('https://mirror.example/')
        if key not in remote:
            error=RuntimeError('not found');error.__cause__=HTTPError(request.full_url,404,'not found',{},None);raise error
        return remote[key]
    def upload(command,check):
        source=Path(command[3]);key=command[4].removeprefix('s3://bucket/')
        puts.append(key);remote[key]=source.read_bytes()
    monkeypatch.setattr(publisher,'fetch',fetch)
    publisher.sync('v1.17.0',tmp_path,'https://mirror.example','s3://bucket',runner=upload)
    assert puts[-1]=='update-index.json'
    previous=remote['update-index.json']
    def failing(command,check): raise RuntimeError('upload failed')
    with pytest.raises(RuntimeError):publisher.sync('v1.17.0',tmp_path,'https://mirror.example','s3://bucket',runner=failing)
    assert remote['update-index.json']==previous
    remote['v1.17.0/deployctl.pyz']=b'tampered'
    with pytest.raises(ValueError,match='immutable'):publisher.sync('v1.17.0',tmp_path,'https://mirror.example','s3://bucket',runner=upload)

def test_installer_remembers_source_for_self_update(tmp_path,monkeypatch):
    from deployctl import self_update
    monkeypatch.delenv('CTL_UPDATE_SOURCE',raising=False)
    package=tmp_path/'cli.pyz';package.write_bytes(b"print('1.17.0')\n")
    Path(str(package)+'.sha256').write_text(hashlib.sha256(package.read_bytes()).hexdigest())
    folder=tmp_path/'bin'
    bootstrap.install(package,folder,source='https://mirror.example/ctl')
    calls=[]
    raw=b"print('1.18.0')\n"
    def download(repository,version,token,release_id=None,source=None):
        calls.append(source);return raw,hashlib.sha256(raw).hexdigest(),'v1.18.0'
    monkeypatch.setattr(bootstrap,'github_release',download)
    monkeypatch.setattr(bootstrap,'resolve_token',lambda:'')
    state=self_update.update_tool(executable=folder/'ctl')
    assert state['update_source']=='https://mirror.example/ctl'
    assert calls==['https://mirror.example/ctl']

def test_explicit_official_source_overrides_environment(monkeypatch):
    monkeypatch.setenv('CTL_UPDATE_SOURCE','https://mirror.example')
    seen=[]
    def fetch(request,limit):
        seen.append(request.full_url)
        if request.full_url.endswith('update-index.json'):return json.dumps(catalog()).encode()
        return b'cli'
    monkeypatch.setattr(bootstrap,'fetch',fetch)
    bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY,'latest','',source='')
    assert seen[0]==bootstrap.INDEX_URL
    assert all(url.startswith('https://github.com/') for url in seen)

@pytest.mark.skipif(__import__('os').name=='nt',reason='platform instance paths require Linux')
def test_center_source_survives_repeat_bootstrap_and_can_be_cleared(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('source_bootstrap_config',Path(__file__).resolve().parents[1]/'control-deploy/bootstrap_config.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    home=tmp_path/'center'
    first=module.prepare(home,'registry.example/ctl@sha256:'+'a'*64,update_source='https://mirror.example/ctl')
    repeat=module.prepare(home,'registry.example/ctl@sha256:'+'b'*64)
    assert repeat['update_source']=='https://mirror.example/ctl'
    assert repeat['postgres_password']==first['postgres_password']
    assert 'CTL_UPDATE_SOURCE=https://mirror.example/ctl\n' in (home/'compose.env').read_text()
    module.prepare(home,'registry.example/ctl@sha256:'+'b'*64,update_source='')
    assert json.loads((home/'instance.json').read_text())['update_source']==''
