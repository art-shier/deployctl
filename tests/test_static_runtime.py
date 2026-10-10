import errno
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deployctl.static_archive import inspect_static_archive
from deployctl.static_runtime import StaticManager
from deployctl.static_state import build_tree_manifest,tree_bytes
from dataclasses import replace


def resolution(package,version='v1.0.0',project='project-a',target=None):
    info=inspect_static_archive(package)
    return {'schema_version':2,'minimum_client_version':'1.13.0','deployment_type':'static','project':project,'environment':'prod',
            'release':{'id':'a'*32,'version':version,'sha256':info.sha256,'archive_format':info.archive_format,'size':info.size,
                       'expanded_size':info.expanded_size,'entry_count':info.entry_count,'commit':'','package_path':f'/api/v1/projects/{project}/artifacts/'+ 'a'*32},
            'configuration':{'id':'b'*32,'revision':1,'deployment_defaults':{'target_dir':str(target)} if target else {}}}


@unittest.skipIf(os.name=='nt','Linux static file transactions')
class StaticRuntimeTests(unittest.TestCase):
    def test_other_root_cannot_mutate_physical_release_tree(self):
        package=self.package('with-empty',{'index.html':b'one','empty/':b''})
        self.manager.deploy('project-a','prod',package,resolution(package,target=self.target))
        physical=(self.target/'empty').resolve()
        second=StaticManager(self.base/'other-root',self.base/'other-config')
        for proposed in (physical,physical/'new'/'child'):
            with self.subTest(target=proposed),self.assertRaisesRegex(ValueError,'managed|cache'):
                second.deploy('project-b','prod',self.v2,resolution(self.v2,project='project-b',target=proposed))
        nested_root=physical/'hidden-root'
        with self.assertRaisesRegex(ValueError,'managed|cache'):
            StaticManager(nested_root,self.base/'other-config').deploy('project-b','prod',self.v2,resolution(self.v2,project='project-b',target=self.base/'other-public'))
        self.assertEqual(list(physical.iterdir()),[])
        self.assertFalse(nested_root.exists())
        self.assertIn('v1.0.0',self.manager.operate('project-a','prod','status'))

    def test_missing_state_cannot_reclaim_owned_target(self):
        self.install()
        (self.root/'project-a/prod/state.json').unlink()
        with self.assertRaisesRegex(ValueError,'state|ownership|owner'):
            self.install()
        self.assertEqual((self.target/'index.html').read_bytes(),b'one')

    def test_obsolete_cache_reextraction_rechecks_package_identity(self):
        self.install();self.upgrade()
        v3=self.base/'v3.zip'
        with zipfile.ZipFile(v3,'w') as archive:archive.writestr('index.html',b'three')
        self.manager.deploy('project-a','prod',v3,resolution(v3,'v3.0.0'),upgrade=True)
        from deployctl.static_archive import extract_static_archive
        def changed_identity(package,destination):
            return replace(extract_static_archive(package,destination),sha256='0'*64)
        with patch('deployctl.static_runtime.extract_static_archive',side_effect=changed_identity):
            with self.assertRaisesRegex(ValueError,'changed'):
                self.manager.deploy('project-a','prod',self.v1,resolution(self.v1),upgrade=True)
        self.assertEqual((self.target/'index.html').read_bytes(),b'three')
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ctl-static-')
        self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.base.chmod(0o755)
        self.root=self.base/'managed';self.target=self.base/'public'/'project-a'
        self.manager=StaticManager(self.root,self.base/'config')
        self.v1=self.package('one',{'index.html':b'one','old.txt':b'old'})
        self.v2=self.package('two',{'index.html':b'two','new.txt':b'new'})

    def package(self,name,files):
        path=self.base/(name+'.zip')
        with zipfile.ZipFile(path,'w') as archive:
            for file,data in files.items():archive.writestr(file,data)
        return path

    def install(self):
        return self.manager.deploy('project-a','prod',self.v1,resolution(self.v1,target=self.target))

    def upgrade(self):
        return self.manager.deploy('project-a','prod',self.v2,resolution(self.v2,'v2.0.0',target=self.target),upgrade=True)

    def test_install_replace_retry_and_offline_rollback(self):
        self.install();self.assertEqual((self.target/'old.txt').read_bytes(),b'old')
        state=self.upgrade();self.assertEqual(state['previous']['version'],'v1.0.0')
        self.assertFalse((self.target/'old.txt').exists());self.assertEqual((self.target/'index.html').read_bytes(),b'two')
        retry=self.upgrade();self.assertEqual(retry['previous']['version'],'v1.0.0')
        restored=self.manager.rollback('project-a','prod');self.assertEqual(restored['current']['version'],'v1.0.0')
        self.assertEqual((self.target/'index.html').read_bytes(),b'one');self.assertFalse((self.target/'new.txt').exists())
        self.assertIn('v1.0.0',self.manager.operate('project-a','prod','status'))
        self.assertIn('install',self.manager.operate('project-a','prod','logs'))
        self.assertEqual(sorted(p.name for p in self.target.iterdir()),['index.html','old.txt'])

    def test_cli_directory_binding_and_remote_precedence(self):
        other=self.base/'ignored'
        self.manager.deploy('project-a','prod',self.v1,resolution(self.v1,target=self.target),target_dir=str(other))
        self.assertTrue(self.target.is_symlink());self.assertFalse(other.exists())
        with self.assertRaisesRegex(ValueError,'migrat'):
            self.manager.deploy('project-a','prod',self.v2,resolution(self.v2,'v2.0.0',target=other),upgrade=True)
        self.manager.deploy('project-a','prod',self.v2,resolution(self.v2,'v2.0.0'),upgrade=True)
        self.assertEqual((self.target/'index.html').read_bytes(),b'two')

    def test_initial_empty_directory_and_failure_restoration(self):
        self.target.mkdir(parents=True)
        with patch('deployctl.static_runtime.switch_target',side_effect=OSError(errno.EACCES,'fixture')):
            with self.assertRaises(OSError):self.install()
        self.assertTrue(self.target.is_dir());self.assertFalse(self.target.is_symlink());self.assertEqual(list(self.target.iterdir()),[])
        self.install();self.assertTrue(self.target.is_symlink())

    def test_preparation_failure_preserves_old_tree(self):
        self.install()
        with patch('deployctl.static_runtime.extract_static_archive',side_effect=OSError(errno.ENOSPC,'fixture')):
            with self.assertRaises(OSError):self.upgrade()
        self.assertEqual((self.target/'index.html').read_bytes(),b'one')
        self.assertIn('v1.0.0',self.manager.operate('project-a','prod','status'))

    def test_switch_failure_rolls_back_or_keeps_pending(self):
        self.install()
        from deployctl.static_target import switch_target
        calls=0
        def fail_once(target,files):
            nonlocal calls
            calls+=1;switch_target(target,files)
            if calls==1:raise OSError(errno.EIO,'fixture')
        with patch('deployctl.static_runtime.switch_target',side_effect=fail_once):
            with self.assertRaises(OSError):self.upgrade()
        self.assertEqual((self.target/'index.html').read_bytes(),b'one')
        state=json.loads((self.root/'project-a/prod/state.json').read_text());self.assertIsNone(state['transaction'])

    def test_sigkill_after_pending_can_be_recovered(self):
        self.install()
        def crash():
            manager=StaticManager(self.root,self.base/'config');save=manager._save
            def stopped(state,event):
                save(state,event)
                if state['transaction']:os.kill(os.getpid(),signal.SIGKILL)
            manager._save=stopped
            manager.deploy('project-a','prod',self.v2,resolution(self.v2,'v2.0.0',target=self.target),upgrade=True)
        process=multiprocessing.get_context('fork').Process(target=crash);process.start();process.join(15)
        self.assertEqual(process.exitcode,-signal.SIGKILL)
        with self.assertRaisesRegex(RuntimeError,'pending'):self.upgrade()
        recovered=self.manager.rollback('project-a','prod');self.assertIsNone(recovered['transaction'])
        self.assertEqual((self.target/'index.html').read_bytes(),b'one')

    def test_failed_automatic_recovery_retains_pending(self):
        self.install()
        from deployctl.static_target import switch_target
        def fail_after_switch(target,files):
            switch_target(target,files);raise OSError(errno.EIO,'fixture')
        with patch('deployctl.static_runtime.switch_target',side_effect=fail_after_switch):
            with self.assertRaises(OSError):self.upgrade()
        state=json.loads((self.root/'project-a/prod/state.json').read_text());self.assertIsNotNone(state['transaction'])
        with self.assertRaisesRegex(RuntimeError,'pending'):self.upgrade()
        restored=self.manager.rollback('project-a','prod');self.assertIsNone(restored['transaction'])
        self.assertEqual((self.target/'index.html').read_bytes(),b'one')

    def test_initial_sigkill_can_restore_original_empty_directory(self):
        self.target.mkdir(parents=True);inode=self.target.stat().st_ino
        def crash():
            from deployctl.static_target import switch_target
            def stopped(target,files):
                switch_target(target,files);os.kill(os.getpid(),signal.SIGKILL)
            with patch('deployctl.static_runtime.switch_target',side_effect=stopped):self.install()
        process=multiprocessing.get_context('fork').Process(target=crash);process.start();process.join(15)
        self.assertEqual(process.exitcode,-signal.SIGKILL);self.assertTrue(self.target.is_symlink())
        state=self.manager.rollback('project-a','prod');self.assertIsNone(state['current'])
        self.assertFalse(self.target.is_symlink());self.assertEqual(self.target.stat().st_ino,inode);self.assertEqual(list(self.target.iterdir()),[])

    def test_root_parent_alias_cannot_hide_cache_overlap(self):
        (self.base/'alias').mkdir()
        manager=StaticManager(self.base/'alias/../managed',self.base/'config')
        proposed=self.base/'managed'/'public'
        with self.assertRaises(ValueError):manager.deploy('project-a','prod',self.v1,resolution(self.v1,target=proposed))

    def test_unknown_targets_and_tampered_cache_are_rejected(self):
        self.target.mkdir(parents=True);(self.target/'unmanaged').write_text('keep')
        with self.assertRaises(ValueError):self.install()
        self.assertEqual((self.target/'unmanaged').read_text(),'keep')
        (self.target/'unmanaged').unlink();self.install();self.upgrade()
        old=self.root/'project-a/prod/releases'
        for directory in old.iterdir():
            if directory.name.startswith('v1.0.0-'):(directory/'files/index.html').write_text('tampered')
        with self.assertRaises(ValueError):self.manager.rollback('project-a','prod')
        self.assertEqual((self.target/'index.html').read_bytes(),b'two')

    def test_obsolete_cache_manifest_cannot_authorize_tampered_files(self):
        self.install();self.upgrade()
        third=self.package('three',{'index.html':b'three'})
        self.manager.deploy('project-a','prod',third,resolution(third,'v3.0.0',target=self.target),upgrade=True)
        for folder in (self.root/'project-a/prod/releases').iterdir():
            if folder.name.startswith('v1.0.0-'):
                (folder/'files/index.html').write_text('evil')
                (folder/'manifest.json').write_bytes(tree_bytes(build_tree_manifest(folder/'files')))
        with self.assertRaises(ValueError):
            self.manager.deploy('project-a','prod',self.v1,resolution(self.v1,target=self.target),upgrade=True)
        self.assertEqual((self.target/'index.html').read_bytes(),b'three')

    @unittest.skipUnless(hasattr(os,'geteuid') and os.geteuid()==0,'actual non-root reader')
    def test_non_root_can_read_only_public_files(self):
        self.install()
        result=subprocess.run(['runuser','-u','nobody','--','cat',str(self.target/'index.html')],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'one')
        self.assertEqual((self.root/'project-a/prod/state.json').stat().st_mode&0o777,0o600)
