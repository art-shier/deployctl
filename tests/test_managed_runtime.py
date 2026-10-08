import copy
import json
from pathlib import Path
import unittest

from deployctl.release import build_release
from test_runtime_hooks import TransactionHooksTests
from test_release import CONFIG, IMAGE


class ManagedRuntimeTests(TransactionHooksTests):
    def test_managed_layer_survives_pre_refresh(self):
        config=copy.deepcopy(CONFIG)
        config['hooks']={'pre_install':{'script':'pre.sh','refresh_config':True}}
        archive=build_release(config,IMAGE,'v1.0.0',self.base/'packages',project_root=self.source)
        original=self.hooks.run
        def hook(*args):
            original(*args)
            (self.folder/'config.env').write_text('DATABASE_URL=pre\nTEXT=pre\n')
        self.hooks.run=hook
        self.manager.deploy('project-a','production',archive,runtime_env={'TEXT':'local'},install_params={'ADMIN':'cli'},
                            managed_runtime={'TEXT':'remote','DATABASE_URL':'managed'},managed_params={'ADMIN':'owner'})
        self.assertEqual(self.driver.values['TEXT'],'remote')
        self.assertEqual(self.driver.values['DATABASE_URL'],'managed')
        self.assertEqual(self.hooks.calls[0][2],{'ADMIN':'owner'})
        ref=self.state()['current']
        local=json.loads((self.folder/'runtime'/ref['configuration']/'overrides.json').read_text())
        self.assertEqual(local,{'TEXT':'local'})
        self.upgrade(self.package('v1.1.0'),managed_runtime={})
        self.assertEqual(self.driver.values['TEXT'],'local')

    def test_managed_resources_and_binding_rollback(self):
        source={'origin':'https://ctl.test','project':'project-a','environment':'production','release_id':'a'*32,'revision_id':'b'*32}
        self.install(management_source=source,deployment_defaults={'host_port':9000,'memory_limit':'256m','cpus':0.5})
        old=self.state()['current']
        self.assertEqual(old['binding']['port'],9000)
        self.upgrade(self.package('v1.1.0'),management_source={**source,'revision_id':'c'*32},deployment_defaults={'host_port':9001,'memory_limit':'512m'},port=9010)
        self.assertEqual(self.state()['current']['binding']['port'],9010)
        self.manager.rollback('project-a','production')
        self.assertEqual(self.state()['current'],old)
        from deployctl.contract import read_yaml
        compose=read_yaml(self.folder/'runtime'/old['configuration']/'compose.yaml')
        self.assertEqual(compose['services']['app']['mem_limit'],'256m')
        self.assertEqual(compose['services']['app']['cpus'],0.5)

if __name__ == '__main__': unittest.main()
