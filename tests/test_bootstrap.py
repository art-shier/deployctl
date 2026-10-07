import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request
import zipapp


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def installer(self):
        from deployctl import bootstrap
        return bootstrap

    def test_local_install_creates_both_commands_and_records_ownership(self):
        installer = self.installer()
        artifact = self.root / 'deployctl.pyz'
        artifact.write_bytes(b'fake-cli')
        artifact.with_name(artifact.name + '.sha256').write_text(hashlib.sha256(b'fake-cli').hexdigest())
        with patch.object(installer, 'artifact_version', return_value='1.2.0'):
            state = installer.install(artifact=str(artifact), install_dir=self.root / 'bin')
        self.assertEqual((self.root / 'bin/deployctl').read_bytes(), b'fake-cli')
        self.assertEqual((self.root / 'bin/ctl').read_bytes(), b'fake-cli')
        self.assertEqual(state['version'], 'v1.2.0')
        self.assertTrue((self.root / 'bin/.deployctl-install.json').is_file())

    def test_bad_checksum_does_not_replace_existing_cli(self):
        installer = self.installer()
        artifact = self.root / 'deployctl.pyz'
        artifact.write_bytes(b'new-cli')
        artifact.with_name(artifact.name + '.sha256').write_text('0' * 64)
        folder = self.root / 'bin'
        folder.mkdir()
        (folder / 'deployctl').write_bytes(b'old-cli')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            installer.install(artifact=str(artifact), install_dir=folder)
        self.assertEqual((folder / 'deployctl').read_bytes(), b'old-cli')

    def test_unmanaged_command_collision_is_not_overwritten(self):
        installer = self.installer()
        artifact = self.root / 'deployctl.pyz'
        artifact.write_bytes(b'fake-cli')
        artifact.with_name(artifact.name + '.sha256').write_text(hashlib.sha256(b'fake-cli').hexdigest())
        folder = self.root / 'bin'
        folder.mkdir()
        (folder / 'ctl').write_bytes(b'other-tool')
        with patch.object(installer, 'artifact_version', return_value='1.2.0'):
            with self.assertRaisesRegex(ValueError, 'managed'):
                installer.install(artifact=str(artifact), install_dir=folder)
        self.assertEqual((folder / 'ctl').read_bytes(), b'other-tool')
        self.assertFalse((folder / 'deployctl').exists())

    def test_managed_upgrade_replaces_verified_files(self):
        installer = self.installer()
        artifact = self.root / 'deployctl.pyz'
        folder = self.root / 'bin'
        for content, version in [(b'old', '1.1.0'), (b'new', '1.2.0')]:
            artifact.write_bytes(content)
            artifact.with_name(artifact.name + '.sha256').write_text(hashlib.sha256(content).hexdigest())
            with patch.object(installer, 'artifact_version', return_value=version):
                installer.install(artifact=str(artifact), install_dir=folder)
        self.assertEqual((folder / 'deployctl').read_bytes(), b'new')
        self.assertEqual(json.loads((folder / '.deployctl-install.json').read_text())['version'], 'v1.2.0')

    def test_alias_can_be_disabled(self):
        installer = self.installer()
        artifact = self.root / 'deployctl.pyz'
        artifact.write_bytes(b'fake-cli')
        artifact.with_name(artifact.name + '.sha256').write_text(hashlib.sha256(b'fake-cli').hexdigest())
        with patch.object(installer, 'artifact_version', return_value='1.2.0'):
            installer.install(artifact=str(artifact), install_dir=self.root / 'bin', no_alias=True)
        self.assertTrue((self.root / 'bin/deployctl').exists())
        self.assertFalse((self.root / 'bin/ctl').exists())

    def test_remote_private_release_uses_asset_api_and_verifies_tag(self):
        installer = self.installer()
        content = b'fake-cli'
        digest = hashlib.sha256(content).hexdigest()
        release = {'tag_name': 'v1.2.0', 'assets': [
            {'name': 'deployctl.pyz', 'url': 'https://api.github.com/repos/art-shier/deployctl/releases/assets/1'},
            {'name': 'deployctl.pyz.sha256', 'url': 'https://api.github.com/repos/art-shier/deployctl/releases/assets/2'},
        ]}
        def responses(request, limit):
            self.assertEqual(request.get_header('Authorization'), 'Bearer read-token')
            if '/releases/tags/' in request.full_url:
                self.assertTrue(request.full_url.endswith('/v1.2.0'))
                return json.dumps(release).encode()
            self.assertEqual(request.get_header('Accept'), 'application/octet-stream')
            return digest.encode() if request.full_url.endswith('/2') else content
        with patch.object(installer, 'fetch', responses), patch.object(installer, 'artifact_version', return_value='1.2.0'):
            state = installer.install(install_dir=self.root / 'bin', version='v1.2.0', token='read-token')
        self.assertEqual(state['repository'], 'art-shier/deployctl')
        self.assertEqual((self.root / 'bin/ctl').read_bytes(), content)

    def test_redirect_strips_token_and_blocks_https_downgrade(self):
        installer = self.installer()
        request = Request('https://api.github.com/asset', headers={'Authorization': 'Bearer secret'})
        handler = installer.SafeRedirect()
        new = handler.redirect_request(request, None, 302, 'Found', {}, 'https://storage.example/file')
        self.assertIsNone(new.get_header('Authorization'))
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, 'Found', {}, 'http://storage.example/file')

    def test_remote_tag_mismatch_never_installs(self):
        installer = self.installer()
        release = {'tag_name': 'v1.1.0', 'assets': []}
        with patch.object(installer, 'fetch', return_value=json.dumps(release).encode()):
            with self.assertRaisesRegex(ValueError, 'version'):
                installer.install(install_dir=self.root / 'bin', version='v1.2.0')
        self.assertFalse((self.root / 'bin').exists())

    def test_gh_token_lookup_pins_github_host(self):
        installer = self.installer()
        def gh(command, **kwargs):
            if command == ['gh', 'auth', 'token', '--hostname', 'github.com']:
                return subprocess.CompletedProcess(command, 0, 'github-token\n', '')
            return subprocess.CompletedProcess(command, 0, 'enterprise-token\n', '')
        with patch.dict(os.environ, {'GH_HOST': 'enterprise.example'}, clear=True), patch.object(installer.subprocess, 'run', gh):
            self.assertEqual(installer.resolve_token(), 'github-token')

    def test_draft_release_installs_using_numeric_release_id(self):
        installer = self.installer()
        content = b'fake-cli'
        digest = hashlib.sha256(content).hexdigest()
        release = {'tag_name': 'v1.2.0', 'draft': True, 'assets': [
            {'name': 'deployctl.pyz', 'url': 'https://api.github.com/repos/art-shier/deployctl/releases/assets/1'},
            {'name': 'deployctl.pyz.sha256', 'url': 'https://api.github.com/repos/art-shier/deployctl/releases/assets/2'},
        ]}
        def responses(request, limit):
            if request.full_url == 'https://api.github.com/repos/art-shier/deployctl/releases/42':
                return json.dumps(release).encode()
            if request.full_url.endswith('/assets/2'):
                return digest.encode()
            if request.full_url.endswith('/assets/1'):
                return content
            raise AssertionError('Draft verification must use its numeric release ID')
        with patch.object(installer, 'fetch', responses), patch.object(installer, 'artifact_version', return_value='1.2.0'):
            state = installer.install(install_dir=self.root / 'bin', version='v1.2.0', token='read-token', release_id='42')
        self.assertEqual(state['version'], 'v1.2.0')

    @unittest.skipIf(os.name == 'nt', 'native Linux launcher is tested on Linux')
    def test_installed_launcher_uses_selected_python_with_spaces_in_path(self):
        installer = self.installer()
        selected = self.root / 'custom python'
        selected.symlink_to(sys.executable)
        source = self.root / 'source'
        source.mkdir()
        (source / '__main__.py').write_text("print('1.2.0')\n")
        artifact = self.root / 'deployctl.pyz'
        zipapp.create_archive(source, artifact, interpreter='/usr/bin/env python3')
        artifact.with_name(artifact.name + '.sha256').write_text(hashlib.sha256(artifact.read_bytes()).hexdigest())
        with patch.object(installer.sys, 'executable', str(selected)):
            installer.install(artifact=str(artifact), install_dir=self.root / 'bin')
        result = subprocess.run([str(self.root / 'bin/ctl'), '--version'], env={'PATH': '/nonexistent'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1.2.0')
