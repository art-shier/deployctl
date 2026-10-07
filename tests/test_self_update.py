import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipapp


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def artifact(self, version):
        source = self.root / ('source-' + version)
        source.mkdir()
        (source / '__main__.py').write_text(f"print('{version}')\n", encoding='utf-8')
        artifact = self.root / (version + '.pyz')
        zipapp.create_archive(source, artifact, interpreter='/usr/bin/env python3')
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + '.sha256').write_text(digest)
        return artifact, digest

    def installed(self, no_alias=False, primary_name='deployctl'):
        from deployctl import bootstrap
        artifact, _ = self.artifact('1.2.0')
        directory = self.root / 'custom-bin'
        bootstrap.install(artifact, directory, repository='example/private-platform',
                          no_alias=no_alias, primary_name=primary_name)
        return directory

    def version(self, executable):
        result = subprocess.run([sys.executable, '-I', '-S', str(executable), '--version'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def update(self, executable, expected='latest'):
        from deployctl.self_update import update_tool
        from deployctl import bootstrap
        artifact, digest = self.artifact('1.3.0')
        def release(repository, version, token, release_id=None):
            self.assertEqual(repository, 'example/private-platform')
            self.assertEqual(version, expected)
            return artifact.read_bytes(), digest, 'v1.3.0'
        with patch.object(bootstrap, 'github_release', release), patch.object(bootstrap, 'resolve_token', return_value='test-token'):
            return update_tool(executable=executable, version=expected)

    def test_invoking_ctl_updates_both_managed_commands_in_original_directory(self):
        directory = self.installed()
        state = self.update(directory / 'ctl')
        self.assertEqual(state['directory'], str(directory.resolve()))
        self.assertEqual(state['version'], 'v1.3.0')
        self.assertEqual(self.version(directory / 'ctl'), '1.3.0')
        self.assertEqual(self.version(directory / 'deployctl'), '1.3.0')

    def test_install_without_alias_remains_without_alias(self):
        directory = self.installed(no_alias=True)
        self.update(directory / 'deployctl', expected='v1.3.0')
        self.assertEqual(self.version(directory / 'deployctl'), '1.3.0')
        self.assertFalse((directory / 'ctl').exists())

    def test_custom_primary_command_is_preserved_when_invoking_alias(self):
        directory = self.installed(primary_name='teamctl')
        self.update(directory / 'ctl')
        self.assertEqual(self.version(directory / 'teamctl'), '1.3.0')
        self.assertEqual(self.version(directory / 'ctl'), '1.3.0')
        self.assertFalse((directory / 'deployctl').exists())

    def test_legacy_installer_metadata_is_supported(self):
        directory = self.installed()
        marker = directory / '.deployctl-install.json'
        metadata = json.loads(marker.read_text())
        metadata.pop('primary_name', None)
        metadata.pop('commands', None)
        marker.write_text(json.dumps(metadata))
        self.update(directory / 'ctl')
        self.assertEqual(self.version(directory / 'deployctl'), '1.3.0')
        self.assertEqual(self.version(directory / 'ctl'), '1.3.0')

    def test_unmanaged_invocation_is_rejected_before_download(self):
        from deployctl.self_update import update_tool
        from deployctl import bootstrap
        artifact, _ = self.artifact('1.2.0')
        with patch.object(bootstrap, 'github_release', side_effect=AssertionError('must not download')):
            with self.assertRaisesRegex(ValueError, 'installer'):
                update_tool(executable=artifact)

    def test_modified_invoking_command_is_rejected_before_download(self):
        from deployctl.self_update import update_tool
        from deployctl import bootstrap
        directory = self.installed()
        (directory / 'ctl').write_bytes(b'unrelated executable')
        with patch.object(bootstrap, 'github_release', side_effect=AssertionError('must not download')):
            with self.assertRaisesRegex(ValueError, 'modified|managed'):
                update_tool(executable=directory / 'ctl')

    def test_alias_collision_preserves_original_tool_and_metadata(self):
        directory = self.installed()
        marker = directory / '.deployctl-install.json'
        previous = marker.read_bytes()
        (directory / 'ctl').write_bytes(b'unrelated executable')
        with self.assertRaisesRegex(ValueError, 'managed'):
            self.update(directory / 'deployctl')
        self.assertEqual(self.version(directory / 'deployctl'), '1.2.0')
        self.assertEqual((directory / 'ctl').read_bytes(), b'unrelated executable')
        self.assertEqual(marker.read_bytes(), previous)

    def test_bad_download_checksum_preserves_existing_install(self):
        from deployctl.self_update import update_tool
        from deployctl import bootstrap
        directory = self.installed()
        previous = (directory / '.deployctl-install.json').read_bytes()
        with patch.object(bootstrap, 'github_release', return_value=(b'bad', '0' * 64, 'v1.3.0')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                update_tool(executable=directory / 'ctl')
        self.assertEqual(self.version(directory / 'ctl'), '1.2.0')
        self.assertEqual((directory / '.deployctl-install.json').read_bytes(), previous)

    def test_partial_command_write_failure_restores_both_commands_and_metadata(self):
        from deployctl import bootstrap
        directory = self.installed()
        previous = (directory / '.deployctl-install.json').read_bytes()
        atomic_write = bootstrap.atomic_write
        def fail_alias(path, content, mode):
            if path.name == 'ctl':
                raise OSError('simulated write failure')
            return atomic_write(path, content, mode)
        with patch.object(bootstrap, 'atomic_write', fail_alias):
            with self.assertRaisesRegex(OSError, 'write failure'):
                self.update(directory / 'deployctl')
        self.assertEqual(self.version(directory / 'deployctl'), '1.2.0')
        self.assertEqual(self.version(directory / 'ctl'), '1.2.0')
        self.assertEqual((directory / '.deployctl-install.json').read_bytes(), previous)

    def test_verification_timeout_reports_failure_and_preserves_install(self):
        from deployctl import bootstrap
        directory = self.installed()
        with patch.object(bootstrap, 'artifact_version', side_effect=subprocess.TimeoutExpired('python', 30)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                self.update(directory / 'ctl')
        self.assertEqual(self.version(directory / 'ctl'), '1.2.0')

    def test_self_update_cli_has_help_and_rejects_unmanaged_source_invocation(self):
        root = Path(__file__).resolve().parents[1]
        help_result = subprocess.run([sys.executable, '-m', 'deployctl', 'self-update', '--help'],
                                     cwd=root, capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('--version', help_result.stdout)
        result = subprocess.run([sys.executable, '-m', 'deployctl', 'self-update'],
                                cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('installer', result.stderr)
