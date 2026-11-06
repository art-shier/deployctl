import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from deployctl import bootstrap, server_bundle, download

STAMP = '2026-11-06T00:00:00Z'


def entry(version='v1.15.1'):
    return dict(version=version, published_at=STAMP, cli_sha256='a'*64, server_sha256='b'*64)


def catalog(*releases):
    return dict(schema_version=1, generated_at=STAMP, releases=list(releases or [entry()]))


def test_manifest_validates_boundaries_and_sorts_versions():
    validate = getattr(bootstrap, 'parse_release_index', None)
    assert callable(validate), 'static release index validation is missing'
    assert [r['version'] for r in validate(json.dumps(catalog(entry('v1.9.0'), entry('v1.15.1'))).encode())['releases']] == ['v1.15.1', 'v1.9.0']
    invalid = [[], dict(catalog(), schema_version=True), dict(catalog(), generated_at='yesterday'),
               catalog(entry(), entry()), dict(catalog(), releases=[entry()]*1001)]
    for key, value in [('version', 'v01.15.1'), ('version', 'v1.15.1-rc1'), ('cli_sha256', 'A'*64),
                       ('server_sha256', 'b'*63), ('published_at', '2026-99-99T00:00:00Z')]:
        bad = entry(); bad[key] = value; invalid.append(catalog(bad))
    for value in invalid:
        with pytest.raises(ValueError): validate(json.dumps(value).encode())
    for raw in (b'{', b'x'*(1024*1024+1)):
        with pytest.raises(ValueError): validate(raw)


def test_official_latest_installer_uses_static_catalog_and_fixed_assets():
    calls = []
    content = b'cli artifact'
    release = entry(); release['cli_sha256'] = hashlib.sha256(content).hexdigest()
    def fetch(request, limit):
        calls.append(request.full_url)
        assert request.get_header('Authorization') is None
        if request.full_url.endswith('/latest/download/update-index.json'):
            return json.dumps(catalog(release)).encode()
        if request.full_url.endswith('/v1.15.1/deployctl.pyz'):
            return content
        raise AssertionError(request.full_url)
    with patch.object(bootstrap, 'fetch', fetch):
        raw, digest, tag = bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY, 'latest', 'existing-token')
    assert (raw, digest, tag) == (content, release['cli_sha256'], 'v1.15.1')
    assert len(calls) == 2


def test_official_exact_installer_uses_direct_sidecar_without_discovery():
    calls = []
    def fetch(request, limit):
        calls.append(request.full_url)
        return ('a'*64).encode() if request.full_url.endswith('.sha256') else b'cli'
    with patch.object(bootstrap, 'fetch', fetch):
        assert bootstrap.github_release(bootstrap.DEFAULT_REPOSITORY, '1.15.0', '') == (b'cli', 'a'*64, 'v1.15.0')
    assert calls == ['https://github.com/art-shier/deployctl/releases/download/v1.15.0/deployctl.pyz.sha256',
                     'https://github.com/art-shier/deployctl/releases/download/v1.15.0/deployctl.pyz']


def test_official_download_keeps_direct_assets_with_existing_token(monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'existing-token')
    url = 'https://github.com/art-shier/deployctl/releases/download/v1.15.1/ctl-platform-v1.15.1.tar.gz'
    with patch.object(download, 'fetch', side_effect=AssertionError('official download must avoid API')):
        request = download.resolve_asset(url)
    assert request.full_url == url
    assert request.get_header('Authorization') is None


