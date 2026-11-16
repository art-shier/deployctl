import copy
import json
import os
import pytest
from deployctl.multiservice_release import validate_multiservice_release
from test_service_plan import release, values


def full_release(version='v2.0.0'):
    return validate_multiservice_release({**release(), 'application': 'lifelog', 'version': version,
        'minimum_deployctl_version': '1.20.0', 'capabilities': ['multi_service_v1']})


def test_stack_snapshot_keeps_database_bundle_path_during_api_upgrade(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot, load_stack_snapshot
    first = create_stack_snapshot(tmp_path, full_release(), values())
    updated = full_release('v2.1.0')
    updated['deployment']['services']['api']['image'] = 'registry.example/api@sha256:'+'c'*64
    second = create_stack_snapshot(tmp_path, updated, values())
    assert first.bundles['postgres'] == second.bundles['postgres']
    assert first.bundles['api'] != second.bundles['api']
    assert load_stack_snapshot(tmp_path, first.ref, 'lifelog').release['version'] == 'v2.0.0'
    assert os.stat(first.directory / 'snapshot.json').st_mode & 0o777 == 0o600
    assert 'admin-secret' not in json.dumps(first.ref)


@pytest.mark.parametrize('file', ['snapshot.json', 'compose.yaml', 'secret'])
def test_stack_snapshot_rejects_changed_configuration_or_compose(tmp_path, file):
    from deployctl.stack_snapshot import create_stack_snapshot, load_stack_snapshot
    snapshot = create_stack_snapshot(tmp_path, full_release(), values())
    path = snapshot.bundles['postgres'] / 'secrets/pg_password' if file == 'secret' else snapshot.directory / file
    path.write_text('tampered')
    with pytest.raises(ValueError): load_stack_snapshot(tmp_path, snapshot.ref, 'lifelog')


def test_stack_snapshot_refuses_cross_application_reference(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot, load_stack_snapshot
    snapshot = create_stack_snapshot(tmp_path, full_release(), values())
    with pytest.raises(ValueError): load_stack_snapshot(tmp_path, snapshot.ref, 'other')


def test_stack_snapshot_values_are_not_shared_with_input(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot, load_stack_snapshot
    data = values()
    snapshot = create_stack_snapshot(tmp_path, full_release(), data)
    data['secrets']['pg_password'] = 'changed'
    assert load_stack_snapshot(tmp_path, snapshot.ref, 'lifelog').values['secrets']['pg_password'] == 'admin-secret'


def test_completed_jobs_do_not_require_credentials_or_compose_env_files(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot, load_stack_snapshot
    from test_stack_jobs import with_restore
    import yaml
    snapshot = create_stack_snapshot(tmp_path, with_restore(), values(), active_jobs=[])
    assert set(snapshot.bundles) == {'api', 'postgres'}
    compose = yaml.safe_load((snapshot.directory/'compose.yaml').read_text())
    assert set(compose['services']) == {'api', 'postgres'}
    assert load_stack_snapshot(tmp_path, snapshot.ref, 'lifelog').release['deployment']['jobs']['restore']


def test_recovery_generation_keeps_configuration_and_resource_identity_isolated(tmp_path):
    from deployctl.stack_snapshot import create_stack_snapshot,load_stack_snapshot
    first=create_stack_snapshot(tmp_path,full_release(),values())
    second=create_stack_snapshot(tmp_path,full_release(),values(),generation='a'*32)
    assert first.ref != second.ref and first.directory != second.directory
    assert first.bundles['postgres'] != second.bundles['postgres']
    assert load_stack_snapshot(tmp_path,second.ref,'lifelog').ref == second.ref


@pytest.mark.parametrize('generation', ['../escape', '', 'A'*32, 'a'*31, 42])
def test_generation_rejects_invalid_paths(tmp_path, generation):
    from deployctl.stack_snapshot import create_stack_snapshot
    with pytest.raises(ValueError, match='generation'):
        create_stack_snapshot(tmp_path, full_release(), values(), generation=generation)
    assert not list(tmp_path.iterdir())
