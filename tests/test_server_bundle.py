import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from deployctl.cli import parser
from deployctl import server_bundle

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/art-shier/ctl-server@sha256:' + 'a' * 64


class ServerBundleTests(unittest.TestCase):
    def test_round_trip_is_deterministic_and_pins_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = server_bundle.build_bundle(ROOT, tmp, IMAGE, 'v1.7.0')
            first = package.read_bytes()
            self.assertEqual(first, server_bundle.build_bundle(ROOT, tmp, IMAGE, 'v1.7.0').read_bytes())
            manifest, files = server_bundle.read_bundle(package)
            self.assertEqual(manifest['image'], IMAGE)
            self.assertEqual(set(files), server_bundle.FILES)
            self.assertEqual(hashlib.sha256(first).hexdigest(), Path(str(package) + '.sha256').read_text().split()[0])
            with self.assertRaises(ValueError):
                server_bundle.build_bundle(ROOT, tmp, 'ghcr.io/art-shier/ctl-server:latest', 'v1.7.0')

    def test_rejects_unsafe_entries_hash_changes_and_future_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = server_bundle.build_bundle(ROOT, tmp, IMAGE, 'v1.7.0')
            _, original = server_bundle.read_bundle(package)
            variants = []
            corrupt = dict(original); corrupt['control-deploy/bootstrap.sh'] += b'\nfalse\n'; variants.append(corrupt)
            for name in ('../escape', '/absolute', 'extra'):
                unsafe = dict(original); unsafe[name] = b'bad'; variants.append(unsafe)
            future = dict(original)
            manifest = json.loads(future['server-release.json']); manifest['minimum_deployctl_version'] = '99.0.0'
            future['server-release.json'] = json.dumps(manifest).encode(); variants.append(future)
            for files in variants:
                with tarfile.open(package, 'w:gz') as archive:
                    for name, raw in files.items():
                        item = tarfile.TarInfo(name); item.size = len(raw); archive.addfile(item, io.BytesIO(raw))
                with self.assertRaises(ValueError): server_bundle.read_bundle(package)
            with tarfile.open(package, 'w:gz') as archive:
                item = tarfile.TarInfo('server-release.json'); item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'; archive.addfile(item)
            with self.assertRaises(ValueError): server_bundle.read_bundle(package)
            with gzip.open(package, 'wb') as archive: archive.write(b'0' * (server_bundle.MAX_EXPANDED + 1))
            with self.assertRaises(ValueError): server_bundle.read_bundle(package)

    def test_parser_supports_server_release_install_and_upgrade(self):
        for operation in ('install', 'upgrade'):
            args = parser().parse_args(['server', operation, '--release', 'https://example.test/platform.tar.gz'])
            self.assertEqual(args.command, 'server')
            self.assertEqual(args.server_command, operation)
            self.assertEqual(args.home, '/opt/ctl-platform')

    @unittest.skipIf(os.name == 'nt', 'Linux root bootstrap state')
    def test_failure_retains_pending_and_only_exact_bundle_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'instance'
            first = server_bundle.build_bundle(ROOT, Path(tmp)/'first', IMAGE, 'v1.7.0')
            other = server_bundle.build_bundle(ROOT, Path(tmp)/'other', IMAGE, 'v1.7.1')
            with patch.object(server_bundle, 'LOCK_HOME', Path(tmp)/'locks'), patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'run_bootstrap', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError): server_bundle.deploy_server(first, home=home)
            state = json.loads((home/'server-state.json').read_text())
            self.assertIsNone(state['current']); self.assertIsNotNone(state['pending'])
            with patch.object(server_bundle, 'LOCK_HOME', Path(tmp)/'locks'), patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'run_bootstrap') as runner:
                with self.assertRaises(ValueError): server_bundle.deploy_server(other, home=home)
                runner.assert_not_called()
                result = server_bundle.deploy_server(first, home=home)
                self.assertEqual(result['current']['image'], IMAGE)
                self.assertIsNone(result['pending'])
                with self.assertRaises(ValueError): server_bundle.deploy_server(first, home=home)
                result = server_bundle.deploy_server(other, home=home, upgrade=True)
                self.assertEqual(result['current']['version'], 'v1.7.1')
                self.assertEqual((home/'server-state.json').stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == 'nt', 'Linux root bootstrap state')
    def test_root_directory_permissions_and_links_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = server_bundle.build_bundle(ROOT, tmp, IMAGE, 'v1.7.0')
            public = Path(tmp)/'public'; public.mkdir(mode=0o755)
            linked = Path(tmp)/'linked'; linked.symlink_to(public, target_is_directory=True)
            with patch.object(server_bundle, 'LOCK_HOME', Path(tmp)/'locks'), patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'run_bootstrap') as runner:
                for home in (public, linked):
                    with self.assertRaises(ValueError): server_bundle.deploy_server(package, home=home)
                runner.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX ancestor permissions')
    def test_writable_ancestor_rejected_before_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = server_bundle.build_bundle(ROOT, tmp, IMAGE, 'v1.7.0')
            parent = Path(tmp)/'mutable'; parent.mkdir(); parent.chmod(0o777)
            with patch.object(server_bundle, 'require_runtime'), patch.object(server_bundle, 'run_bootstrap') as runner:
                with self.assertRaisesRegex(ValueError, 'ancestors'): server_bundle.deploy_server(package, home=parent/'instance')
                runner.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'actual POSIX process cleanup')
    def test_timeout_stops_background_work_before_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); (folder/'control-deploy').mkdir()
            marker = folder/'marker'
            (folder/'control-deploy/bootstrap.sh').write_text(f'(sleep .4; touch "{marker}") &\nwait\n')
            with patch.object(server_bundle, 'check_project'), patch.object(server_bundle, 'BOOTSTRAP_TIMEOUT', .05):
                with self.assertRaisesRegex(RuntimeError, 'timed out'): server_bundle.run_bootstrap(folder, folder, IMAGE, {})
            time.sleep(.5)
            self.assertFalse(marker.exists())

    def test_another_instance_home_is_rejected(self):
        import subprocess
        replies = [subprocess.CompletedProcess([], 0, 'a'*12, ''), subprocess.CompletedProcess([], 0,
            json.dumps([{'Destination':'/run/ctl-keys', 'Source':'/other/instance/keys'}]), '')]
        with patch.object(server_bundle.subprocess, 'run', side_effect=replies):
            with self.assertRaisesRegex(ValueError, 'different home'): server_bundle.check_project(Path('/expected/instance'))

    def test_stalled_docker_inspection_has_actionable_error(self):
        import subprocess
        with patch.object(server_bundle.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['docker'], 30)):
            with self.assertRaisesRegex(RuntimeError, 'inspection timed out'): server_bundle.check_project(Path('/instance'))


if __name__ == '__main__': unittest.main()