def test_builder_is_deterministic_and_checks_actual_artifacts(tmp_path):
    from deployctl import release_catalog
    artifacts = tmp_path / 'assets'; artifacts.mkdir()
    cli = artifacts / 'deployctl.pyz'; cli.write_bytes(b'abc')
    server = artifacts / 'ctl-platform-v1.16.0.tar.gz'; server.write_bytes(b'xyz')
    for path in (cli, server):
        path.with_name(path.name+'.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n')
    history = catalog(entry())
    result = release_catalog.build_index(history, '2026-11-06T01:00:00Z', 'v1.16.0', STAMP, artifacts)
    assert result['releases'][0] == dict(version='v1.16.0', published_at=STAMP,
        cli_sha256='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
        server_sha256='3608bca1e44ea6c4d268eb6db02260269892c0b42b86bbf1e77a6fa16c3c9282')
    assert result['releases'][1] == entry()
    assert release_catalog.encode_index(result) == release_catalog.encode_index(release_catalog.build_index(history, '2026-11-06T01:00:00Z', 'v1.16.0', STAMP, artifacts))
    assert history == catalog(entry()), 'builder must not mutate its history input'
    cli.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'): release_catalog.build_index(history, STAMP, 'v1.16.0', STAMP, artifacts)


def test_builder_preserves_immutable_versions_and_rejects_invalid_history(tmp_path):
    from deployctl import release_catalog
    with pytest.raises(ValueError): release_catalog.build_index(dict(catalog(), schema_version=2), STAMP)
    for name in ('deployctl.pyz', 'ctl-platform-v1.15.1.tar.gz'):
        path = tmp_path/name; path.write_bytes(b'changed')
        path.with_name(path.name+'.sha256').write_text(hashlib.sha256(b'changed').hexdigest())
    with pytest.raises(ValueError, match='immutable'): release_catalog.build_index(catalog(), STAMP, 'v1.15.1', STAMP, tmp_path)


def test_publisher_bootstrap_excludes_drafts_prereleases_and_candidate(monkeypatch):
    from deployctl import release_catalog
    from urllib.error import HTTPError
    calls = []
    def metadata(tag, **changes):
        value = dict(tag_name=tag, published_at=STAMP, draft=False, prerelease=False, assets=[
            dict(name='deployctl.pyz', digest='sha256:'+'a'*64),
            dict(name='deployctl.pyz.sha256', url='https://api.github.com/repos/art-shier/deployctl/releases/assets/1'),
            dict(name=f'ctl-platform-{tag}.tar.gz', digest='sha256:'+'b'*64),
            dict(name=f'ctl-platform-{tag}.tar.gz.sha256', url='https://api.github.com/repos/art-shier/deployctl/releases/assets/2')])
        value.update(changes); return value
    def fetch(request, limit):
        calls.append(request.full_url)
        if request.full_url.endswith('/latest/download/update-index.json'):
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)
        assert request.get_header('Authorization') == 'Bearer CI-token'
        if '/releases?per_page=100&page=1' in request.full_url:
            return json.dumps([metadata('v1.15.1'), metadata('v1.16.0'), metadata('v1.17.0',draft=True), metadata('v1.18.0',prerelease=True)]).encode()
        return ('a'*64 if request.full_url.endswith('/1') else 'b'*64).encode()
    monkeypatch.setattr(release_catalog, 'fetch', fetch)
    result = release_catalog.published_history('art-shier/deployctl', 'CI-token', STAMP, 'v1.16.0')
    assert result['releases'] == [entry()]
    assert len(calls) == 4


def test_publisher_network_and_malformed_index_do_not_silently_bootstrap(monkeypatch):
    from deployctl import release_catalog
    for failure in (RuntimeError('network failure'), ValueError('invalid manifest')):
        def fetch(request, limit): raise failure
        monkeypatch.setattr(release_catalog, 'fetch', fetch)
        with pytest.raises(type(failure)): release_catalog.published_history('art-shier/deployctl', 'token', STAMP)


def test_index_rejects_duplicate_fields_and_out_of_range_versions():
    validate = getattr(bootstrap, 'parse_release_index')
    raw = json.dumps(catalog()).replace('"schema_version": 1', '"schema_version": 2, "schema_version": 1')
    with pytest.raises(ValueError): validate(raw.encode())
    raw = json.dumps(catalog()).replace('"version": "v1.15.1"', '"version": "v1.15.0", "version": "v1.15.1"')
    with pytest.raises(ValueError): validate(raw.encode())
    with pytest.raises(ValueError): validate(json.dumps(catalog(entry('v4294967296.0.0'))).encode())


