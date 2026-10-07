from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PrivatePlatformTests(unittest.TestCase):
    def test_init_maps_explicit_private_platform_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Dockerfile').write_text('FROM python:3.12-slim\n')
            result = subprocess.run([sys.executable, '-m', 'deployctl', 'init', 'project-a',
                                     '--directory', str(root), '--platform-repository', 'art-shier/deployctl',
                                     '--platform-ref', 'v1.2.0', '--private-platform', '--with-deploy-workflow'],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for file, job in [('release.yml', 'release'), ('deploy.yml', 'deploy')]:
                workflow = yaml.safe_load((root / '.github/workflows' / file).read_text())
                self.assertEqual(workflow['jobs'][job]['secrets']['PLATFORM_READ_TOKEN'],
                                 '${{ secrets.PLATFORM_READ_TOKEN }}')
