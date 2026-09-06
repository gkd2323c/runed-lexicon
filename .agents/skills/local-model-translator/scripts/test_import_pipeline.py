"""Offline CLI integration tests for import-map -> executor filler."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(__file__).with_name('ollama_translate.py')
FILLER = Path(__file__).resolve().parents[2] / 'translation-executor/scripts/fill_translations.py'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = self.save('request.json', {'schema_version': 1, 'task_id': 'test',
            'items': [{'id': 'w', 'source': 'Hello %s'}]})
        self.response = self.save('response.json', {'task_id': 'test',
            'raw_output': '[w] 你好 %s', 'metrics': {'done_reason': 'stop'},
            'validation': {'ok': True},
            'translations': [{'id': 'w', 'translation': '你好 %s'}]})
        self.result = self.save('result.json', {'translations': [
            {'translation_unit_id': 'u', 'xml_index': 0, 'source': 'Hello %s',
             'translation': '', 'status': 'PENDING'},
            {'translation_unit_id': 'other', 'xml_index': 1, 'source': 'Other',
             'translation': '保留', 'status': 'TRANSLATED'}]})
        self.bindings = self.save('bindings.json', {'w': 'u'})
        self.output = self.root / 'map.json'

    def save(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def execute(self, *args):
        return subprocess.run([sys.executable, *map(str, args)], capture_output=True)

    def import_map(self):
        return self.execute(WORKER, 'import-map', self.response, '--request', self.request,
                            '--result', self.result, '--bindings', self.bindings, '--output', self.output)

    def fill(self):
        return self.execute(FILLER, '--result', self.result, '--map', self.output,
                            '--output', self.result, '--force')

    def test_pipeline_preserves_unmapped_and_review(self):
        original = self.result.read_bytes()
        self.assertEqual(self.import_map().returncode, 0)
        self.assertEqual(self.result.read_bytes(), original)
        self.assertEqual(self.fill().returncode, 0)
        items = json.loads(self.result.read_text(encoding='utf-8'))['translations']
        self.assertEqual(items[0]['status'], 'REVIEW')
        self.assertEqual(items[0]['translation'], '你好 %s')
        self.assertEqual(items[1], json.loads(original)['translations'][1])

    def test_existing_map_never_overwritten(self):
        self.output.write_bytes(b'original')
        original = self.result.read_bytes()
        self.assertNotEqual(self.import_map().returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'original')
        self.assertEqual(self.result.read_bytes(), original)

    def test_failed_validation_creates_no_map(self):
        self.save('response.json', {'task_id': 'test', 'raw_output': '[w] broken',
            'metrics': {'done_reason': 'stop'}, 'validation': {'ok': True},
            'translations': [{'id': 'w', 'translation': 'broken'}]})
        original = self.result.read_bytes()
        self.assertNotEqual(self.import_map().returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.result.read_bytes(), original)

    def test_stale_pending_translation_refuses_fill(self):
        self.assertEqual(self.import_map().returncode, 0)
        data = json.loads(self.result.read_text(encoding='utf-8'))
        data['translations'][0]['translation'] = 'concurrent change'
        self.save('result.json', data)
        before = self.result.read_bytes()
        self.assertEqual(self.fill().returncode, 2)
        self.assertEqual(self.result.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
