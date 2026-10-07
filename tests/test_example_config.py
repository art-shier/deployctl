import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class ExampleConfigTests(unittest.TestCase):
    def test_json_startup_preserves_strings_and_rejects_explicit_bad_file(self):
        path = Path(__file__).resolve().parents[1] / 'examples/project-a/app.py'
        spec = importlib.util.spec_from_file_location('example_app', path)
        app = importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        self.assertEqual(app.load_runtime_config({'APP_VERSION': 'development'})['APP_VERSION'], 'development')
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / '.env.json'
            data = {'APP_VERSION': 'v1.0.0', 'TEXT': ' literal $ # = ', 'EMPTY': ''}
            file.write_text(json.dumps(data))
            env = {'APP_VERSION': 'v1.0.0', 'DEPLOYCTL_ENV_FILE': str(file)}
            self.assertEqual(app.load_runtime_config(env)['TEXT'], data['TEXT'])
            for bad in ({'APP_VERSION': 1}, {'APP_VERSION': 'v1.0.0', 'A': True},
                        {'APP_VERSION': 'v2.0.0'}, []):
                file.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError): app.load_runtime_config(env)
            file.unlink()
            with self.assertRaises(RuntimeError): app.load_runtime_config(env)


if __name__ == '__main__': unittest.main()
