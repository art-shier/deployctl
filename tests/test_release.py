import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/acme/project-a@sha256:' + 'a' * 64
CONFIG = {
    'schema_version': 1, 'application': 'project-a',
    'build': {'dockerfile': 'Dockerfile', 'context': '.'},
    'container': {'port': 8080},
    'health': {'readiness_path': '/health/ready', 'startup_timeout_seconds': 5},
    'resources': {'memory_limit': '512m'},
    'required_config': ['DATABASE_URL'],
}


def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'deployctl', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


class ReleaseTests(unittest.TestCase):
    def test_archive_does_not_depend_on_clock_or_output_directory(self):
        from unittest.mock import patch
        from deployctl.release import build_release
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('time.time',return_value=1000000000): first=build_release(CONFIG,IMAGE,'v1.0.0',root/'a').read_bytes()
            with patch('time.time',return_value=2000000000): second=build_release(CONFIG,IMAGE,'v1.0.0',root/'b').read_bytes()
            self.assertEqual(first,second)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'deployment.yaml'
        self.config.write_text(yaml.safe_dump(CONFIG), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def package(self, image=IMAGE, version='v1.0.0'):
        return run_cli('package', '--config', self.config, '--image', image,
                       '--version', version, '--output', self.root)

    def test_package_records_digest_and_generates_external_checksum(self):
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.root / 'project-a-v1.0.0.tar.gz'
        with tarfile.open(path) as tar:
            self.assertEqual(set(tar.getnames()),
                             {'release.yaml', 'compose.yaml', '.env.example', 'README.md'})
            release = yaml.safe_load(tar.extractfile('release.yaml'))
            self.assertEqual(release['image'], IMAGE)
            self.assertEqual(release['version'], 'v1.0.0')
            compose = yaml.safe_load(tar.extractfile('compose.yaml'))
            app = compose['services']['app']
            self.assertEqual(app['image'], IMAGE)
            self.assertEqual(app['env_file'][0]['format'], 'raw')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(path.with_name(path.name + '.sha256').read_text().split()[0], actual)

    def test_rejects_unknown_deployment_fields(self):
        data = dict(CONFIG, privileged=True)
        self.config.write_text(yaml.safe_dump(data), encoding='utf-8')
        result = run_cli('validate', self.config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('privileged', result.stderr)

    def test_rejects_mutable_image_tag(self):
        result = self.package(image='ghcr.io/acme/project-a:latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('digest', result.stderr)

    def test_rejects_application_path_traversal(self):
        data = dict(CONFIG, application='../other')
        self.config.write_text(yaml.safe_dump(data), encoding='utf-8')
        result = run_cli('validate', self.config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('application', result.stderr)

    def test_rejects_health_url_and_zero_timeout(self):
        for health in [{'readiness_path': 'https://evil.test/'},
                       {'readiness_path': '/ready', 'startup_timeout_seconds': 0}]:
            with self.subTest(health=health):
                data = dict(CONFIG, health=health)
                self.config.write_text(yaml.safe_dump(data), encoding='utf-8')
                result = run_cli('validate', self.config)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('health', result.stderr)

    def test_unpack_rejects_traversal_symlink_and_duplicate(self):
        self.assertEqual(self.package().returncode, 0)
        from deployctl.release import unpack_release
        original = self.root / 'project-a-v1.0.0.tar.gz'
        for kind in ('traversal', 'symlink', 'duplicate'):
            attack = self.root / (kind + '.tar.gz')
            with tarfile.open(original) as src, tarfile.open(attack, 'w:gz') as dst:
                for member in src:
                    dst.addfile(member, src.extractfile(member))
                extra = tarfile.TarInfo('../escape' if kind == 'traversal' else 'release.yaml')
                if kind == 'symlink':
                    extra.type = tarfile.SYMTYPE
                    extra.linkname = '/etc/passwd'
                    dst.addfile(extra)
                else:
                    extra.size = 1
                    dst.addfile(extra, io.BytesIO(b'x'))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                unpack_release(attack, self.root / kind)
        self.assertFalse((self.root / 'escape').exists())

    def test_unpack_rejects_modified_compose(self):
        self.assertEqual(self.package().returncode, 0)
        from deployctl.release import unpack_release
        original = self.root / 'project-a-v1.0.0.tar.gz'
        attack = self.root / 'modified.tar.gz'
        with tarfile.open(original) as src, tarfile.open(attack, 'w:gz') as dst:
            for member in src:
                content = src.extractfile(member).read()
                if member.name == 'compose.yaml':
                    obj = yaml.safe_load(content)
                    obj['services']['app']['privileged'] = True
                    content = yaml.safe_dump(obj).encode()
                member.size = len(content)
                dst.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, 'compose'):
            unpack_release(attack, self.root / 'out')

    def test_compressed_metadata_cannot_bypass_expansion_budget(self):
        self.assertEqual(self.package().returncode, 0)
        from deployctl.release import unpack_release
        attack = self.root / 'metadata.tar.gz'
        original = self.root / 'project-a-v1.0.0.tar.gz'
        with tarfile.open(attack, 'w:gz') as dst, tarfile.open(original) as src:
            metadata = tarfile.TarInfo('pax-metadata')
            metadata.type = tarfile.XHDTYPE
            metadata.size = 6 * 1024 * 1024
            dst.addfile(metadata, io.BytesIO(b'\0' * metadata.size))
            for member in src:
                dst.addfile(member, src.extractfile(member))
        self.assertLess(attack.stat().st_size, 100000)
        with self.assertRaisesRegex(ValueError, 'expanded'):
            unpack_release(attack, self.root / 'expanded-out')


if __name__ == '__main__':
    unittest.main()
