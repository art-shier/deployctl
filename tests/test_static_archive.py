import json
from pathlib import Path
import tempfile
import unittest
from deployctl.static_archive import inspect_static_archive, extract_static_archive

FIXTURES = Path(__file__).parent/'fixtures/static-archives'

class StaticArchiveTests(unittest.TestCase):
    def test_shared_archives_and_safe_extraction(self):
        for case in json.loads((FIXTURES/'cases.json').read_text()):
            with self.subTest(case=case['name']):
                path=FIXTURES/case['name']
                if not case['valid']:
                    with self.assertRaises(ValueError): inspect_static_archive(path)
                else:
                    info=inspect_static_archive(path)
                    self.assertEqual(info.expanded_size,9)
                    self.assertEqual(info.entry_count,3)
                    with tempfile.TemporaryDirectory() as directory:
                        dest=Path(directory)/'files'
                        extracted=extract_static_archive(path,dest)
                        self.assertEqual(extracted,info)
                        self.assertEqual((dest/'index.html').read_bytes(),b'hello')
                        self.assertEqual((dest/'资源/a [1].txt').read_bytes(),b'data')

    def test_small_injected_limits_reject_actual_expansion(self):
        from unittest.mock import patch
        with patch('deployctl.static_archive.MAX_EXPANDED',8):
            with self.assertRaises(ValueError): inspect_static_archive(FIXTURES/'valid.zip')
        with patch('deployctl.static_archive.MAX_ENTRIES',2):
            with self.assertRaises(ValueError): inspect_static_archive(FIXTURES/'valid.tar.gz')
        with patch('deployctl.static_archive.MAX_FILE',4):
            with self.assertRaises(ValueError): inspect_static_archive(FIXTURES/'valid.zip')

    def test_extraction_refuses_existing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory);(dest/'existing').write_text('preserve')
            with self.assertRaises(ValueError):extract_static_archive(FIXTURES/'valid.zip',dest)
            self.assertEqual((dest/'existing').read_text(),'preserve')

    def test_implicit_directories_are_included_in_resource_budget(self):
        import zipfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/'deep.zip'
            with zipfile.ZipFile(package,'w') as archive:archive.writestr('a/b/c/d/file',b'one')
            with patch('deployctl.static_archive.MAX_ENTRIES',3):
                with self.assertRaises(ValueError):inspect_static_archive(package)
