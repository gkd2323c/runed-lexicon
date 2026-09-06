"""Regression: no partial publication for multi-file corrections."""
import json
import tempfile
import unittest
from pathlib import Path
from fill_translation_set import fill_set, SetFillError


class SetTests(unittest.TestCase):
    def test_failure_then_success_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            before = {}
            for i in range(2):
                result = root / f'r{i}.json'
                result.write_text(json.dumps({'translations': [{'xml_index': i, 'source': 'Hello',
                    'translation': 'old', 'status': 'TRANSLATED'}]}), encoding='utf-8')
                before[result] = result.read_bytes()
                patch = {str(i): {'translation': 'new', 'status': 'TRANSLATED',
                                  'expected_translation': 'old' if i == 0 else 'wrong'}}
                (root / f'm{i}.json').write_text(json.dumps(patch), encoding='utf-8')
                files.append({'result': f'r{i}.json', 'map': f'm{i}.json',
                              'output_name': f'r{i}.json', 'overwrite': True})
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema_version': 1, 'files': files}), encoding='utf-8')
            output = root / 'output'
            with self.assertRaises(SetFillError): fill_set(manifest, output)
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('.fill-set-*')))
            for path, data in before.items(): self.assertEqual(path.read_bytes(), data)
            patch['1']['expected_translation'] = 'old'
            (root / 'm1.json').write_text(json.dumps(patch), encoding='utf-8')
            self.assertEqual(fill_set(manifest, output), 2)
            for path, data in before.items(): self.assertEqual(path.read_bytes(), data)
            self.assertEqual(len(list(output.glob('*.json'))), 2)
            with self.assertRaises(SetFillError): fill_set(manifest, output)

    def test_reject_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema_version': 1, 'files': [
                {'result': 'r', 'map': 'm', 'output_name': '../escape.json'}]}))
            with self.assertRaises(SetFillError): fill_set(manifest, root / 'output')


if __name__ == '__main__':
    unittest.main()
