import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/build_agent_assets.py'


class AgentAssetsTests(unittest.TestCase):
    def load_builder(self):
        self.assertTrue(SCRIPT.is_file(), 'agent asset builder is missing')
        spec = importlib.util.spec_from_file_location('agent_assets', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, directory, cli_version='1.12.0'):
        root = Path(directory)
        (root / 'deployctl').mkdir(parents=True)
        # The version reader must parse source rather than import its dependencies.
        (root / 'deployctl/__init__.py').write_text("import unavailable_dependency\n__version__ = '1.12.0'\n")
        skill = root / 'skills/team-deploy'
        (skill / 'references').mkdir(parents=True)
        (skill / 'assets/templates').mkdir(parents=True)
        (skill / 'agents').mkdir()
        (skill / 'SKILL.md').write_text('---\nname: team-deploy\n---\nAgent guide\n')
        (skill / 'references/control-plane.md').write_text('Public instructions\n')
        (skill / 'assets/templates/release.yml').write_text('name: release\n')
        (skill / 'assets/install.sh').write_text('#!/bin/sh\n')
        (skill / 'assets/LICENSE').write_text('License\nPublic text\n')
        (skill / 'assets/THIRD_PARTY_NOTICES.txt').write_text('Notices\nPublic text\n')
        (skill / 'agents/openai.yaml').write_text('interface: {}\n')
        (skill / 'owner.token').write_text('private-owner-marker')
        (skill / 'assets/private.key').write_text('private-key-marker')
        (skill / 'references/private.env').write_text('private-config-marker')
        with zipfile.ZipFile(skill / 'assets/deployctl.pyz', 'w') as archive:
            archive.writestr('deployctl/__init__.py', f"__version__ = '{cli_version}'\n")
            archive.writestr('__main__.py', f"print('{cli_version}')\n")
            archive.writestr('binary.dat', b'\x00\r\n\xff')
        cli_digest = hashlib.sha256((skill / 'assets/deployctl.pyz').read_bytes()).hexdigest()
        (skill / 'assets/deployctl.pyz.sha256').write_text(f'{cli_digest}  deployctl.pyz\n')
        return root, skill

    def test_text_line_endings_and_mtimes_do_not_change_archive_or_public_bytes(self):
        builder = self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, skill = self.fixture(temporary)
            cli = (skill / 'assets/deployctl.pyz').read_bytes()
            text_files = [path for path in skill.rglob('*')
                          if path.is_file() and path.name != 'deployctl.pyz']
            original = {path: path.read_bytes().replace(b'\r\n', b'\n') for path in text_files}
            for path, data in original.items():
                path.write_bytes(data)
                os.utime(path, (1000000000, 1000000000))
            first = root / 'lf-output'
            builder.build_bundle(root, first)
            for path, data in original.items():
                path.write_bytes(data.replace(b'\n', b'\r\n'))
                os.utime(path, (1700000000, 1700000000))
            os.utime(skill / 'assets/deployctl.pyz', (1700000000, 1700000000))
            second = root / 'crlf-output'
            builder.build_bundle(root, second)
            self.assertEqual((first / 'team-deploy-skill.zip').read_bytes(),
                             (second / 'team-deploy-skill.zip').read_bytes())
            self.assertEqual(cli, (second / 'assets/deployctl.pyz').read_bytes())
            for path in first.rglob('*'):
                if path.is_file():
                    relative = path.relative_to(first)
                    self.assertEqual(path.read_bytes(), (second / relative).read_bytes(), str(relative))
            with zipfile.ZipFile(second / 'team-deploy-skill.zip') as archive:
                for name in archive.namelist():
                    relative = Path(name).relative_to('team-deploy')
                    contents = archive.read(name)
                    if relative.as_posix() != 'assets/deployctl.pyz':
                        self.assertNotIn(b'\r\n', contents, name)
                    if relative.parts[0] != 'agents':
                        self.assertEqual(contents, (second / relative).read_bytes(), name)

    def test_bundle_and_shared_archive_are_identical_and_public_only(self):
        builder = self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, skill = self.fixture(temporary)
            first, second = root / 'first', root / 'second'
            builder.build_bundle(root, first)
            builder.build_bundle(root, second)
            archive = root / 'release.zip'
            builder.build_archive(skill, archive)
            self.assertEqual(archive.read_bytes(), (first / 'team-deploy-skill.zip').read_bytes())
            self.assertEqual(archive.read_bytes(), (second / 'team-deploy-skill.zip').read_bytes())
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                self.assertIn('team-deploy/agents/openai.yaml', names)
                self.assertIn('team-deploy/assets/deployctl.pyz', names)
                self.assertFalse(any('private' in name or 'owner.token' in name for name in names))
                self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in package.infolist()))
            self.assertFalse((first / 'agents').exists())
            self.assertFalse((first / 'owner.token').exists())
            metadata = json.loads((first / 'metadata.json').read_text())
            self.assertEqual(metadata['version'], '1.12.0')
            for field, path in [('skill_sha256', first / 'team-deploy-skill.zip'),
                                ('cli_sha256', first / 'assets/deployctl.pyz')]:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(metadata[field], digest)
                self.assertEqual(path.with_name(path.name + '.sha256').read_text(), f'{digest}  {path.name}\n')

    def test_stale_bundled_cli_is_rejected_before_output(self):
        builder = self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.fixture(temporary, cli_version='1.11.0')
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'version'):
                builder.build_bundle(root, output)
            self.assertFalse(output.exists())

    def test_missing_or_stale_cli_checksum_is_rejected_before_archive_or_bundle(self):
        builder = self.load_builder()
        for invalid in ('missing', 'stale'):
            for operation in ('archive', 'bundle'):
                with self.subTest(invalid=invalid, operation=operation), tempfile.TemporaryDirectory() as temporary:
                    root, skill = self.fixture(temporary)
                    checksum = skill / 'assets/deployctl.pyz.sha256'
                    if invalid == 'missing':
                        checksum.unlink()
                    else:
                        checksum.write_text('0' * 64 + '  deployctl.pyz\n')
                    output = root / ('skill.zip' if operation == 'archive' else 'bundle')
                    with self.assertRaisesRegex(ValueError, 'checksum'):
                        if operation == 'archive':
                            builder.build_archive(skill, output)
                        else:
                            builder.build_bundle(root, output)
                    self.assertFalse(output.exists())

    def test_cli_runs_in_isolated_stdlib_python(self):
        self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.fixture(temporary)
            result = subprocess.run([sys.executable, '-I', '-S', str(SCRIPT), '--root', str(root),
                                     '--output', str(root / 'output')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_version_is_literal_and_cannot_execute_code(self):
        builder = self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.fixture(temporary)
            (root / 'deployctl/__init__.py').write_text("__version__ = str(__import__('os').getpid())\n")
            with self.assertRaisesRegex(ValueError, 'version'):
                builder.build_bundle(root, root / 'output')

    def test_curated_symlink_cannot_enter_archive(self):
        builder = self.load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            root, skill = self.fixture(temporary)
            target = root / 'private.md'
            target.write_text('private-file-marker')
            try:
                (skill / 'references/linked.md').symlink_to(target)
            except OSError:
                self.skipTest('symlinks unavailable on this host')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                builder.build_archive(skill, root / 'skill.zip')


if __name__ == '__main__':
    unittest.main()
