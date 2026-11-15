"""Public contract boundaries for managed service stacks."""
import copy
import pytest
from deployctl.contract import validate_deployment

IMAGE = 'registry.example/postgres@sha256:' + 'a' * 64


def source():
    return {
        'schema_version': 2, 'application': 'lifelog', 'platform': 'linux/amd64',
        'primary_endpoint': 'api.http',
        'services': {
            'api': {'lifecycle': 'application', 'build': {'context': '.', 'dockerfile': 'Dockerfile'},
                    'depends_on': {'postgres': 'healthy'},
                    'environment': {'DB_HOST': {'literal': 'postgres'}, 'DB_PASSWORD': {'secret': 'db_password'}},
                    'endpoints': {'http': {'container_port': 8080, 'host_port': 8081}},
                    'health': {'http': '/health/ready'}},
            'postgres': {'lifecycle': 'persistent', 'image': IMAGE, 'mirror': 'project',
                         'secret_files': {'pg_password': '/run/secrets/pg-password'},
                         'volumes': {'data': '/var/lib/postgresql'},
                         'health': {'exec': ['pg_isready', '-U', 'postgres']}}},
        'volumes': {'data': {'retain': True}}}


def test_valid_source_normalizes_without_mutating_input():
    data = source(); before = copy.deepcopy(data)
    result = validate_deployment(data)
    assert data == before
    assert result['services']['api']['endpoints']['http']['bind_address'] == '127.0.0.1'
    assert result['services']['postgres']['health']['startup_timeout_seconds'] == 120
    assert 'build' not in result['services']['postgres']
    assert result['volumes']['data']['retain'] is True
    assert validate_deployment(result) == result


@pytest.mark.parametrize('case', [
    'cycle', 'missing_dependency', 'mutable_image', 'both_sources', 'unknown_field',
    'volume_missing', 'volume_not_retained', 'overlapping_mounts', 'host_mount',
    'wildcard_port_collision', 'missing_primary', 'bad_env', 'path_traversal',
    'boolean_schema', 'wrong_platform', 'shell_health', 'self_dependency'])
def test_rejects_unsafe_source(case):
    data = source(); api = data['services']['api']; pg = data['services']['postgres']
    if case == 'cycle': pg['depends_on'] = {'api': 'healthy'}
    if case == 'self_dependency': api['depends_on'] = {'api': 'healthy'}
    if case == 'missing_dependency': api['depends_on'] = {'unknown': 'healthy'}
    if case == 'mutable_image': pg['image'] = 'postgres:latest'
    if case == 'both_sources': api['image'] = IMAGE
    if case == 'unknown_field': pg['privileged'] = True
    if case == 'volume_missing': pg['volumes'] = {'unknown': '/var/lib/postgresql'}
    if case == 'volume_not_retained': data['volumes']['data']['retain'] = False
    if case == 'overlapping_mounts': pg['secret_files']['pg_password'] = '/var/lib/postgresql/password'
    if case == 'host_mount': pg['volumes'] = {'/etc': '/var/lib/postgresql'}
    if case == 'wildcard_port_collision':
        pg['endpoints'] = {'sql': {'container_port': 5432, 'host_port': 8081, 'bind_address': '0.0.0.0'}}
    if case == 'missing_primary': data['primary_endpoint'] = 'api.missing'
    if case == 'bad_env': api['environment']['DB_HOST'] = {'literal': 'postgres', 'secret': 'db_password'}
    if case == 'path_traversal': api['build']['context'] = '../outside'
    if case == 'boolean_schema': data['schema_version'] = True
    if case == 'wrong_platform': data['platform'] = 'windows/amd64'
    if case == 'shell_health': pg['health']['exec'] = 'pg_isready; touch /tmp/x'
    with pytest.raises(ValueError): validate_deployment(data)


def test_dependencies_order_and_jobs_gate_api():
    from deployctl.multiservice_contract import dependency_order
    services = {'api': {'depends_on': {'verify': 'succeeded'}}, 'postgres': {}}
    jobs = {'restore': {'depends_on': {'postgres': 'healthy'}},
            'verify': {'depends_on': {'restore': 'succeeded'}}}
    assert dependency_order(services, jobs) == ['postgres', 'restore', 'verify', 'api']
    with pytest.raises(ValueError): dependency_order({'api': {'depends_on': {'api': 'healthy'}}}, {})
    with pytest.raises(ValueError): dependency_order({'api': {'depends_on': {'postgres': 'succeeded'}}, 'postgres': {}}, {})
