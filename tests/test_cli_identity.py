from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch
from deployctl.cli import main
from deployctl.platform_credentials import Credentials

class IdentityCLITests(unittest.TestCase):
    def test_whoami_exposes_scope_without_credentials(self):
        identity={'schema_version':1,'id':'fixture','role':'deployer','project':'','projects':['notes','config'],'groups':['default'],'environments':['prod'],'token':'must-not-print-fixture-secret'}
        output=io.StringIO()
        with patch('deployctl.platform_credentials.Credentials.load',return_value=Credentials('https://ctl.test','must-not-print-fixture-secret')),patch('deployctl.platform_client.PlatformClient.json',return_value=identity),redirect_stdout(output):
            result=main(['whoami'])
        self.assertEqual(result,0)
        value=json.loads(output.getvalue());self.assertEqual(value['groups'],['default']);self.assertEqual(value['projects'],['notes','config'])
        self.assertNotIn('must-not-print',output.getvalue())

    def test_projects_uses_current_login_and_only_public_summary(self):
        output=io.StringIO()
        data=[{'slug':'notes','name':'Notes','group':'default','default_environment':'prod','irrelevant_secret':'must-not-print'}]
        with patch('deployctl.platform_credentials.Credentials.load',return_value=Credentials('https://ctl.test','fixture-token-long-enough')),patch('deployctl.platform_client.PlatformClient.json',return_value=data) as query,redirect_stdout(output):
            result=main(['projects','--client-config','/private/shared.json'])
        self.assertEqual(result,0);self.assertEqual(json.loads(output.getvalue())[0]['group'],'default');self.assertNotIn('must-not-print',output.getvalue())
        query.assert_called_once_with('GET','/api/v1/projects')
