import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request


class DownloadTests(unittest.TestCase):
    def test_local_release_requires_matching_checksum(self):
        from deployctl.download import acquire_release
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.tar.gz'
            path.write_bytes(b'archive')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                acquire_release(str(path), Path(tmp) / 'cache')
            path.with_name(path.name + '.sha256').write_text('0' * 64 + '  app.tar.gz\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                acquire_release(str(path), Path(tmp) / 'cache')
            path.with_name(path.name + '.sha256').write_text(hashlib.sha256(b'archive').hexdigest())
            result = acquire_release(str(path), Path(tmp) / 'cache')
            self.assertEqual(result.read_bytes(), b'archive')

    def test_explicit_checksum_is_enforced(self):
        from deployctl.download import acquire_release
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.tar.gz'
            path.write_bytes(b'archive')
            digest = hashlib.sha256(b'archive').hexdigest()
            self.assertEqual(acquire_release(str(path), Path(tmp) / 'cache', digest).read_bytes(), b'archive')

    def test_insecure_url_and_embedded_credentials_are_rejected(self):
        from deployctl.download import validate_url
        for url in ('http://example.com/file', 'https://token@example.com/file', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_redirect_drops_authorization_and_rejects_http_downgrade(self):
        from deployctl.download import SafeRedirect
        request = Request('https://api.github.com/asset', headers={'Authorization': 'Bearer secret'})
        handler = SafeRedirect()
        redirected = handler.redirect_request(request, None, 302, 'Found', {}, 'https://storage.example/file')
        self.assertIsNone(redirected.get_header('Authorization'))
        same = handler.redirect_request(request, None, 302, 'Found', {}, 'https://api.github.com/next')
        self.assertEqual(same.get_header('Authorization'), 'Bearer secret')
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, 'Found', {}, 'http://storage.example/file')

    def test_private_asset_resolves_through_api_and_does_not_auth_arbitrary_hosts(self):
        from deployctl.download import resolve_asset
        def github_response(request, limit):
            self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
            self.assertEqual(request.full_url, 'https://api.github.com/repos/acme/app/releases/tags/v1.0.0')
            return b'{"assets":[{"name":"app.tar.gz","url":"https://api.github.com/repos/acme/app/releases/assets/123"}]}'
        with patch.dict('os.environ', {'GH_TOKEN': 'secret'}), patch('deployctl.download.fetch', github_response):
            request = resolve_asset('https://github.com/acme/app/releases/download/v1.0.0/app.tar.gz')
            self.assertEqual(request.full_url, 'https://api.github.com/repos/acme/app/releases/assets/123')
            self.assertEqual(request.get_header('Accept'), 'application/octet-stream')
            arbitrary = resolve_asset('https://storage.example/app.tar.gz')
            self.assertIsNone(arbitrary.get_header('Authorization'))

    def test_url_download_checks_digest_before_returning(self):
        from deployctl.download import acquire_release
        digest = hashlib.sha256(b'archive').hexdigest()
        def response(request, limit):
            if request.full_url.endswith('.sha256'):
                return digest.encode() + b'  app.tar.gz\n'
            return b'archive'
        with tempfile.TemporaryDirectory() as tmp, patch('deployctl.download.fetch', response):
            path = acquire_release('https://storage.example/app.tar.gz', Path(tmp))
            self.assertEqual(path.read_bytes(), b'archive')
