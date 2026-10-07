import copy
import hashlib
import inspect
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

import yaml

from deployctl.contract import validate_deployment, validate_release
from deployctl.release import build_release, unpack_release
from test_release import CONFIG, IMAGE


class HookReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'source'
        self.project.mkdir()
        self.script = self.project / 'deploy/hooks/pre-install.sh'
        self.script.parent.mkdir(parents=True)
        self.payload = '#!/bin/bash\nprintf "部署准备\\n"\n'.encode('utf-8')
        self.script.write_bytes(self.payload)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        return dict(copy.deepcopy(CONFIG), hooks={'pre_install': {'script': 'deploy/hooks/pre-install.sh'}})

    def package(self, config=None, suffix='out'):
        self.assertIn('project_root', inspect.signature(build_release).parameters, 'hook packaging is not implemented')
        return build_release(config or self.config(), IMAGE, 'v1.0.0', self.root / suffix,
                             project_root=self.project)

    def test_no_hooks_retains_four_member_v1(self):
        for i, config in enumerate((CONFIG, dict(CONFIG, hooks={}))):
            with self.subTest(config=config):
                package = build_release(config, IMAGE, 'v1.0.0', self.root / str(i))
                with tarfile.open(package) as archive:
                    self.assertEqual(set(archive.getnames()), {'release.yaml', 'compose.yaml', '.env.example', 'README.md'})
                release = unpack_release(package, self.root / ('unpack' + str(i)))
                self.assertEqual(release['schema_version'], 1)
                self.assertEqual(release['minimum_deployctl_version'], '1.0.0')

    def test_hook_package_has_v2_manifest_and_original_bytes(self):
        config = self.config()
        before = copy.deepcopy(config)
        package = self.package(config)
        release = unpack_release(package, self.root / 'unpacked')
        self.assertEqual(release['schema_version'], 2)
        self.assertEqual(release['minimum_deployctl_version'], '1.5.0')
        self.assertEqual(release['hooks']['pre_install'], {
            'path': 'hooks/pre-install.sh', 'sha256': hashlib.sha256(self.payload).hexdigest(),
            'timeout_seconds': 300,
        })
        self.assertNotIn('hooks', release['deployment'])
        self.assertEqual((self.root / 'unpacked/hooks/pre-install.sh').read_bytes(), self.payload)
        self.assertEqual(config, before)

    def test_both_hooks_and_timeout_boundaries(self):
        for i, timeout in enumerate((1, 300, 3600)):
            config = self.config()
            config['hooks']['pre_install']['timeout_seconds'] = timeout
            config['hooks']['post_install'] = {'script': 'deploy/hooks/pre-install.sh', 'timeout_seconds': timeout}
            package = self.package(config, str(i))
            release = unpack_release(package, self.root / ('unpacked' + str(i)))
            self.assertEqual(set(release['hooks']), {'pre_install', 'post_install'})
            self.assertEqual(release['hooks']['post_install']['timeout_seconds'], timeout)
        for timeout in (0, 3601, True, '300'):
            config = self.config()
            config['hooks']['pre_install']['timeout_seconds'] = timeout
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, 'timeout'):
                validate_deployment(config)

    def test_source_script_must_be_normal_bounded_project_file(self):
        self.script.write_bytes(b'#' * (256 * 1024))
        self.package(suffix='boundary')
        self.script.write_bytes(b'#' * (256 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, 'script'):
            self.package(suffix='too-large')
        self.script.unlink()
        with self.assertRaises(ValueError):
            self.package(suffix='missing')
        self.script.mkdir()
        with self.assertRaises(ValueError):
            self.package(suffix='directory')
        for path in ('../outside.sh', '/tmp/script.sh', 'C:/script.sh', 'deploy\\script.sh', 'x\r.sh'):
            config = self.config()
            config['hooks']['pre_install']['script'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_deployment(config)

    def test_source_symlink_and_symlink_parent_rejected(self):
        link = self.project / 'linked.sh'
        try:
            link.symlink_to(self.script)
            (self.project / 'linked-folder').symlink_to(self.script.parent, target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation not permitted')
        for i, path in enumerate(('linked.sh', 'linked-folder/pre-install.sh')):
            config = self.config()
            config['hooks']['pre_install']['script'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.package(config, 'symlink' + str(i))

    def test_rejects_undeclared_changed_or_linked_scripts(self):
        package = self.package()
        with tarfile.open(package) as archive:
            contents = {member.name: archive.extractfile(member).read() for member in archive}
        for kind in ('extra', 'changed', 'link', 'duplicate', 'oversized'):
            changed = dict(contents)
            if kind == 'changed':
                changed['hooks/pre-install.sh'] = b'echo altered\n'
            if kind == 'oversized':
                changed['hooks/pre-install.sh'] = b'#' * (256 * 1024 + 1)
                manifest = yaml.safe_load(changed['release.yaml'])
                manifest['hooks']['pre_install']['sha256'] = hashlib.sha256(changed['hooks/pre-install.sh']).hexdigest()
                changed['release.yaml'] = yaml.safe_dump(manifest).encode()
            attack = self.root / (kind + '.tar.gz')
            with tarfile.open(attack, 'w:gz') as archive:
                for name, data in changed.items():
                    member = tarfile.TarInfo(name)
                    if kind == 'link' and name == 'hooks/pre-install.sh':
                        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                        archive.addfile(member)
                    else:
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                if kind in ('extra', 'duplicate'):
                    member = tarfile.TarInfo('hooks/extra.sh' if kind == 'extra' else 'hooks/pre-install.sh')
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b'x'))
            destination = self.root / ('reject-' + kind)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                unpack_release(attack, destination)
            self.assertFalse(destination.exists())

    def test_protocol_minimum_and_manifest_must_agree(self):
        release = unpack_release(self.package(), self.root / 'unpacked')
        malformed = [dict(release, schema_version=1), dict(release, minimum_deployctl_version='1.0.0'),
                     dict(release, hooks={}), {k: v for k, v in release.items() if k != 'hooks'}]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_release(data)
        for key, value in (('path', '../escape'), ('sha256', 'x' * 64), ('timeout_seconds', False)):
            data = copy.deepcopy(release)
            data['hooks']['pre_install'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_release(data)


if __name__ == '__main__':
    unittest.main()
