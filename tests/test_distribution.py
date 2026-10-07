from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
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
