"""Permanent regression tests: worker imports must not trust saved validation flags."""
import copy
import unittest
from ollama_translate import prepare_import_map, RequestError


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.request = {'schema_version': 1, 'task_id': 'test', 'background': 'test',
                        'items': [{'id': 'w1', 'source': 'Hello %s'}]}
        self.response = {'task_id': 'test', 'raw_output': '[w1] 你好 %s',
                         'metrics': {'done_reason': 'stop'}, 'validation': {'ok': True},
                         'translations': [{'id': 'w1', 'translation': '你好 %s'}]}
        self.result = {'translations': [{'translation_unit_id': 'u1', 'xml_index': 0,
                                        'source': 'Hello %s', 'translation': '', 'status': 'PENDING'}]}
        self.bindings = {'w1': 'u1'}

    def run_import(self):
        return prepare_import_map(self.request, self.response, self.result, self.bindings)

    def test_review_only_no_mutation(self):
        before = copy.deepcopy(self.result)
        self.response['validation']['ok'] = False
        self.assertEqual(self.run_import()['0']['status'], 'REVIEW')
        self.assertEqual(before, self.result)

    def test_forged_pass_rejected(self):
        self.response['raw_output'] = '[w1] 你好'
        self.response['translations'][0]['translation'] = '你好'
        with self.assertRaises(RequestError): self.run_import()

    def test_repaired_disagreement_rejected(self):
        self.response['translations'][0]['translation'] = '欢迎 %s'
        with self.assertRaises(RequestError): self.run_import()

    def test_truncation_rejected(self):
        self.response['metrics']['done_reason'] = 'length'
        with self.assertRaises(RequestError): self.run_import()

    def test_missing_raw_rejected(self):
        del self.response['raw_output']
        with self.assertRaises(RequestError): self.run_import()

    def test_wrong_source_rejected(self):
        self.result['translations'][0]['source'] = 'Other'
        with self.assertRaises(RequestError): self.run_import()

    def test_completed_item_rejected(self):
        self.result['translations'][0]['status'] = 'TRANSLATED'
        with self.assertRaises(RequestError): self.run_import()

    def test_missing_binding_rejected(self):
        self.bindings = {}
        with self.assertRaises(RequestError): self.run_import()

    def test_duplicate_result_rejected(self):
        self.result['translations'].append(copy.deepcopy(self.result['translations'][0]))
        with self.assertRaises(RequestError): self.run_import()


if __name__ == '__main__':
    unittest.main()
