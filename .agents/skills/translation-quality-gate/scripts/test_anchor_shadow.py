"""短锚被长专名覆盖时的锚点豁免（R21）单元测试。

锁定的事故（TheKalpicAnomaly_GLENMORIL）：契约里 `the Eyes`（裸形，译「眼线」）与
`the Eyes of Hinnom`（实体专名，译「欣嫩之眼」）是两条严格分立的词条。源文
`Is the Eyes of Hinnom here with me now?` 只含全形专名，译文「欣嫩之眼」完全正确，
但整词边界匹配认为 `the Eyes` 出现了，于是报
`TERM001 WARN: required target 未出现: '眼线'`——纯假阳性，实测 6 行
（2869 / 2870 / 13102 / 14343 / 29180 / 31831）。

修法：短锚的某次命中若**完整落在**某个更长的、契约里已登记的专名锚点区间内，该次
命中不算短锚的有效出现。规则从契约已登记词条推导，检查期计算，不需要重编译契约，
也不硬编码任何专名列表。

最容易做错的地方是**漏报**：同一句里裸形与全形并存时，裸形那次必须照常触发。
下面 `test_bare_short_anchor_alongside_long_name_still_triggers` 专门锁这条。

Run:  python -m unittest discover -s .agents/skills/translation-quality-gate/scripts -p "test_*.py"
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quality_gate import _resolve_auto, run_gate, standalone_forbidden_issues  # noqa: E402
from term_match import (find_source_hit_spans, is_shadowed, maximal_spans,  # noqa: E402
                        resolve_global_bans, find_global_ban_hits, cross_target_covered)


def _term(source, target, **kw):
    """A no-risk REQUIRED term, i.e. one that IS an auto-bind candidate."""
    t = {
        'term_id': source.lower().replace(' ', '-'),
        'source': source,
        'target': target,
        'enforcement': 'REQUIRED',
        'risk_flags': [],
        'forbidden': [],
        'match': {'kind': 'contains_phrase', 'accepted': [target]},
    }
    t.update(kw)
    return t


def _contract(terms):
    return {'schema_version': 1, 'terms': {t['term_id']: t for t in terms}}


def _codes(issues):
    return sorted(i['code'] for i in issues)


class AnchorSpanMathTest(unittest.TestCase):
    """区间工具本身：只认「完整包含」，不认相邻、不认共享前缀。"""

    def test_spans_are_reported_in_stripped_source_coordinates(self):
        src = 'Is the Eyes of Hinnom here with me now?'
        self.assertEqual(find_source_hit_spans(src, 'the Eyes'), [(3, 11)])

    def test_whole_word_guard_is_preserved(self):
        """R6 护栏不能被 R21 改坏：子串不算命中。"""
        self.assertEqual(find_source_hit_spans('A Rathis walks.', 'Rathis'), [(2, 8)])
        # "Athis" 里不含独立出现的 Rathis（事故原例：字面量反向子串）
        self.assertEqual(find_source_hit_spans('An Athis walks.', 'Rathis'), [])
        # 词尾粘连也不算（Imperial ⊅ Ria）
        self.assertEqual(find_source_hit_spans('An Imperial walks.', 'Ria'), [])

    def test_html_tags_are_stripped_before_matching(self):
        src = '<font face="Adielle">Hi</font>'
        self.assertEqual(find_source_hit_spans(src, 'Adielle'), [])

    def test_maximal_spans_drops_nested_spans(self):
        self.assertEqual(maximal_spans([(0, 5), (2, 9), (2, 5)]), [(0, 5), (2, 9)])

    def test_is_shadowed_requires_strict_containment(self):
        # 完全包含 -> 被覆盖
        self.assertTrue(is_shadowed((4, 8), [(0, 12)]))
        # 同一区间不算（不是更长锚点）
        self.assertFalse(is_shadowed((0, 5), [(0, 5)]))
        # 部分重叠不算
        self.assertFalse(is_shadowed((4, 9), [(0, 6)]))
        # 相邻不算
        self.assertFalse(is_shadowed((6, 9), [(0, 5)]))


class ShortAnchorShadowedByLongNameTest(unittest.TestCase):
    """核心：短锚只出现在长专名内部 -> 不绑定、不报 TERM001。"""

    def setUp(self):
        self.terms = [
            _term('the Eyes', '眼线'),
            _term('the Eyes of Hinnom', '欣嫩之眼'),
        ]
        self.contract = _contract(self.terms)
        self.index = {t['term_id']: t for t in self.terms}

    def _bound(self, src):
        return [rt.term_id for rt in _resolve_auto(self.index, src)]

    def test_short_anchor_inside_long_name_is_not_bound(self):
        src = 'Is the Eyes of Hinnom here with me now?'
        self.assertNotIn('the-eyes', self._bound(src))

    def test_correct_translation_raises_no_term001(self):
        """事故行本身：译文正确，门禁必须干净。"""
        from term_match import check_unit
        src = 'Is the Eyes of Hinnom here with me now?'
        dst = '欣嫩之眼现在就在我身边吗？'
        resolved = _resolve_auto(self.index, src)
        self.assertEqual([rt.term_id for rt in resolved], ['the-eyes-of-hinnom'])
        self.assertEqual(check_unit(src, dst, resolved, self.index), [])

    def test_bare_short_anchor_independently_still_triggers(self):
        """无长锚时保持原行为（不能把规则做成「永远豁免 the Eyes」）。"""
        src = 'The Eyes watch the walls at night.'
        self.assertIn('the-eyes', self._bound(src))
        from term_match import check_unit
        resolved = _resolve_auto(self.index, src)
        issues = check_unit(src, '他们监视夜里的城墙。', resolved, self.index)
        self.assertIn('TERM001', _codes(issues))

    def test_bare_short_anchor_alongside_long_name_still_triggers(self):
        """**防漏报**：同句内裸形与全形并存时，裸形那次仍必须绑定并触发。"""
        src = 'The Eyes of Hinnom watch, and the Eyes report to Ja\'zel.'
        self.assertIn('the-eyes', self._bound(src))
        self.assertIn('the-eyes-of-hinnom', self._bound(src))
        from term_match import check_unit
        resolved = _resolve_auto(self.index, src)
        # 译文只给了全形、漏掉裸形 -> the-eyes 仍须报
        issues = check_unit(src, '欣嫩之眼在监视，而他们向贾’泽尔复命。', resolved, self.index)
        term001 = [i for i in issues if i['code'] == 'TERM001']
        self.assertEqual([i['term_id'] for i in term001], ['the-eyes'])

    def test_two_occurrences_one_shadowed_one_not(self):
        """同一行两次裸形锚点：被长专名吞掉的那次豁免，独立的那次照常。"""
        src = 'The Eyes of Hinnom came, and later the Eyes withdrew.'
        self.assertIn('the-eyes', self._bound(src))


class SuffixContinuationShadowTest(unittest.TestCase):
    """同类形态：短锚后紧跟专名延续（带 of / the 的全形专名）。

    契约登记全形 `the Order of the Silver Hand` 时，裸形 `the order` 落在其内部，
    不应再要求「组织」；全形自己的检查照常。
    """

    def setUp(self):
        self.terms = [
            _term('the order', '组织'),
            _term('the Order of the Silver Hand', '银手团'),
        ]
        self.index = {t['term_id']: t for t in self.terms}

    def _bound(self, src):
        return [rt.term_id for rt in _resolve_auto(self.index, src)]

    def test_short_anchor_before_of_continuation_is_shadowed(self):
        src = 'He serves the Order of the Silver Hand now.'
        self.assertNotIn('the-order', self._bound(src))
        self.assertIn('the-order-of-the-silver-hand', self._bound(src))

    def test_bare_short_anchor_without_long_name_still_binds(self):
        src = 'He serves the order now.'
        self.assertIn('the-order', self._bound(src))

    def test_long_name_still_checked_on_its_own_terms(self):
        """豁免短锚不等于放过长锚：长锚的 required target 缺失仍要报。"""
        from term_match import check_unit
        src = 'He serves the Order of the Silver Hand now.'
        resolved = _resolve_auto(self.index, src)
        issues = check_unit(src, '他现在效忠于某个团体。', resolved, self.index)
        self.assertIn('the-order-of-the-silver-hand',
                      [i['term_id'] for i in issues if i['code'] == 'TERM001'])


class NoLongAnchorRegisteredTest(unittest.TestCase):
    """契约里没有对应长锚时，行为与修复前逐字一致。"""

    def test_anchor_without_longer_registered_term_is_untouched(self):
        terms = [_term('the Eyes', '眼线')]
        index = {t['term_id']: t for t in terms}
        for src in ('The Eyes watch.', 'The Eyes of Hinnom watch.',
                    'The Eyes report to the Eyes of Hinnom.'):
            self.assertIn('the-eyes',
                          [rt.term_id for rt in _resolve_auto(index, src)],
                          msg=src)


class GlobalBansPathUnaffectedTest(unittest.TestCase):
    """R21 只作用于 auto-bind 锚点绑定；global_bans 路径行为不变。"""

    def test_global_ban_still_fires_without_shadowing(self):
        bans = [{'english': 'Stormcloak', 'forbidden': ['暴风斗篷'], 'target': '风暴斗篷',
                 'reason': '官方译名'}]
        resolved = resolve_global_bans({'global_bans': bans})
        self.assertEqual(find_global_ban_hits('The Stormcloak is here.', '暴风斗篷来了。', resolved[0]),
                         ['暴风斗篷'])

    def test_global_ban_anchor_gating_unchanged(self):
        """global_bans 用 _iter_word_matches，不走 R21 覆盖逻辑：锚点缺席的行仍不触发。"""
        bans = [{'english': 'Stormcloak', 'forbidden': ['暴风斗篷'], 'target': '风暴斗篷'}]
        resolved = resolve_global_bans({'global_bans': bans})
        self.assertEqual(find_global_ban_hits('A quiet day in Whiterun.',
                                              '雪漫城平静的一天，来了个暴风斗篷。',
                                              resolved[0]), [])

    def test_cross_target_covered_unchanged(self):
        """跨条豁免既有行为保留：波斯莫既是 Bosmer 的 target 也是 Wood Elf 的 forbidden。"""
        bans = [
            {'english': 'Bosmer', 'forbidden': ['暗精灵法师'], 'target': '波斯莫'},
            {'english': 'Wood Elf', 'forbidden': ['波斯莫'], 'target': '林精灵'},
        ]
        src = 'The Bosmer and the Wood Elf both serve me.'
        dst = '波斯莫和林精灵都为我效力。'
        covered = cross_target_covered(src, dst, '波斯莫', bans, 'Wood Elf')
        self.assertTrue(covered)
        # 覆盖方锚点缺席时不得豁免
        self.assertFalse(cross_target_covered('Only the Wood Elf.',
                                              '只有林精灵。', '波斯莫', bans, 'Wood Elf'))


class StandaloneForbiddenPathUnaffectedTest(unittest.TestCase):
    """R19 独立禁形路径不受 R21 影响：FORBIDDEN_ONLY 词条照常按锚点拦截。"""

    def test_unbound_forbidden_still_fires(self):
        terms = [_term('the Reach', '瑞驰',
                       enforcement='FORBIDDEN_ONLY', forbidden=['瑞驰领'])]
        index = {t['term_id']: t for t in terms}
        issues = standalone_forbidden_issues('Welcome to The Reach.', '欢迎来到瑞驰领。', index, set())
        self.assertIn('TERM002', _codes(issues))

    def test_unbound_forbidden_absent_anchor_does_not_fire(self):
        terms = [_term('the Reach', '瑞驰',
                       enforcement='FORBIDDEN_ONLY', forbidden=['瑞驰领'])]
        index = {t['term_id']: t for t in terms}
        issues = standalone_forbidden_issues('Go to Whiterun.', '去雪漫城。', index, set())
        self.assertEqual(issues, [])


class HyphenAndLegacyExemptionRegressionTest(unittest.TestCase):
    """回归：Altmer / High Elf 连字符变体、the Reach、the Eye 等既有豁免不被破坏。"""

    def test_anchor_present_accepts_hyphen_variants(self):
        from term_match import _anchor_present
        self.assertTrue(_anchor_present('the noble High-elf blood', 'High Elf'))
        self.assertTrue(_anchor_present('an alt-mer priest', 'Altmer'))
        self.assertTrue(_anchor_present('noble Altmeri blood', 'Altmer'))

    def test_forbidden_only_terms_are_not_auto_bound(self):
        """FORBIDDEN_ONLY（the Reach / the Eye / the order）本就不该进 auto-bind 集合。"""
        terms = [
            _term('the Reach', '瑞驰', enforcement='FORBIDDEN_ONLY', forbidden=['瑞驰领']),
            _term('the Eye', '眼线', enforcement='FORBIDDEN_ONLY', forbidden=['巨眼']),
            _term('Order', '组织', enforcement='FORBIDDEN_ONLY', risk_flags=['alias']),
        ]
        index = {t['term_id']: t for t in terms}
        self.assertEqual(_resolve_auto(index, 'Welcome to The Reach.'), [])

    def test_altmer_high_elf_cross_term_exemption_still_holds(self):
        """R17 跨条豁免：Altmer / High Elf 互搏条的正确译文不被误拦。"""
        terms = [
            _term('Altmer', '高精灵', enforcement='FORBIDDEN_ONLY', forbidden=['傲特莫']),
            _term('High Elf', '高精灵', enforcement='FORBIDDEN_ONLY', forbidden=['傲特莫']),
        ]
        index = {t['term_id']: t for t in terms}
        for src, dst in (("A High-elf's pride.", '高精灵的骄傲。'),
                         ("An alt-mer priest.", '一位高精灵祭司。')):
            self.assertEqual(standalone_forbidden_issues(src, dst, index, set()), [],
                             msg=src)


class RunGateEndToEndTest(unittest.TestCase):
    """端到端：走真实 run_gate，确认 auto-bind 模式下不再出 TERM001。"""

    def test_gate_reports_no_term001_for_long_name_only_line(self):
        terms = [_term('the Eyes', '眼线'), _term('the Eyes of Hinnom', '欣嫩之眼')]
        contract = _contract(terms)
        results = [{
            'translation_unit_id': 'xml-index:2869',
            'xml_index': 2869,
            'source': 'Is the Eyes of Hinnom here with me now?',
            'translation': '欣嫩之眼现在就在我身边吗？',
        }]
        report = run_gate(results, contract, [], auto_bind=True)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual([i for i in report['warnings'] if i['code'] == 'TERM001'], [])

    def test_gate_still_warns_when_bare_anchor_target_missing(self):
        terms = [_term('the Eyes', '眼线'), _term('the Eyes of Hinnom', '欣嫩之眼')]
        contract = _contract(terms)
        results = [{
            'translation_unit_id': 'xml-index:1',
            'xml_index': 1,
            'source': 'The Eyes of Hinnom watch, and the Eyes report to Ja\'zel.',
            'translation': '欣嫩之眼在监视，并向贾’泽尔复命。',
        }]
        report = run_gate(results, contract, [], auto_bind=True)
        term001 = [i for i in report['warnings'] + report['fails']
                   if i['code'] == 'TERM001' and i['term_id'] == 'the-eyes']
        self.assertEqual(len(term001), 1, msg=report)

    def test_resolved_order_is_stable(self):
        """排序稳定：返回顺序与 term_index 顺序一致，报告可比对。"""
        terms = [_term('Ja\'zel', '贾’泽尔'), _term('the Eyes', '眼线'),
                 _term('the Eyes of Hinnom', '欣嫩之眼')]
        index = {t['term_id']: t for t in terms}
        bound = [rt.term_id for rt in _resolve_auto(index, 'Ja\'zel and the Eyes of Hinnom.')]
        self.assertEqual(bound, ["ja'zel", 'the-eyes-of-hinnom'])


if __name__ == '__main__':
    unittest.main()
