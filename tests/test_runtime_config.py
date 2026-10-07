import json
import importlib.util
from pathlib import Path
import tempfile
import unittest


class RuntimeConfigTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('deployctl.runtime_config'), 'runtime configuration is not implemented')
        from deployctl import runtime_config
        return runtime_config

    def test_assignments_preserve_literal_values(self):
        literal = ' a,b "quoted" $literal #tag = 中文 '
        result = self.api().parse_assignments(['EMPTY=', 'TEXT=' + literal], 'runtime env')
        self.assertEqual(result, {'EMPTY': '', 'TEXT': literal})
        self.assertEqual(json.loads(self.api().render_json(result)), result)
        self.assertEqual(self.api().render_raw_env(result), 'EMPTY=\nTEXT=' + literal + '\n')

    def test_duplicate_or_bare_key_rejected(self):
        for items in (['A=x', 'A=y'], ['A'], ['bad-name=x'], ['=x']):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.api().parse_assignments(items, 'runtime env')

    def test_counts_names_value_and_utf8_limits(self):
        api = self.api()
        self.assertEqual(len(api.parse_assignments([f'K{i}=' for i in range(128)], 'runtime env')), 128)
        self.assertEqual(api.parse_assignments(['A' * 128 + '=x'], 'runtime env')['A' * 128], 'x')
        self.assertEqual(len(api.parse_assignments(['A=' + 'x' * 4096], 'runtime env')['A']), 4096)
        # Sixteen explicit lines total exactly 65536 UTF-8 bytes including LF.
        boundary = [f'K{i:02d}=' + 'x' * 4091 for i in range(16)]
        self.assertEqual(len(api.parse_assignments(boundary, 'runtime env')), 16)
        invalid = [
            [f'K{i}=' for i in range(129)], ['A' * 129 + '=x'], ['A=' + 'x' * 4097],
            boundary[:-1] + [boundary[-1] + 'x'],
            [f'K{i}=' + '中' * 2000 for i in range(12)],
        ]
        for items in invalid:
            with self.subTest(size=len(items)), self.assertRaises(ValueError):
                api.parse_assignments(items, 'runtime env')

    def test_controls_and_platform_names_rejected_without_echoing_values(self):
        api = self.api()
        for value in ('x\ny', 'x\ry', 'x\x00y', 'x\x7fy', 'x\x85y', 'x\u2028y', 'x\u2029y'):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError) as error:
                api.parse_assignments(['KEY=' + value], 'runtime env')
            self.assertNotIn(value, str(error.exception))
        for name in ('APP_VERSION', 'DEPLOYCTL_ENV_FILE'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.api().parse_assignments([name + '=secret-sentinel'], 'runtime env')
        self.assertEqual(self.api().parse_assignments(['APP_VERSION=x'], 'install params', False),
                         {'APP_VERSION': 'x'})

    def test_precedence_persistence_and_unset(self):
        api = self.api()
        old = {'A': 'old', 'EMPTY': ''}
        values, overrides = api.merge_runtime_values({'A': 'base'}, {'A': 'secret'}, old,
                                                     {'A': 'new'}, [], 'v2.0.0')
        self.assertEqual(values, {'A': 'new', 'EMPTY': '', 'APP_VERSION': 'v2.0.0'})
        self.assertEqual(overrides, {'A': 'new', 'EMPTY': ''})
        self.assertEqual(old, {'A': 'old', 'EMPTY': ''})
        values, overrides = api.merge_runtime_values({'A': 'base'}, {'A': 'secret'}, old,
                                                     {}, ['A'], 'v2.1.0')
        self.assertEqual(values['A'], 'secret')
        self.assertEqual(overrides, {'EMPTY': ''})
        values, _ = api.merge_runtime_values({}, {}, {}, {'ONLY': 'x'}, [], 'v1.0.0')
        self.assertEqual(values['ONLY'], 'x')

    def test_overlap_duplicate_unset_and_merged_limits_rejected(self):
        api = self.api()
        for updates, unset in (({'A': 'x'}, ['A']), ({}, ['A', 'A']), ({}, ['APP_VERSION']), ({}, ['bad'])):
            with self.subTest(updates=updates, unset=unset), self.assertRaises(ValueError):
                api.merge_runtime_values({}, {}, {}, updates, unset, 'v1.0.0')
        with self.assertRaises(ValueError):
            api.merge_runtime_values({f'K{i}': '' for i in range(128)}, {}, {}, {'EXTRA': ''}, [], 'v1.0.0')
        with self.assertRaises(ValueError):
            api.merge_runtime_values({'APP_VERSION': 'x'}, {}, {}, {}, [], 'v1.0.0')

    def test_raw_files_preserve_values_and_reject_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.env'
            path.write_text('# comment\n\nA= x$literal#tag "quoted" = \nEMPTY=\n', encoding='utf-8')
            self.assertEqual(self.api().read_raw_env(path), {'A': ' x$literal#tag "quoted" = ', 'EMPTY': ''})
            path.write_text('A=x\nA=y\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                self.api().read_raw_env(path)


if __name__ == '__main__':
    unittest.main()
