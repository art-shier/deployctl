"""Bootstrap packaging and the Agent's actual trusted-path contract."""
from pathlib import Path
import os
import stat
import unittest
from unittest.mock import patch
from deployctl import updater

ROOT = Path(__file__).resolve().parents[1]

class NodeBootstrapTests(unittest.TestCase):
    def test_public_script_matches_embedded_source(self):
        self.assertEqual((ROOT/'control-server/internal/nodeinstall/bootstrap.sh').read_bytes(),
                         (ROOT/'skills/team-deploy/assets/node-bootstrap.sh').read_bytes())

    def test_root_private_run_token_passes_existing_agent_contract(self):
        token = Path('/run/ctl-node-install.abcd1234/enrollment.token')
        def node_stat(path, *args, **kwargs):
            mode = stat.S_IFREG | 0o600 if path == token else stat.S_IFDIR | 0o755
            if path == token.parent: mode = stat.S_IFDIR | 0o700
            return os.stat_result((mode, 1, 1, 1, 0, 0, 0, 0, 0, 0))
        with patch.object(Path, 'stat', node_stat), patch.object(Path, 'lstat', node_stat):
            self.assertEqual(updater.trusted(token, private=True), token)
