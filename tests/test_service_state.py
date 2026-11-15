import json
import os
import pytest
from deployctl.service_state import prepare_service_bundle, read_inventory, write_inventory
from deployctl.multiservice_release import resolve_services
from test_multiservice_contract import source, IMAGE
from test_multiservice_release import API_IMAGE
from test_service_plan import values


def postgres(): return resolve_services(source(), {'api': API_IMAGE, 'postgres': IMAGE})['services']['postgres']


def test_bundles_reuse_paths_and_keep_secrets_private(tmp_path):
    root = tmp_path / 'state'; v = values()
    one = prepare_service_bundle(root, 'postgres', postgres(), v)
    v['secrets']['db_password'] = 'unrelated-change'
    assert prepare_service_bundle(root, 'postgres', postgres(), v) == one
    assert (one / 'secrets' / 'pg_password').read_text() == 'admin-secret'
    assert os.stat(one).st_mode & 0o777 == 0o700
    assert os.stat(one / 'secrets' / 'pg_password').st_mode & 0o777 == 0o600
    assert 'business-secret' not in (one / 'environment.env').read_text()
    (one / 'secrets' / 'pg_password').write_text('tampered')
    with pytest.raises(ValueError, match='integrity'): prepare_service_bundle(root, 'postgres', postgres(), v)


def test_bundle_refuses_symlink_source(tmp_path):
    target = tmp_path / 'real'; target.mkdir()
    root = tmp_path / 'link'; root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError): prepare_service_bundle(root, 'postgres', postgres(), values())


def test_inventory_roundtrip_keeps_pending_and_ownership(tmp_path):
    path = tmp_path / 'inventory.json'
    data = {'schema_version': 1, 'multi_service': True, 'services': {}, 'volumes': {'data': {'identity': 'abc'}},
            'transaction': {'phase': 'restore-unknown', 'operation': 'abc'}}
    write_inventory(path, data)
    assert read_inventory(path) == data
    assert os.stat(path).st_mode & 0o777 == 0o600
    path.write_text('not json')
    with pytest.raises(ValueError): read_inventory(path)


def test_raw_environment_preserves_dollars_and_rejects_multiline(tmp_path):
    spec = postgres(); spec['environment'] = {'VALUE': {'literal': '$UNCHANGED "quoted"'}}
    path = prepare_service_bundle(tmp_path, 'postgres', spec, values())
    assert (path / 'environment.env').read_text() == 'VALUE=$UNCHANGED "quoted"\n'
    spec['environment']['VALUE'] = {'secret': 'pg_password'}
    v = values(); v['secrets']['pg_password'] = 'one\nOTHER=injected'
    with pytest.raises(ValueError): prepare_service_bundle(tmp_path, 'postgres', spec, v)
