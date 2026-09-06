"""Permanent synthetic regression tests for the read-only review entry."""
import json
import tempfile
import unittest
from pathlib import Path
from translation_result import query_results, TranslationResultError


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'result.json'
        self.item = {'translation_unit_id': 'xml-index:0', 'source': 'Whiterun ' + 'x' * 400,
                     'translation': '白漫', 'status': 'TRANSLATED', 'rec': 'DIAL:FULL'}
        self.path.write_text(json.dumps({'translations': [self.item]}), encoding='utf-8')
        self.original = self.path.read_bytes()
        self.report = {'fails': [], 'warnings': [{'translation_unit_id': 'xml-index:0', 'term_id': 'whiterun'}]}

    def test_complete_text_and_read_only(self):
        out = query_results([self.path], text='WHITERUN', rec='DIAL:FULL')
        self.assertEqual(out['rows'][0]['item'], self.item)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_pagination(self):
        out = query_results([self.path], offset=1)
        self.assertEqual(out['total_matches'], 1)
        self.assertEqual(out['returned'], 0)
        self.assertFalse(out['has_more'])

    def test_term_source_not_english(self):
        out = query_results([self.path], report=self.report,
                            contract={'terms': {'whiterun': {'source': 'Whiterun', 'target': '白漫'}}})
        self.assertEqual(out['rows'][0]['term_definition']['source'], 'Whiterun')

    def test_missing_term_is_error(self):
        with self.assertRaises(TranslationResultError):
            query_results([self.path], report=self.report, contract={'terms': {}})

    def test_bad_report_is_error(self):
        with self.assertRaises(TranslationResultError):
            query_results([self.path], report={'issues': []})

    def test_missing_identity_is_error(self):
        self.report['warnings'][0]['translation_unit_id'] = 'absent'
        with self.assertRaises(TranslationResultError):
            query_results([self.path], report=self.report)

    def test_duplicate_files_are_error(self):
        with self.assertRaises(TranslationResultError):
            query_results([self.path, self.path])

    def test_ambiguous_identity_is_error(self):
        other = Path(self.tmp.name) / 'other.json'
        other.write_bytes(self.original)
        with self.assertRaises(TranslationResultError):
            query_results([self.path, other], report=self.report)

    def test_negative_limit_is_error(self):
        with self.assertRaises(TranslationResultError):
            query_results([self.path], limit=-1)


if __name__ == '__main__':
    unittest.main()
