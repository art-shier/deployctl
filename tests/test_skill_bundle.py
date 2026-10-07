import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'team-deploy'


class SkillBundleTests(unittest.TestCase):
    def test_standalone_skill_cli_validates_its_project_template(self):
        artifact = SKILL / 'assets' / 'deployctl.pyz'
        result = subprocess.run([sys.executable, '-I', '-S', str(artifact), 'validate',
                                 str(SKILL / 'assets/templates/deployment.yaml')],
                                cwd=SKILL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"application": "project-a"', result.stdout)

    def test_skill_cli_checksum_matches_distributed_binary(self):
        artifact = SKILL / 'assets' / 'deployctl.pyz'
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(artifact.with_name(artifact.name + '.sha256').read_text().split()[0], digest)

    def test_skill_cli_self_update_help_runs_without_external_dependencies(self):
        result = subprocess.run([sys.executable, '-I', '-S', str(SKILL / 'assets/deployctl.pyz'),
                                 'self-update', '--help'], cwd=SKILL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--version', result.stdout)

    def test_skill_cli_keeps_failures_nonzero(self):
        result = subprocess.run([sys.executable, '-I', '-S', str(SKILL / 'assets/deployctl.pyz'),
                                 'validate', str(SKILL / 'nonexistent.yaml')],
                                cwd=SKILL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_standalone_skill_cli_includes_init_and_preserves_platform_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'Dockerfile').write_text('FROM python:3.12-slim\n')
            result = subprocess.run([
                sys.executable, '-I', '-S', str(SKILL / 'assets/deployctl.pyz'),
                'init', 'project-a', '--directory', str(project),
                '--platform-repository', 'acme/team-deploy', '--platform-ref', 'v1.0.0',
            ], cwd=SKILL, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            import yaml
            workflow = yaml.safe_load((project / '.github/workflows/release.yml').read_text())
            self.assertEqual(workflow['jobs']['release']['with']['platform-ref'], 'v1.0.0')
