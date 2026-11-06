import unittest
from deployctl.platform_management import metadata, changes_payload

class StaticManagementTests(unittest.TestCase):
    def test_static_metadata_without_registry(self):
        value = {'slug':'project-a','group':'default','name':'A','description':'','repository':'',
                 'image_repository':'','default_environment':'prod','deployment_type':'static'}
        self.assertEqual(metadata(value,'project')['deployment_type'],'static')
        value['deployment_type']='docker'
        with self.assertRaises(ValueError): metadata(value,'project')

    def test_group_and_project_target_defaults(self):
        for scope in ('group','project'):
            self.assertEqual(changes_payload({'deployment_defaults':{'target_dir':'/var/www/a'}},scope)['deployment_defaults'],{'target_dir':'/var/www/a'})
            with self.assertRaises(ValueError): changes_payload({'deployment_defaults':{'target_dir':'/'}},scope)
