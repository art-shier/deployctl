import io
import urllib.error
import pytest
from scripts.register_release import register, NoRedirect


class Opener:
    def __init__(self, codes):
        self.codes = iter(codes)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        code = next(self.codes)
        if code != 201:
            raise urllib.error.HTTPError(request.full_url, code, 'failure', {}, None)
        result = io.BytesIO(b'{}')
        result.status = code
        return result


def test_retry_is_idempotent_and_credential_only_sent_to_center():
    opener = Opener([503, 429, 201])
    sleeps = []
    register('https://center.example', 'dedicated', {'version': 'v1.17.0'}, opener, sleeps.append)
    assert sleeps == [1, 2]
    assert len(opener.requests) == 3
    assert all(r.full_url == 'https://center.example/api/v1/system-updates/registrations' for r in opener.requests)
    assert all(r.get_header('Authorization') == 'Bearer dedicated' for r in opener.requests)


@pytest.mark.parametrize('code', [301, 302, 307, 401, 403, 409])
def test_no_retry_on_redirect_auth_or_conflict(code):
    opener = Opener([code])
    with pytest.raises(RuntimeError, match=str(code)):
        register('https://center.example', 'secret', {}, opener)
    assert len(opener.requests) == 1
    assert NoRedirect().redirect_request(None, None, code, '', {}, 'https://other.example') is None


@pytest.mark.parametrize('url', ['http://center.example', 'https://user:secret@center.example', 'https://center.example?token=a'])
def test_reject_unsafe_center(url):
    with pytest.raises(ValueError):
        register(url, 'secret', {})


def test_published_metadata_uses_verified_catalog_time(monkeypatch):
    import hashlib
    import json
    import urllib.request
    from scripts.register_release import metadata
    item = dict(version='v1.17.0', published_at='2026-11-01T00:00:00Z', cli_sha256='a'*64, server_sha256='b'*64)
    raw = json.dumps({'releases': [item]}).encode()
    def fetch(request, timeout):
        assert request.get_header('Authorization') is None
        if 'api.github.com' in request.full_url:
            return io.BytesIO(json.dumps(dict(tag_name='v1.17.0', published_at='2026-11-02T00:00:00Z', draft=False, prerelease=False)).encode())
        if request.full_url.endswith('.sha256'):
            return io.BytesIO((hashlib.sha256(raw).hexdigest()+'  update-index.json').encode())
        return io.BytesIO(raw)
    monkeypatch.setattr(urllib.request, 'urlopen', fetch)
    payload = metadata('art-shier/deployctl', 'v1.17.0')
    assert payload['published_at'] == item['published_at']
    assert payload['cli_sha256'] == item['cli_sha256']
    assert payload['server_url'].endswith('/v1.17.0/ctl-platform-v1.17.0.tar.gz')
