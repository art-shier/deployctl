import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from test_static_runtime import resolution
from deployctl.static_runtime import StaticManager
from deployctl.static_target import target_lock

@unittest.skipIf(os.name=='nt','Linux ownership and target locking')
class StaticTargetTests(unittest.TestCase):
    def test_different_roots_cannot_claim_the_same_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);base.chmod(0o755);target=base/'public'/'a'
            package=Path(__file__).parent/'fixtures/static-archives/valid.zip'
            ctx=multiprocessing.get_context('fork');queue=ctx.Queue()
            def deploy(name):
                try:
                    StaticManager(base/name,base/'config').deploy('project-a','prod',package,resolution(package,target=target))
                    queue.put('success')
                except (ValueError,RuntimeError):queue.put('conflict')
            processes=[ctx.Process(target=deploy,args=(name,)) for name in ('one','two')]
            for process in processes:process.start()
            for process in processes:process.join(15);self.assertEqual(process.exitcode,0)
            self.assertEqual(sorted([queue.get(timeout=2),queue.get(timeout=2)]),['conflict','success'])
            self.assertEqual((target/'index.html').read_bytes(),b'hello')

    def test_nested_targets_and_cache_overlaps_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);base.chmod(0o755);target=base/'public/a'
            package=Path(__file__).parent/'fixtures/static-archives/valid.zip'
            first=StaticManager(base/'one',base/'config');first.deploy('project-a','prod',package,resolution(package,target=target))
            second=StaticManager(base/'two',base/'config')
            for proposed in (target/'nested',target.parent,base/'two'/'public'):
                with self.subTest(target=proposed),self.assertRaises(ValueError):
                    second.deploy('project-a','prod',package,resolution(package,target=proposed))
            self.assertEqual((target/'index.html').read_bytes(),b'hello')
