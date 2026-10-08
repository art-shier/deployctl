import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'team-deploy'


class SkillBundleTests(unittest.TestCase):
    def test_bundled_managed_commands_and_version(self):
        from deployctl import __version__
        artifact=SKILL/'assets/deployctl.pyz'
        result=subprocess.run([sys.executable,'-I','-S',str(artifact),'--version'],capture_output=True,text=True)
        self.assertEqual(result.stdout.strip(),__version__)
        for action,flag in [('login','--server'),('publish','--channel'),('install','--prod'),('install','--with-platform-config'),('upgrade','--port')]:
            result=subprocess.run([sys.executable,'-I','-S',str(artifact),action,'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(flag,result.stdout)

    def test_bundled_runtime_flags_and_hook_protocol_without_site_packages(self):
        import json
        import tarfile
        import yaml
        artifact = SKILL / 'assets/deployctl.pyz'
        for action, flag in (('install', '--env-var'), ('install', '--set'), ('upgrade', '--unset-env')):
            result = subprocess.run([sys.executable, '-I', '-S', str(artifact), action, '--help'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(flag, result.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'pre.sh').write_text('true\n')
            config = yaml.safe_load((SKILL / 'assets/templates/deployment.yaml').read_text())
            config['hooks'] = {'pre_install': {'script': 'pre.sh'}}
            (project / 'deployment.yaml').write_text(yaml.safe_dump(config))
            validate = subprocess.run([sys.executable, '-I', '-S', str(artifact), 'validate', 'deployment.yaml'],
                                      cwd=project, capture_output=True, text=True)
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertIn('hooks', json.loads(validate.stdout))
            package = subprocess.run([sys.executable, '-I', '-S', str(artifact), 'package',
                                     '--config', 'deployment.yaml', '--image', 'ghcr.io/a/b@sha256:' + 'a' * 64,
                                     '--version', 'v1.0.0'], cwd=project, capture_output=True, text=True)
            self.assertEqual(package.returncode, 0, package.stderr)
            with tarfile.open(project / 'dist/project-a-v1.0.0.tar.gz') as archive:
                manifest = yaml.safe_load(archive.extractfile('release.yaml').read())
            self.assertEqual(manifest['schema_version'], 2)
            self.assertEqual(manifest['minimum_deployctl_version'], '1.5.0')

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
