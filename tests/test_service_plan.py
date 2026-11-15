import copy
import pytest
from deployctl.service_plan import plan_services, service_fingerprint
from deployctl.multiservice_release import resolve_services
from test_multiservice_contract import source, IMAGE
from test_multiservice_release import API_IMAGE


def release():
    return {'schema_version': 3, 'deployment': resolve_services(source(), {'api': API_IMAGE, 'postgres': IMAGE})}


def values():
    return {'config': {}, 'secrets': {'db_password': 'business-secret', 'pg_password': 'admin-secret'}}


def inventory(r, v):
    return {'multi_service': True, 'services': {name: {'fingerprint': service_fingerprint(node, v), 'running': True,
        'healthy': True, 'lifecycle': node['lifecycle']} for name, node in r['deployment']['services'].items()},
        'volumes': {'data': {'identity': 'volume-1', 'present': True}}, 'volume_users': {}}


def test_api_only_upgrade_preserves_database_configuration_identity():
    r = release(); v = values(); state = inventory(r, v)
    r['deployment']['services']['api']['image'] = 'registry.example/api@sha256:' + 'c' * 64
    plan = plan_services(r, state, v)
    assert plan['services'] == {'api': 'update', 'postgres': 'unchanged'}
    assert plan['applicable'] is True
    v['secrets']['db_password'] = 'changed'
    assert service_fingerprint(r['deployment']['services']['postgres'], v) == state['services']['postgres']['fingerprint']
    assert 'business-secret' not in str(plan)


def test_persistent_secret_change_requires_maintenance_before_any_actions():
    r = release(); v = values(); state = inventory(r, v)
    v['secrets']['pg_password'] = 'new-admin-secret'
    plan = plan_services(r, state, v)
    assert plan['services']['postgres'] == 'maintenance-required'
    assert plan['applicable'] is False


def test_missing_existing_volume_is_recovery_not_empty_creation():
    r = release(); v = values(); state = inventory(r, v)
    state['volumes']['data']['present'] = False
    plan = plan_services(r, state, v)
    assert plan['volumes']['data'] == 'missing'
    assert not plan['applicable']


def test_first_install_creates_services_and_volume():
    plan = plan_services(release(), {}, values())
    assert plan['services'] == {'api': 'create', 'postgres': 'create'}
    assert plan['volumes'] == {'data': 'create'}
    assert plan['order'] == ['postgres', 'api']


def test_omitted_service_cannot_be_deleted_implicitly():
    r = release(); v = values(); state = inventory(r, v)
    state['services']['retired'] = {'fingerprint': 'old', 'lifecycle': 'application'}
    plan = plan_services(r, state, v)
    assert plan['services']['retired'] == 'removal-required'
    assert not plan['applicable']


def test_missing_configuration_rejected_without_leaking_values():
    with pytest.raises(ValueError, match='pg_password'):
        plan_services(release(), {}, {'config': {}, 'secrets': {'db_password': 'hidden'}})


def test_cross_schema_rollback_rejected_with_persistent_resource_history():
    with pytest.raises(ValueError, match='legacy'):
        plan_services({'schema_version': 1}, {'multi_service': True}, values())
