import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]

class StaticPackagingTests(unittest.TestCase):
    def builder(self):
        path=ROOT/'scripts/package_static.py'
        self.assertTrue(path.exists(), 'static packager is missing')
        spec=importlib.util.spec_from_file_location('package_static',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.package_static

    def test_reproducible_formats_preserve_root_and_ignore_mtime_mode(self):
        package=self.builder()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);site=root/'site';site.mkdir();(site/'assets').mkdir()
            (site/'index.html').write_bytes(b'index');(site/'assets/app.js').write_bytes(b'app')
            for fmt in ('zip','tar.gz'):
                first=root/('first.'+fmt);second=root/('second.'+fmt)
                info=package(site,first,fmt)
                for path in site.rglob('*'):
                    os.utime(path,(1000000000,1000000000))
                    if path.is_file():path.chmod(0o700)
                self.assertEqual(info,package(site,second,fmt))
                self.assertEqual(first.read_bytes(),second.read_bytes())
                from deployctl.static_archive import extract_static_archive
                target=root/('extracted-'+fmt);extract_static_archive(first,target)
                self.assertEqual((target/'index.html').read_bytes(),b'index')
                self.assertFalse((target/'site').exists())

    def test_internal_output_and_symlink_rejected(self):
        package=self.builder()
        with tempfile.TemporaryDirectory() as temporary:
            site=Path(temporary)/'site';site.mkdir();(site/'index.html').write_bytes(b'index')
            with self.assertRaises(ValueError):package(site,site/'output.tar.gz')
            self.assertFalse((site/'output.tar.gz').exists())
            if os.name=='posix':
                (site/'link').symlink_to('index.html')
                with self.assertRaises(ValueError):package(site,Path(temporary)/'out.tar.gz')

    def test_failure_preserves_existing_output_and_size_is_bounded(self):
        from unittest.mock import patch
        package=self.builder()
        with tempfile.TemporaryDirectory() as temporary:
            site=Path(temporary)/'site';site.mkdir();(site/'index.html').write_bytes(b'large')
            output=Path(temporary)/'out.tar.gz';output.write_bytes(b'existing')
            with patch('deployctl.static_archive.MAX_FILE',2):
                with self.assertRaises(ValueError):package(site,output)
            self.assertEqual(output.read_bytes(),b'existing')

class StaticDistributionTests(unittest.TestCase):
    def test_skill_archive_includes_static_contract_and_cli(self):
        from scripts.build_agent_assets import build_archive
        with tempfile.TemporaryDirectory() as temporary:
            archive=build_archive(ROOT/'skills/team-deploy',Path(temporary)/'skill.zip')
            with zipfile.ZipFile(archive) as bundle:
                self.assertIn('team-deploy/assets/templates/static-release.yml',bundle.namelist())
                self.assertIn('team-deploy/references/static-deployment.md',bundle.namelist())
            with zipfile.ZipFile(ROOT/'skills/team-deploy/assets/deployctl.pyz') as cli:
                self.assertIn('deployctl/static_runtime.py',cli.namelist())

if __name__=='__main__':unittest.main()
