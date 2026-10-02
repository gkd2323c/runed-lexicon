# -*- coding: utf-8 -*-
"""adjudicate.py 的契约尺回归。

真事故：compiled.json 的 terms 以 term_id 为 key（kalpic.glen.distinction）、
源词形在 source 字段，而 cmd_contract 只按 key 查 —— 于是对**任何**源词都报
「契约无此词条」。distinction / transformation 这两个恰恰是「契约 target 优先于
多数形」时最该被看见的词，却一直查不到。
"""
import unittest

import adjudicate as adj


def terms_fixture():
    return {
        'kalpic.glen.distinction': {
            'term_id': 'kalpic.glen.distinction',
            'source': 'distinction',
            'target': '这重区别',
            'enforcement': 'REQUIRED',
            'match': {'kind': 'contains_phrase', 'accepted': ['这重区别']},
        },
        'kalpic.glen.transformation': {
            'term_id': 'kalpic.glen.transformation',
            'source': 'transformation',
            'target': '转化',
            'enforcement': 'REQUIRED',
            'match': {'kind': 'contains_phrase', 'accepted': ['转化']},
        },
    }


class SourceIndexTest(unittest.TestCase):
    def test_key_is_term_id_not_source(self):
        """回归点：key 形如 kalpic.glen.distinction，与源词形不同。"""
        terms = terms_fixture()
        self.assertNotIn('distinction', terms)
        self.assertIn('kalpic.glen.distinction', terms)

    def test_index_hits_by_source(self):
        idx = adj.source_index(terms_fixture())
        self.assertIn('distinction', idx)
        k, v = idx['distinction']
        self.assertEqual(k, 'kalpic.glen.distinction')
        self.assertEqual(v['target'], '这重区别')

    def test_index_is_case_insensitive(self):
        terms = {'t1': {'source': 'Distinction', 'target': 'x'}}
        self.assertIn('distinction', adj.source_index(terms))

    def test_index_skips_non_dict_and_blank_source(self):
        terms = {'t1': 'not-a-dict', 't2': {'source': '  '}, 't3': {'source': 'vestige'}}
        idx = adj.source_index(terms)
        self.assertEqual(list(idx), ['vestige'])

    def test_unregistered_word_is_absent(self):
        idx = adj.source_index(terms_fixture())
        for w in ('truly', 'consent', 'burden'):
            self.assertNotIn(w, idx)


class StemCandidateTest(unittest.TestCase):
    def test_plural_keeps_lemma(self):
        cands = adj.stem_candidates('distinctions')
        self.assertIn('distinction', cands)

    def test_past_tense_keeps_lemma(self):
        cands = adj.stem_candidates('transformed')
        self.assertIn('transform', cands)

    def test_doubled_consonant_stemmed(self):
        cands = adj.stem_candidates('burden')
        self.assertIn('burd', cands)


if __name__ == '__main__':
    unittest.main()
