import copy
import unittest


class StateTests(unittest.TestCase):
    def test_legacy_normalization_is_read_only_and_ownership_checked(self):
        from deployctl.deployment_state import normalize_state
        data = {'schema_version': 1, 'application': 'a', 'environment': 'prod', 'current': 'v1.0.0',
                'previous': None, 'transaction': None, 'binding': {'address': '127.0.0.1', 'port': 80}}
        original = copy.deepcopy(data)
        state = normalize_state(data, 'a', 'prod')
        self.assertEqual(data, original)
        self.assertTrue(state['current']['legacy'])
        with self.assertRaises(ValueError): normalize_state(data, 'b', 'prod')

    def test_invalid_ref_and_transaction_shapes_rejected(self):
        from deployctl.deployment_state import normalize_state
        good = {'schema_version': 2, 'application': 'a', 'environment': 'prod', 'current': {
            'version': 'v1.0.0', 'configuration': 'a' * 32, 'configuration_sha256': 'b' * 64,
            'binding': {'address': '127.0.0.1', 'port': 80}, 'legacy': False},
            'previous': None, 'transaction': None, 'binding': {'address': '127.0.0.1', 'port': 80}}
        normalize_state(good, 'a', 'prod')
        for key, value in (('configuration', '../escape'), ('configuration_sha256', 'bad'),
                           ('legacy', 1), ('binding', {'port': True, 'address': '127.0.0.1'})):
            bad = copy.deepcopy(good);bad['current'][key] = value
            with self.assertRaises(ValueError): normalize_state(bad, 'a', 'prod')
        bad = copy.deepcopy(good);bad['transaction'] = {'from': None, 'to': good['current'], 'phase': 'arbitrary'}
        with self.assertRaises(ValueError): normalize_state(bad, 'a', 'prod')

    def test_legacy_environment_selects_actual_config_and_image_differences(self):
        from deployctl.deployment_state import collect_legacy_values
        result = collect_legacy_values(['PATH=/bin', 'A=actual', 'B=image', 'C=extra', 'APP_VERSION=v1.0.0'],
                                       ['PATH=/bin', 'B=image'], {'A', 'B'}, 'v1.0.0')
        self.assertEqual(result, {'A': 'actual', 'B': 'image', 'C': 'extra', 'APP_VERSION': 'v1.0.0'})


if __name__ == '__main__': unittest.main()
