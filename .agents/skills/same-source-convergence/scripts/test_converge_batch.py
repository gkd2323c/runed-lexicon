# -*- coding: utf-8 -*-
"""converge_batch.py 的单元测试（unittest 风格，CI 的 discover 会自动收）。

只测纯函数，不依赖任何 MOD 数据。字面中文直写，不手算 \\uXXXX。
用法：py -3 -m unittest discover -s .agents/skills/same-source-convergence/scripts -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import converge_batch as cb  # noqa: E402


def term(tid, source, target, accepted=None, enforcement='REQUIRED', forbidden=None):
    return {
        'term_id': tid, 'source': source, 'target': target,
        'enforcement': enforcement, 'forbidden': forbidden or [],
        'match': {'kind': 'contains_phrase', 'accepted': accepted or [target]},
    }


class TestContractCheck(unittest.TestCase):
    def setUp(self):
        self.cidx = cb.contract_index([
            term('harvest', 'harvest', '收成'),
            term('distinction', 'distinction', '这重区别'),
            term('maker', 'maker', '造物者', forbidden=['造物主', '制造者']),
            term('advisory', 'advisory', '忠告', enforcement='ADVISORY'),
        ])

    def test_index_is_lowercased(self):
        self.assertIn('harvest', self.cidx)
        self.assertEqual(self.cidx['harvest'][0]['target'], '收成')

    def test_missing_required_target_warns(self):
        out = []
        hard = cb.check_contract(
            'Boltzmer sees the beginning of a harvest.', '波尔茨默看见一场收获的开端。',
            self.cidx, out, strict=False)
        self.assertFalse(hard, '非 strict 模式只告警，不中止')
        self.assertTrue(any('CONTRACT_MISSING' in m for m in out), out)
        self.assertTrue(any('harvest' in m for m in out), out)

    def test_missing_required_target_blocks_under_strict(self):
        out = []
        hard = cb.check_contract(
            'Keep the distinction in mind.', '记住这区别就行。',
            self.cidx, out, strict=True)
        self.assertTrue(hard)
        self.assertTrue(any('CONTRACT_MISSING' in m for m in out), out)

    def test_satisfied_required_target_is_quiet(self):
        out = []
        hard = cb.check_contract(
            'Because the experiment notices such inheritances of harvest.',
            '因为实验留意这类收成的继承。', self.cidx, out, strict=True)
        self.assertFalse(hard, out)
        self.assertEqual(out, [])

    def test_forbidden_form_always_blocks(self):
        out = []
        hard = cb.check_contract(
            'That is the language of makers.', '那是制造者的语言。',
            self.cidx, out, strict=False)
        self.assertTrue(hard, '禁形即使非 strict 也要中止')
        self.assertTrue(any('CONTRACT_FORBIDDEN' in m for m in out), out)

    def test_advisory_enforcement_never_warns(self):
        out = []
        hard = cb.check_contract(
            'An advisory body meets.', '一个顾问机构开会。',
            self.cidx, out, strict=True)
        self.assertFalse(hard, out)
        self.assertEqual(out, [])

    def test_word_boundary_respected(self):
        """harvest 不应被 harvest-like / reharvesting 之类误触发。"""
        out = []
        cb.check_contract('The reharvesting was thorough.', '那次再收割做得很彻底。',
                          self.cidx, out, strict=True)
        self.assertEqual(out, [], '子串不应触发词条：%s' % out)

    def test_inflected_source_still_matches_contract(self):
        """契约写 harvest（词元），源句写 harvests（屈折形）——必须仍然触发。"""
        out = []
        hard = cb.check_contract(
            'Each harvest matters.', '每一次收获都要紧。',
            self.cidx, out, strict=False)
        self.assertTrue(any('CONTRACT_MISSING' in m and 'harvest' in m for m in out),
                        '屈折形未触发契约核对：%s' % out)

    def test_plural_forbidden_form_blocks(self):
        """源句 makers（复数）对上契约 maker（单数），禁形必须生效。"""
        out = []
        hard = cb.check_contract(
            'That is the language of makers.', '那是制造者的语言。',
            self.cidx, out, strict=False)
        self.assertTrue(hard, '复数源词未触发禁形检查：%s' % out)
        self.assertTrue(any('CONTRACT_FORBIDDEN' in m for m in out), out)

    def test_past_tense_matches(self):
        out = []
        cb.check_contract('He harvested nothing.', '他什么也没收成。',
                         self.cidx, out, strict=True)
        self.assertEqual(out, [], '过去式已满足契约，不该报：%s' % out)

    def test_gerund_matches(self):
        out = []
        cb.check_contract('She was harvesting.', '她当时在收割。',
                         self.cidx, out, strict=True)
        self.assertTrue(any('CONTRACT_MISSING' in m for m in out), out)

    def test_empty_contract_is_harmless(self):
        out = []
        hard = cb.check_contract('Any sentence.', '任何一句。',
                                 cb.contract_index([]), out, strict=True)
        self.assertFalse(hard)
        self.assertEqual(out, [])


if __name__ == '__main__':
    unittest.main()
