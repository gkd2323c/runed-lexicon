"""整句词条只认整句（v0.4.3）：`He did.` 不应命中 `...what he did.` 的尾部子串。

与 `test_case_sensitive.py` 同族：那是「大小写承载语义」，这是「词条本身是一整句」。
两者的共同点是把一条此前无法表达的匹配约束落到词表里，而不是改译文。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from term_match import (  # noqa: E402
    find_source_hit_spans,
    find_source_hits,
    is_standalone_sentence,
)


class TestIsStandaloneSentence(unittest.TestCase):
    """词条形状判定。"""

    def test_sentence_punctuation_counts(self):
        for eng in ('He did.', 'There it is.', 'Timing matters.', 'Unknown.', 'Really?'):
            self.assertTrue(is_standalone_sentence(eng), eng)

    def test_ordinary_terms_do_not(self):
        for eng in ('master', 'scene', 'transformation', 'movement', 'relief',
                    'Laelnoah', 'Red Drop'):
            self.assertFalse(is_standalone_sentence(eng), eng)

    def test_edge_cases(self):
        self.assertFalse(is_standalone_sentence(''))
        self.assertFalse(is_standalone_sentence(None))
        self.assertFalse(is_standalone_sentence('.'))      # 单字符不算一句
        self.assertFalse(is_standalone_sentence('  '))

    def test_surrounding_whitespace_ignored(self):
        self.assertTrue(is_standalone_sentence('  He did.  '))


class TestStandaloneMatchGuards(unittest.TestCase):
    """核心行为：整句词条只在源文整句就是它时命中。"""

    def test_substring_inside_longer_sentence_does_not_match(self):
        """真实事故：INFO-480 idx 33438。"""
        src = 'It does not rewrite what he did.'
        self.assertEqual(find_source_hit_spans(src, 'He did.'), [])

    def test_exact_standalone_sentence_matches(self):
        self.assertEqual(len(find_source_hit_spans('He did.', 'He did.')), 1)

    def test_case_insensitive_exact_still_matches(self):
        self.assertEqual(len(find_source_hit_spans('he did.', 'He did.')), 1)

    def test_quoted_and_padded_exact_still_matches(self):
        for src in ('"He did."', '“He did.”', '  He did.  ', '“He did.” '):
            self.assertEqual(len(find_source_hit_spans(src, 'He did.')), 1, src)

    def test_prefix_suffix_do_not_match(self):
        self.assertEqual(find_source_hit_spans('He did. And more.', 'He did.'), [])
        self.assertEqual(find_source_hit_spans('Well, he did.', 'He did.'), [])

    def test_non_sentence_terms_keep_loose_substring_matching(self):
        """回归护栏：普通词条行为逐字不变。"""
        self.assertEqual(len(find_source_hit_spans('the master returns', 'master')), 1)
        self.assertEqual(len(find_source_hit_spans('Athis one', 'Rathis')), 0)


class TestSubstringMatchEscapeHatch(unittest.TestCase):
    """缩写型词条可显式退回宽松匹配。"""

    def test_explicit_false_opts_back_in(self):
        spans = find_source_hit_spans('It does not rewrite what he did.',
                                      'He did.', standalone=False)
        self.assertEqual(len(spans), 1)

    def test_term_level_escape_hatch(self):
        term = {'source': 'He did.', 'substring_match': True}
        self.assertEqual(len(find_source_hits('It does not rewrite what he did.', term)), 1)

    def test_term_without_flag_keeps_standalone_rule(self):
        term = {'source': 'He did.'}
        self.assertEqual(find_source_hits('It does not rewrite what he did.', term), [])


class TestInteractionWithCaseSensitive(unittest.TestCase):
    """两条规则同时生效，互不污染。"""

    def test_case_sensitive_standalone(self):
        self.assertEqual(len(find_source_hit_spans('He did.', 'He did.',
                                                   case_sensitive=True)), 1)
        self.assertEqual(find_source_hit_spans('he did.', 'He did.',
                                               case_sensitive=True), [])

    def test_explicit_true_requires_full_coverage(self):
        """`standalone=True` 的语义就是「必须覆盖整句」，与词条形状无关。"""
        self.assertEqual(find_source_hit_spans('the master returns', 'master',
                                               standalone=True), [])
        self.assertEqual(len(find_source_hit_spans('master', 'master',
                                                   standalone=True)), 1)


if __name__ == '__main__':
    unittest.main()
