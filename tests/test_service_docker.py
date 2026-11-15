import json
import pytest
from deployctl.service_docker import ServiceDockerDriver
from test_service_plan import release


class Commands:
    def __init__(self): self.calls = []; self.docker_config = None
    def _run(self, command, environment=None, timeout=600):
        self.calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']: return 'linux/amd64'
        return ''
    def _stream_pull(self, command, environment=None): self.calls.append(command)


def driver(tmp_path):
    return ServiceDockerDriver(Commands(), tmp_path, 'lifelog-prod-test', release(), {}, {})


def test_targeted_up_never_recreates_dependencies_or_removes_orphans(tmp_path):
    d = driver(tmp_path); d.up('api')
    command = d.commands.calls[-1]
    assert command[-7:] == ['up', '-d', '--no-build', '--pull', 'never', '--no-deps', 'api']
    assert 'down' not in command and '--remove-orphans' not in command


def test_pull_checks_runtime_platform(tmp_path):
    d = driver(tmp_path); d.pull('postgres')
    assert d.commands.calls[0][:2] == ['docker', 'pull']
    assert d.commands.calls[1][:3] == ['docker', 'image', 'inspect']


def test_unowned_existing_volume_cannot_be_reused(tmp_path):
    d = driver(tmp_path)
    d.inspect = lambda kind, name: {'Name': name, 'CreatedAt': 'now', 'Labels': {}}
    with pytest.raises(ValueError, match='ownership'): d.ensure_resources()


def test_unsupported_platform_fails_before_up(tmp_path):
    d = driver(tmp_path)
    d.commands._run = lambda *args, **kwargs: 'linux/arm64'
    with pytest.raises(ValueError, match='platform'): d.pull('postgres')
