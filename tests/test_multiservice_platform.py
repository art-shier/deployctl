import copy
import hashlib
from unittest.mock import patch
import pytest
from deployctl.platform_client import PlatformClient, validate_resolution
from deployctl.platform_credentials import Credentials
from deployctl.release import build_release
from test_multiservice_contract import source, IMAGE
from test_multiservice_release import API_IMAGE


def resolution():
    return {'schema_version': 3, 'minimum_client_version': '1.20.0', 'capabilities': ['multi_service_v1'],
            'project': 'lifelog', 'environment': 'prod',
            'release': {'id': 'a'*32, 'version': 'v2.0.0', 'images': {'api': API_IMAGE, 'postgres': IMAGE},
                        'sha256': 'b'*64, 'package_path': '/api/v1/projects/lifelog/artifacts/'+'a'*32},
            'configuration': {'id': 'c'*32, 'revision': 1, 'config': {'DB_HOST': 'postgres'},
                              'secrets': {'DB_PASSWORD': 'private-sentinel'}, 'install_params': {}, 'deployment_defaults': {}}}


def test_resolution_keeps_secret_classification():
    value = resolution()
    with patch('deployctl.platform_client.__version__', '1.20.0'):
        assert validate_resolution(value, 'lifelog', 'prod') == value


@pytest.mark.parametrize('change', ['capability', 'floor', 'mixed_image', 'empty_images', 'overlap', 'bad_digest'])
def test_invalid_resolution_rejected(change):
    value = resolution()
    if change == 'capability': value['capabilities'] = []
    if change == 'floor': value['minimum_client_version'] = '1.7.0'
    if change == 'mixed_image': value['release']['image'] = API_IMAGE
    if change == 'empty_images': value['release']['images'] = {}
    if change == 'overlap': value['configuration']['config']['DB_PASSWORD'] = 'private-sentinel'
    if change == 'bad_digest': value['release']['images']['api'] = 'api:latest'
    with patch('deployctl.platform_client.__version__', '1.20.0'), pytest.raises(ValueError) as exc:
        validate_resolution(value, 'lifelog', 'prod')
    assert 'private-sentinel' not in str(exc.value)


def test_old_client_rejects_before_download():
    with patch('deployctl.platform_client.__version__', '1.19.5'), pytest.raises(ValueError):
        validate_resolution(resolution(), 'lifelog', 'prod')


def test_download_matches_every_service_digest(tmp_path):
    value = resolution()
    package = build_release(source(), value['release']['images'], 'v2.0.0', tmp_path / 'build')
    payload = package.read_bytes()
    value['release']['sha256'] = hashlib.sha256(payload).hexdigest()
    client = PlatformClient(Credentials('https://ctl.test', 'private-test-token'))
    with patch.object(client, 'request', return_value=payload):
        assert client.download_release(value, tmp_path).read_bytes() == payload
        bad = copy.deepcopy(value)
        bad['release']['images']['postgres'] = API_IMAGE
        with pytest.raises(ValueError, match='does not match'):
            client.download_release(bad, tmp_path)


def test_agent_registry_auth_covers_same_registry_services(tmp_path):
    import json
    from pathlib import Path
    from unittest.mock import Mock
    from deployctl.node_agent import RuntimeExecutor
    runtime = object.__new__(RuntimeExecutor)
    runtime.client = Mock()
    runtime.client.json.return_value = {'registry_host': 'registry.example', 'token': 'scoped-test-token'}
    value = {'id': 'd'*32, 'resolution': resolution()}
    with runtime.registry_config(value) as folder:
        config = json.loads((Path(folder) / 'config.json').read_text())
        assert config['auths']['registry.example']['registrytoken'] == 'scoped-test-token'
    assert not Path(folder).exists()
    value['resolution']['release']['images']['postgres'] = IMAGE.replace('registry.example', 'untrusted.example')
    with pytest.raises(ValueError, match='registry'):
        with runtime.registry_config(value):
            pytest.fail('credentials must not be shared with a different registry')


def test_publisher_recovers_multi_service_package_without_configuration_access(tmp_path):
    value = resolution()
    package = build_release(source(), value['release']['images'], 'v2.0.0', tmp_path / 'original', commit='e'*40)
    payload = package.read_bytes()
    record = {**value['release'], 'schema_version': 3, 'project': 'lifelog', 'status': 'published', 'commit': 'e'*40,
              'sha256': hashlib.sha256(payload).hexdigest(), 'image': '',
              'minimum_deployctl_version': '1.20.0', 'capabilities': ['multi_service_v1']}
    client = PlatformClient(Credentials('https://ctl.test', 'private-test-token'))
    with patch('deployctl.platform_client.__version__', '1.20.0'), \
            patch.object(client, 'json', return_value=record) as metadata, patch.object(client, 'request', return_value=payload):
        assert client.recover_release('lifelog', 'v2.0.0', tmp_path / 'recovered', 'e'*40) == record
    assert metadata.call_args.args == ('GET', '/api/v1/projects/lifelog/releases/v2.0.0')
    assert (tmp_path / 'recovered/lifelog-v2.0.0.tar.gz').read_bytes() == payload
