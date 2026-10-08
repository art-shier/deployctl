import copy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from deployctl.release import build_release, unpack_release
from test_release import CONFIG, IMAGE

ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def test_refresh_hook_schemas_and_minimum_match_contract(self):
        ds = json.loads((ROOT / 'schemas/deployment.schema.json').read_text())
        rs = json.loads((ROOT / 'schemas/release.schema.json').read_text())
        registry = Registry().with_resource('deployment.schema.json', Resource.from_contents(ds))
        project_validator = Draft202012Validator(ds)
        release_validator = Draft202012Validator(rs, registry=registry)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pre.sh').write_text('true\n')
            for index, refresh in enumerate((True, False)):
                config = copy.deepcopy(CONFIG)
                config['hooks'] = {'pre_install': {'script': 'pre.sh', 'refresh_config': refresh}}
                self.assertEqual(list(project_validator.iter_errors(config)), [])
                archive = build_release(config, IMAGE, f'v1.0.{index}', root / 'packages', project_root=root)
                release = unpack_release(archive, root / f'r{index}')
                self.assertEqual(list(release_validator.iter_errors(release)), [])
                release['minimum_deployctl_version'] = '1.5.0'
                self.assertTrue(list(release_validator.iter_errors(release)))
            config['hooks'] = {'post_install': {'script': 'pre.sh', 'refresh_config': True}}
            self.assertTrue(list(project_validator.iter_errors(config)))

    def test_project_and_release_schemas_agree_with_v1_v2_packages(self):
        ds = json.loads((ROOT / 'schemas/deployment.schema.json').read_text())
        rs = json.loads((ROOT / 'schemas/release.schema.json').read_text())
        for schema in (ds, rs): Draft202012Validator.check_schema(schema)
        registry = Registry().with_resource('deployment.schema.json', Resource.from_contents(ds))
        project_validator = Draft202012Validator(ds)
        release_validator = Draft202012Validator(rs, registry=registry)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);(root / 'pre.sh').write_text('true\n')
            for schema_version in (1, 2):
                config = copy.deepcopy(CONFIG)
                if schema_version == 2: config['hooks'] = {'pre_install': {'script': 'pre.sh'}}
                project_validator.validate(config)
                archive = build_release(config, IMAGE, f'v1.0.{schema_version}', root / 'packages', project_root=root)
                release = unpack_release(archive, root / f'r{schema_version}')
                release_validator.validate(release)
                bad = copy.deepcopy(release)
                bad['minimum_deployctl_version'] = '1.5.0' if schema_version == 1 else '1.0.0'
                self.assertTrue(list(release_validator.iter_errors(bad)))
                if schema_version == 2:
                    for change in ({'path': 'other.sh'}, {'timeout_seconds': True}, {'sha256': 'a' * 64 + '\n'}):
                        bad = copy.deepcopy(release);bad['hooks']['pre_install'].update(change)
                        self.assertTrue(list(release_validator.iter_errors(bad)))
        for script in ('../bad', '/bad', 'dir/../bad', 'dir\\bad', 'a\u2028b'):
            bad = copy.deepcopy(CONFIG);bad['hooks'] = {'pre_install': {'script': script}}
            self.assertTrue(list(project_validator.iter_errors(bad)))


if __name__ == '__main__': unittest.main()
