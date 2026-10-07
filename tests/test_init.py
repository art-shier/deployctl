import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.dockerfile = self.project / 'Dockerfile'
        self.dockerfile.write_text('FROM python:3.12-slim\nEXPOSE 3000\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def init(self, *extra):
        return subprocess.run([
            sys.executable, '-m', 'deployctl', 'init', 'project-a',
            '--directory', str(self.project), '--platform-repository', 'acme/team-deploy',
            '--platform-ref', 'v1.1.0', '--port', '3000', '--health-path', '/ready', *extra,
        ], cwd=ROOT, capture_output=True, text=True)

    def test_init_generates_valid_project_contract_and_matching_workflow(self):
        original = self.dockerfile.read_bytes()
        result = self.init('--required-config', 'DATABASE_URL')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = yaml.safe_load((self.project / 'deploy/deployment.yaml').read_text())
        self.assertEqual(config['application'], 'project-a')
        self.assertEqual(config['container']['port'], 3000)
        self.assertEqual(config['health']['readiness_path'], '/ready')
        self.assertEqual(config['required_config'], ['DATABASE_URL'])
        workflow = yaml.safe_load((self.project / '.github/workflows/release.yml').read_text())
        job = workflow['jobs']['release']
        self.assertEqual(job['uses'], 'acme/team-deploy/.github/workflows/build-release.yml@v1.1.0')
        self.assertEqual(job['with']['platform-repository'], 'acme/team-deploy')
        self.assertEqual(job['with']['platform-ref'], 'v1.1.0')
        self.assertEqual(job['with']['version'], '${{ github.ref_name }}')
        self.assertEqual(self.dockerfile.read_bytes(), original)
        self.assertFalse((self.project / '.github/workflows/deploy.yml').exists())
        self.assertFalse((self.project / 'release.yaml').exists())
        check = subprocess.run([sys.executable, '-m', 'deployctl', 'validate',
                                str(self.project / 'deploy/deployment.yaml')],
                               cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_existing_workflow_blocks_entire_init_without_overwrite(self):
        workflow = self.project / '.github/workflows/release.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: existing\n', encoding='utf-8')
        result = self.init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exists', result.stderr)
        self.assertEqual(workflow.read_text(), 'name: existing\n')
        self.assertFalse((self.project / 'deploy/deployment.yaml').exists())

    def test_existing_contract_is_preserved(self):
        config = self.project / 'deploy/deployment.yaml'
        config.parent.mkdir()
        config.write_text('keep me\n')
        result = self.init()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(config.read_text(), 'keep me\n')
        self.assertFalse((self.project / '.github/workflows/release.yml').exists())

    def test_dry_run_returns_reviewable_content_without_creating_directories(self):
        result = self.init('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(len(preview['files']), 2)
        generated = {entry['path']: entry['content'] for entry in preview['files']}
        self.assertEqual(yaml.safe_load(generated['deploy/deployment.yaml'])['container']['port'], 3000)
        self.assertFalse((self.project / 'deploy').exists())
        self.assertFalse((self.project / '.github').exists())

    def test_invalid_parameters_create_no_files(self):
        cases = [('--platform-repository', '../other'), ('--platform-ref', 'main\nother'),
                 ('--port', '0'), ('--health-path', 'https://example.test'),
                 ('--deployment-file', '../escaped.yaml')]
        for args in cases:
            with self.subTest(args=args):
                result = self.init(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.project / 'deploy').exists())
                self.assertFalse((self.project / '.github').exists())

    def test_optional_deploy_workflow_has_matching_application_and_platform(self):
        result = self.init('--with-deploy-workflow', '--test-command', 'python -m unittest')
        self.assertEqual(result.returncode, 0, result.stderr)
        workflow = yaml.safe_load((self.project / '.github/workflows/deploy.yml').read_text())
        job = workflow['jobs']['deploy']
        self.assertEqual(job['uses'], 'acme/team-deploy/.github/workflows/deploy.yml@v1.1.0')
        self.assertEqual(job['with']['application'], 'project-a')
        self.assertEqual(job['with']['platform-ref'], 'v1.1.0')
        release = yaml.safe_load((self.project / '.github/workflows/release.yml').read_text())
        self.assertEqual(release['jobs']['release']['with']['test-command'], 'python -m unittest')

    def test_custom_output_paths_and_existing_build_paths(self):
        result = self.init('--deployment-file', 'ops/app.yaml',
                           '--workflow-file', '.github/workflows/team-release.yml')
        self.assertEqual(result.returncode, 0, result.stderr)
        workflow = yaml.safe_load((self.project / '.github/workflows/team-release.yml').read_text())
        self.assertEqual(workflow['jobs']['release']['with']['deployment-file'], 'ops/app.yaml')
        self.assertTrue((self.project / 'ops/app.yaml').is_file())

    def test_missing_dockerfile_reports_gap_without_creating_yaml(self):
        self.dockerfile.unlink()
        result = self.init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Dockerfile', result.stderr)
        self.assertFalse((self.project / 'deploy').exists())

    def test_outputs_cannot_escape_project_through_directory_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.project / 'deploy').symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Directory symlinks unavailable on this host')
            result = self.init()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(outside) / 'deployment.yaml').exists())
