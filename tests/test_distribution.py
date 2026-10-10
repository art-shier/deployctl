from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_unpinned_yaml_is_rejected_before_building_release_assets(self):
        from scripts import build_zipapp
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / 'deployctl.pyz'
            with patch.object(build_zipapp.yaml, '__version__', '6.0.1'), \
                 patch('sys.argv', ['build_zipapp', '--output', str(artifact)]):
                with self.assertRaisesRegex(SystemExit, 'PyYAML==6.0.3'):
                    build_zipapp.main()
            self.assertFalse(artifact.exists())

    def test_cli_archive_is_reproducible_across_source_newlines_and_timestamps(self):
        from scripts import build_zipapp
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            archives=[]
            for label,newline in [('windows','\r\n'),('linux','\n')]:
                source=base/label;package=source/'deployctl';package.mkdir(parents=True)
                (package/'__init__.py').write_bytes(('__version__ = "1.12.0"'+newline).encode())
                (package/'cli.py').write_bytes(newline.join(['def main():','    print("1.12.0")','    return 0','']).encode())
                for name in ('LICENSE','THIRD_PARTY_NOTICES.txt'):
                    (source/name).write_bytes(('Public text'+newline).encode())
                import os
                for path in source.rglob('*'):
                    if path.is_file(): os.utime(path,(1000000000 if label=='windows' else 1700000000,)*2)
                archive=base/(label+'.pyz');archives.append(archive)
                with patch.object(build_zipapp,'__file__',str(source/'scripts/build_zipapp.py')),patch('sys.argv',['build_zipapp','--output',str(archive)]):
                    build_zipapp.main()
                result=subprocess.run([sys.executable,'-I','-S',str(archive),'--version'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'1.12.0')
                with zipfile.ZipFile(archive) as bundle:
                    self.assertEqual(bundle.namelist(),sorted(bundle.namelist()))
                    self.assertTrue(all(entry.date_time==(1980,1,1,0,0,0) for entry in bundle.infolist()))
            self.assertEqual(archives[0].read_bytes(),archives[1].read_bytes())

    def test_packaged_cli_preserves_failure_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'deployctl.pyz'
            build = subprocess.run([sys.executable, 'scripts/build_zipapp.py', '--output', str(artifact)],
                                   cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            bad = subprocess.run([sys.executable, '-I', '-S', str(artifact), 'validate', 'missing.yaml'],
                                 cwd=tmp, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1, bad.stderr)
            self.assertIn('ERROR', bad.stderr)
            good = subprocess.run([sys.executable, '-I', '-S', str(artifact), '--version'],
                                  cwd=tmp, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
