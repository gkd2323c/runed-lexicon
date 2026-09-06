"""CLI regression: stale corrections must leave the result unchanged."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class FillPreconditionTests(unittest.TestCase):
    def test_stale_and_matching_old_value(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.json'
            mapping = Path(directory) / 'map.json'
            result.write_text(json.dumps({'translations': [{'xml_index': 0, 'source': 'Hello',
                              'translation': 'old', 'status': 'TRANSLATED'}]}), encoding='utf-8')
            original = result.read_bytes()
            entry = {'translation': 'new', 'status': 'TRANSLATED', 'expected_translation': 'stale'}
            mapping.write_text(json.dumps({'0': entry}), encoding='utf-8')
            command = [sys.executable, str(Path(__file__).with_name('fill_translations.py')),
                       '--result', str(result), '--map', str(mapping), '--output', str(result),
                       '--force', '--overwrite']
            failed = subprocess.run(command, capture_output=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(result.read_bytes(), original)
            entry['expected_translation'] = 'old'
            mapping.write_text(json.dumps({'0': entry}), encoding='utf-8')
            passed = subprocess.run(command, capture_output=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(result.read_text())['translations'][0]['translation'], 'new')


if __name__ == '__main__':
    unittest.main()
