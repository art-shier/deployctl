from pathlib import Path
import pytest
import yaml
from deployctl.cli import main
from deployctl.release import unpack_release
from test_multiservice_contract import source, IMAGE
from test_multiservice_release import API_IMAGE
from test_release import CONFIG


def invoke(tmp_path, capsys, config, images):
    path = tmp_path / 'deployment.yaml'
    path.write_text(yaml.safe_dump(config))
    args = ['package', '--config', str(path), '--version', 'v2.0.0', '--output', str(tmp_path / 'dist')]
    for item in images:
        args.extend(['--image', item])
    result = main(args)
    return result, capsys.readouterr()


def test_keyed_images_package_all_services(tmp_path, capsys):
    result, output = invoke(tmp_path, capsys, source(), [f'api={API_IMAGE}', f'postgres={IMAGE}'])
    assert result == 0, output.err
    release = unpack_release(Path(output.out.strip()), tmp_path / 'unpacked')
    assert release['deployment']['services']['api']['image'] == API_IMAGE
    assert release['deployment']['services']['postgres']['image'] == IMAGE


@pytest.mark.parametrize('images', [
    [f'api={API_IMAGE}', f'api={API_IMAGE}', f'postgres={IMAGE}'],
    [API_IMAGE, f'postgres={IMAGE}'],
    [f'api={API_IMAGE}'],
])
def test_bad_mapping_does_not_write_archive(tmp_path, capsys, images):
    result, _ = invoke(tmp_path, capsys, source(), images)
    assert result == 1
    assert not list(tmp_path.rglob('*.tar.gz'))


def test_legacy_package_command_is_unchanged(tmp_path, capsys):
    result, output = invoke(tmp_path, capsys, CONFIG, [API_IMAGE])
    assert result == 0, output.err
    assert unpack_release(Path(output.out.strip()), tmp_path / 'unpacked')['image'] == API_IMAGE


def test_legacy_repeated_image_is_rejected(tmp_path, capsys):
    result, _ = invoke(tmp_path, capsys, CONFIG, [API_IMAGE, IMAGE])
    assert result == 1
    assert not list(tmp_path.rglob('*.tar.gz'))
