import copy
import tarfile
import pytest
from deployctl.release import build_release, unpack_release, render_compose
from deployctl.contract import validate_release
from test_multiservice_contract import source, IMAGE

API_IMAGE = 'registry.example/api@sha256:' + 'b' * 64


def test_package_multiservice_roundtrip_is_deterministic(tmp_path):
    images = {'api': API_IMAGE, 'postgres': IMAGE}
    archive = build_release(source(), images, 'v2.0.0', tmp_path / 'a')
    other = build_release(source(), images, 'v2.0.0', tmp_path / 'b')
    assert archive.read_bytes() == other.read_bytes()
    release = unpack_release(archive, tmp_path / 'unpacked')
    assert release['schema_version'] == 3
    assert release['capabilities'] == ['multi_service_v1']
    assert 'image' not in release
    assert release['deployment']['services']['api']['image'] == API_IMAGE
    assert 'build' not in release['deployment']['services']['api']
    compose = render_compose(release)
    assert set(compose['services']) == {'api', 'postgres'}
    assert compose['volumes']['data']['external'] is True
    assert compose['services']['postgres']['volumes'][0]['target'] == '/var/lib/postgresql'
    assert 'ports' not in compose['services']['postgres']
    assert 'privileged' not in compose['services']['postgres']
    assert 'depends_on' not in compose['services']['api']  # ctl enforces DAG itself.
    assert compose['services']['postgres']['labels'].get('io.team-deploy.version') is None
    bad = copy.deepcopy(release)
    bad['deployment']['services']['api']['build'] = {'context': '.', 'dockerfile': 'Dockerfile'}
    with pytest.raises(ValueError): validate_release(bad)


@pytest.mark.parametrize('images', [{}, {'api': API_IMAGE}, {'api': API_IMAGE, 'postgres': IMAGE, 'extra': IMAGE},
                                     {'api': 'api:latest', 'postgres': IMAGE}])
def test_rejects_unresolved_images(tmp_path, images):
    with pytest.raises(ValueError): build_release(source(), images, 'v2.0.0', tmp_path)


def test_copied_external_image_must_preserve_digest(tmp_path):
    images = {'api': API_IMAGE, 'postgres': 'registry.example/mirror@sha256:' + 'c' * 64}
    with pytest.raises(ValueError, match='digest'): build_release(source(), images, 'v2.0.0', tmp_path)


def test_json_schemas_accept_new_source_and_release(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    root = Path(__file__).resolve().parents[1]
    ds = json.loads((root / 'schemas/deployment.schema.json').read_text())
    rs = json.loads((root / 'schemas/release.schema.json').read_text())
    registry = Registry().with_resource('deployment.schema.json', Resource.from_contents(ds))
    Draft202012Validator(ds).validate(source())
    archive = build_release(source(), {'api': API_IMAGE, 'postgres': IMAGE}, 'v2.0.0', tmp_path)
    release = unpack_release(archive, tmp_path / 'unpacked')
    Draft202012Validator(rs, registry=registry).validate(release)
    bad = copy.deepcopy(release); bad['capabilities'] = []
    assert list(Draft202012Validator(rs, registry=registry).iter_errors(bad))


def test_compose_does_not_expand_application_command_dollars(tmp_path):
    data = source()
    data['services']['api']['command'] = ['sh', '-c', 'echo "$DB_HOST"']
    archive = build_release(data, {'api': API_IMAGE, 'postgres': IMAGE}, 'v2.0.0', tmp_path)
    release = unpack_release(archive, tmp_path / 'unpacked')
    assert render_compose(release)['services']['api']['command'][-1] == 'echo "$$DB_HOST"'


def test_multiple_endpoint_order_survives_canonical_yaml(tmp_path):
    data = source()
    data['services']['api']['health'] = {'exec': ['check']}
    data['services']['api']['endpoints'] = {
        'z-admin': {'container_port': 9000, 'host_port': 9000},
        'a-public': {'container_port': 8080, 'host_port': 8081},
    }
    data['primary_endpoint'] = 'api.a-public'
    archive = build_release(data, {'api': API_IMAGE, 'postgres': IMAGE}, 'v2.0.0', tmp_path)
    release = unpack_release(archive, tmp_path / 'unpacked')
    assert render_compose(release)['services']['api']['ports'] == ['127.0.0.1:8081:8080', '127.0.0.1:9000:9000']


@pytest.mark.parametrize('version', ['v2.0.0', 'v2.0.1'])
def test_shared_go_fixtures_are_current_python_packages(tmp_path, version):
    from pathlib import Path
    import yaml
    fixture = Path(__file__).resolve().parents[1] / 'control-server/internal/artifacts/testdata/multiservice'
    original = fixture / f'lifelog-{version}.tar.gz'
    manifest = unpack_release(original, tmp_path / 'unpacked')
    nodes = {**manifest['deployment']['services'], **manifest['deployment']['jobs']}
    config = yaml.safe_load((fixture / f'{version}.source.yaml').read_text())
    built = build_release(config, {key: node['image'] for key, node in nodes.items()}, version, tmp_path / 'build')
    assert built.read_bytes() == original.read_bytes()