def test_written_index_hash_matches_sidecar_and_sha256sums(tmp_path):
    from deployctl import release_catalog
    output = tmp_path/'update-index.json'
    (tmp_path/'SHA256SUMS').write_text('a'*64+'  deployctl.pyz\n'+'b'*64+'  update-index.json\n')
    release_catalog.write_index(output, catalog(), checksum=True)
    first = output.read_bytes()
    digest = hashlib.sha256(first).hexdigest()
    assert output.with_name('update-index.json.sha256').read_text() == digest+'  update-index.json\n'
    assert (tmp_path/'SHA256SUMS').read_text().splitlines() == ['a'*64+'  deployctl.pyz',digest+'  update-index.json']
    release_catalog.write_index(output, catalog(), checksum=True)
    assert output.read_bytes() == first
    assert len((tmp_path/'SHA256SUMS').read_text().splitlines()) == 2


def test_generated_standalone_installer_discovers_latest_without_package_import(tmp_path):
    root = Path(__file__).resolve().parents[1]
    generated = (root/'install.sh').read_text().split("<<'DEPLOYCTL_INSTALL_PY'\n",1)[1].rsplit('\nDEPLOYCTL_INSTALL_PY',1)[0]
    namespace = {'__name__':'standalone_fixture'}
    exec(compile(generated, 'install.sh', 'exec'), namespace)
    content = b'cli'; row=entry(); row['cli_sha256']=hashlib.sha256(content).hexdigest()
    def fetch(request, limit):
        if request.full_url.endswith('/latest/download/update-index.json'): return json.dumps(catalog(row)).encode()
        assert request.full_url == 'https://github.com/art-shier/deployctl/releases/download/v1.15.1/deployctl.pyz'
        return content
    namespace['fetch']=fetch
    namespace['artifact_version']=lambda path:'1.15.1'
    state=namespace['install'](install_dir=tmp_path/'bin')
    assert state['version']=='v1.15.1'
    assert (tmp_path/'bin/ctl').read_bytes()==content


def test_refresh_seed_clock_cannot_displace_newer_full_catalog(monkeypatch):
    from deployctl import release_catalog
    old = entry(); current = entry('v1.16.0'); current['published_at']='2026-11-06T01:00:00Z'
    full = catalog(old, current); full['generated_at']='2026-11-06T01:01:00Z'
    monkeypatch.setattr(release_catalog, 'fetch', lambda *args:json.dumps(full).encode())
    first=release_catalog.published_history('art-shier/deployctl','token','2026-11-07T00:00:00Z','v1.16.0')
    later=release_catalog.published_history('art-shier/deployctl','token','2026-11-08T00:00:00Z','v1.16.0')
    assert first == later
    assert first['generated_at'] == STAMP
    assert first['releases'] == [old]


def test_publisher_bootstrap_requires_both_actual_artifacts(monkeypatch):
    from deployctl import release_catalog
    from urllib.error import HTTPError
    calls=[]
    metadata=dict(tag_name='v1.15.1',published_at=STAMP,draft=False,prerelease=False,assets=[
        dict(name='deployctl.pyz.sha256',url='https://api.github.com/repos/art-shier/deployctl/releases/assets/1'),
        dict(name='ctl-platform-v1.15.1.tar.gz.sha256',url='https://api.github.com/repos/art-shier/deployctl/releases/assets/2')])
    def fetch(request, limit):
        calls.append(request.full_url)
        if request.full_url.endswith('update-index.json'):
            raise HTTPError(request.full_url,404,'Not Found',{},None)
        if '/releases?' in request.full_url: return json.dumps([metadata]).encode()
        return ('a'*64).encode()
    monkeypatch.setattr(release_catalog,'fetch',fetch)
    assert release_catalog.published_history('art-shier/deployctl','CI-token',STAMP)['releases']==[]
    assert len(calls)==2
