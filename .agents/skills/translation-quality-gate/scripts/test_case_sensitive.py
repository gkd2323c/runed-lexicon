# -*- coding: utf-8 -*-
"""case_sensitive 词条字段（v0.4.2）——源文大小写承载语义时的门禁行为。

事故背景（TheKalpicAnomaly_GLENMORIL 两个独立实例）：
  ① 26848 `the eye`  —— 小写普通义误触发大写专名 the Eyes 的 REQUIRED 约束；
  ② 31517 `chain of command` —— 小写普通动词义误触发大写 Command（掌权者）的
     REQUIRED 约束，门禁报 TERM001「required target 未出现: '掌权者'」，
     而译文「指挥链」完全正确。

根因是 find_source_hit_spans 硬编码 re.IGNORECASE，契约里没有任何字段能表达
「本条只看这个大小写」。修法是加一个**逐词条 opt-in** 的 case_sensitive 字段，
缺省 False，行为与修复前逐字节一致。

真实事故：command（小写）源行里的中形分布是 命令 63 : 指挥 34 : 指挥链 5，
主形「掌权者」只对应大写 C 的 3 行；用 additional_accepted 放宽或降级
FORBIDDEN_ONLY 都是绕过（前者丢掉主形约束，后者把大写 C 的约束也一并丢掉）。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from term_match import (  # noqa: E402
    _compiled_literal,
    find_source_hit_spans,
    find_source_hits,
    auto_bind_candidates,
)
from quality_gate import _resolve_auto, standalone_forbidden_issues  # noqa: E402

_COMPILER_DIR = os.path.abspath(os.path.join(_HERE, '..', '..', 'term-contract-compiler', 'scripts'))
if _COMPILER_DIR not in sys.path:
    sys.path.insert(0, _COMPILER_DIR)

from compile_contract import build_terms_json, build_terms  # noqa: E402


class TestLiteralCacheModes(unittest.TestCase):
    """缓存键必须含大小写标志：两种模式互不污染。"""

    def test_default_is_case_insensitive(self):
        self.assertTrue(_compiled_literal('Command').search('chain of command'))
        self.assertTrue(_compiled_literal('Command').search('Command teaches men'))

    def test_case_sensitive_requires_exact_casing(self):
        pat = _compiled_literal('Command', True)
        self.assertTrue(pat.search('Command teaches men'))
        self.assertIsNone(pat.search('chain of command'))

    def test_cache_does_not_leak_between_modes(self):
        # 同一 needle 先走默认模式再走敏感模式（或反序）结果都必须稳定
        for _ in range(3):
            self.assertIsNotNone(_compiled_literal('Command', True).search('Command'))
            self.assertIsNone(_compiled_literal('Command', True).search('command'))
            self.assertIsNotNone(_compiled_literal('Command', False).search('command'))


class TestFindSourceHitSpans(unittest.TestCase):
    def test_default_keeps_legacy_case_insensitive_behavior(self):
        # 'a chain of command' -> 'command' 落在 [11, 18)
        self.assertEqual(find_source_hit_spans('a chain of command', 'Command'), [(11, 18)])

    def test_case_sensitive_rejects_lowercase(self):
        self.assertEqual(find_source_hit_spans('a chain of command', 'Command', True), [])

    def test_case_sensitive_accepts_exact_casing(self):
        self.assertEqual(find_source_hit_spans('Command is limited.', 'Command', True), [(0, 7)])

    def test_case_sensitive_still_honors_word_boundary(self):
        # 大小写敏感不能削弱 R6 词边界护栏
        self.assertEqual(find_source_hit_spans('Commanded moves.', 'Command', True), [])
        self.assertEqual(find_source_hit_spans('DisCommand now.', 'Command', True), [])

    def test_case_sensitive_still_strips_html_tags(self):
        self.assertEqual(find_source_hit_spans('<font face="Command">Hi</font>',
                                               'Command', True), [])


class TestFindSourceHitsReadsTermFlag(unittest.TestCase):
    def test_term_without_flag_is_case_insensitive(self):
        term = {'source': 'Command', 'target': '掌权者'}
        self.assertEqual(find_source_hits('a chain of command', term), [11])

    def test_term_with_flag_requires_exact_casing(self):
        term = {'source': 'Command', 'target': '掌权者', 'case_sensitive': True}
        self.assertEqual(find_source_hits('a chain of command', term), [])
        self.assertEqual(find_source_hits('Command is limited.', term), [0])

    def test_flag_false_is_explicitly_equivalent_to_absent(self):
        a = {'source': 'Command', 'target': '掌权者'}
        b = {'source': 'Command', 'target': '掌权者', 'case_sensitive': False}
        self.assertEqual(find_source_hits('a chain of command', a),
                         find_source_hits('a chain of command', b))


class TestResolveAuto(unittest.TestCase):
    """事故 31517 的端到端复现：门禁不该给「指挥链」报 TERM001。"""

    def _index(self, **extra):
        return {'command': dict({
            'term_id': 'command', 'source': 'Command', 'target': '掌权者',
            'enforcement': 'REQUIRED', 'forbidden': [], 'match': {
                'kind': 'contains_phrase', 'accepted': ['掌权者']},
        }, **extra)}

    def test_lowercase_command_does_not_bind_when_case_sensitive(self):
        resolved = _resolve_auto(self._index(case_sensitive=True),
                                 'Your names became targets inside a chain of command.')
        self.assertEqual(resolved, [])

    def test_capital_command_still_binds_when_case_sensitive(self):
        resolved = _resolve_auto(self._index(case_sensitive=True),
                                 'Command is always limited.')
        self.assertEqual([r.term_id for r in resolved], ['command'])

    def test_lowercase_command_still_binds_without_flag(self):
        # 未开启时保持旧行为——这条是回归护栏，不是期望行为
        resolved = _resolve_auto(self._index(), 'a chain of command')
        self.assertEqual([r.term_id for r in resolved], ['command'])

    def test_auto_bind_candidate_set_unaffected(self):
        # 大小写敏感不参与 auto-bind 资格判定（_index 返回的是 term_index，
        # auto_bind_candidates 吃的是单条 term 定义）
        self.assertTrue(auto_bind_candidates(self._index()['command']))
        self.assertTrue(auto_bind_candidates(self._index(case_sensitive=True)['command']))


class TestCompilerEmitsFlag(unittest.TestCase):
    def test_json_path_emits_flag(self):
        terms, _keep = build_terms_json([
            {'english': 'Command', 'zh': '掌权者', 'status': 'CONFIRMED',
             'case_sensitive': True, 'note': ''},
        ])
        self.assertIs(terms['command']['case_sensitive'], True)

    def test_json_path_omits_flag_by_default(self):
        terms, _keep = build_terms_json([
            {'english': 'Command', 'zh': '掌权者', 'status': 'CONFIRMED', 'note': ''},
        ])
        self.assertNotIn('case_sensitive', terms['command'])

    def test_table_path_emits_flag(self):
        terms, _keep = build_terms([
            {'english': 'Command', 'zh': '掌权者', 'status': 'CONFIRMED',
             'note': '', 'case_sensitive': True},
        ])
        self.assertIs(terms['command']['case_sensitive'], True)

    def test_only_explicit_true_enables(self):
        for falsey in (False, 0, None, 'false', ''):
            terms, _keep = build_terms_json([
                {'english': 'Command', 'zh': '掌权者', 'status': 'CONFIRMED',
                 'case_sensitive': falsey, 'note': ''},
            ])
            self.assertNotIn('case_sensitive', terms['command'],
                             msg='falsey %r must not enable the flag' % (falsey,))

    def test_contract_survives_auto_bind_gate_path(self):
        """编译器产物直接喂给门禁时标志要能透传（真实流水线形态）。"""
        terms, _keep = build_terms_json([
            {'english': 'Command', 'zh': '掌权者', 'status': 'CONFIRMED',
             'case_sensitive': True, 'note': ''},
        ])
        resolved = _resolve_auto(terms, 'a chain of command')
        self.assertEqual(resolved, [])
        resolved = _resolve_auto(terms, 'Command teaches men.')
        self.assertEqual([r.term_id for r in resolved], ['command'])


class TestStandaloneForbiddenIssuesHonorsFlag(unittest.TestCase):
    """v0.4.4 事故 9039 的端到端复现：standalone_forbidden_issues 是
    case_sensitive 的**第二条**独立执行路径（不经过 _resolve_auto）。

    真实事故 TheKalpicAnomaly_GLENMORIL idx 9039：词条 `the Serpent` 已开
    case_sensitive，全库普查证明大小写与中文形一对一（大写 Serpent 12 行 →
    巨蛇；小写 serpent 仅 9038/9039 两行 → 巨蟒，蜕皮铁皮喻），但门禁仍报
    TERM002 forbidden '巨蟒'。根因是本函数调用 _anchor_present 时没透传
    case_sensitive，锚点闸门按 re.IGNORECASE 命中——**译文是对的，是工具在说谎**。
    """

    SERPENT = ('Yet if the skin remembers how the serpent moved, the '
               'distinction offers less comfort than it should.')
    SERPENT_DEST = '但若那张皮囊仍记得巨蟒游弋的姿态，这种分别带来的宽慰恐怕远不如人所料。'
    CAPS = 'The second is only the Serpent finding a more eloquent mouth.'
    CAPS_BAD_DEST = '第二者不过是巨蟒寻到了更雄辩的嘴。'

    def _index(self, **extra):
        return {'the-serpent': dict({
            'term_id': 'the-serpent', 'source': 'the Serpent', 'target': '巨蛇',
            'enforcement': 'REQUIRED', 'forbidden': ['巨蟒'], 'match': {
                'kind': 'contains_phrase', 'accepted': ['巨蛇']},
        }, **extra)}

    def test_sibling_lowercase_line_has_no_anchor_at_all(self):
        """9038「A shed skin is not the whole serpent.」含 the **whole** serpent，
        压根不含 `the Serpent` 这个锚点——所以全库只报 9039 一条。
        这条锁住「为什么两行小写只有一行会报」，防止有人误以为是漏检。"""
        issues = standalone_forbidden_issues(
            'A shed skin is not the whole serpent.', '褪下的皮囊算不得整条巨蟒。',
            self._index(case_sensitive=True), set())
        self.assertEqual(issues, [])

    def test_lowercase_serpent_not_flagged_when_case_sensitive(self):
        issues = standalone_forbidden_issues(
            self.SERPENT, self.SERPENT_DEST, self._index(case_sensitive=True), set())
        self.assertEqual(issues, [],
                         '小写 serpent 行不得被判 forbidden；开了 case_sensitive 就该消解')

    def test_lowercase_serpent_still_flagged_without_flag(self):
        # 未开启时保持旧行为——回归护栏，不是期望行为
        issues = standalone_forbidden_issues(
            self.SERPENT, self.SERPENT_DEST, self._index(), set())
        self.assertEqual([i['code'] for i in issues], ['TERM002'])

    def test_caps_serpent_forbidden_still_fires_when_case_sensitive(self):
        # 敏感化不得把大写行的约束一起丢掉——否则是修过头
        issues = standalone_forbidden_issues(
            self.CAPS, self.CAPS_BAD_DEST, self._index(case_sensitive=True), set())
        self.assertEqual([i['code'] for i in issues], ['TERM002'])
        self.assertEqual(issues[0]['variant'], '巨蟒')

    def test_caps_serpent_correct_form_passes(self):
        issues = standalone_forbidden_issues(
            self.CAPS, '第二者不过是巨蛇寻到了更雄辩的嘴。',
            self._index(case_sensitive=True), set())
        self.assertEqual(issues, [])

    def test_flag_is_read_from_compiled_contract_shape(self):
        """契约条目里 case_sensitive 是 term 的同级字段（不是 match 的子字段）。"""
        term = self._index(case_sensitive=True)['the-serpent']
        self.assertIs(term['case_sensitive'], True)
        issues = standalone_forbidden_issues(self.SERPENT, self.SERPENT_DEST,
                                            {'the-serpent': term}, set())
        self.assertEqual(issues, [])


class TestCrossTermCoverageHonorsFlag(unittest.TestCase):
    """v0.4.4：跨条覆盖豁免也不能「不区分大小写地豁免」。

    覆盖方词条自己声明了只认大写时，小写源文不构成覆盖依据。
    """

    def _terms(self):
        return {
            'dwemer': {'source': 'Dwemer', 'target': '矮人', 'forbidden': []},
            'dwarven': {'source': 'Dwarven', 'target': '矮人', 'forbidden': []},
        }

    def test_covering_term_without_flag_still_exempts(self):
        from term_match import _cross_term_target_covered
        self.assertTrue(_cross_term_target_covered(
            'Dwemer and Dwarven alike.', '矮人的废墟', '矮人', self._terms(), 'dwemer'))

    def test_covering_term_with_flag_is_not_exempted_by_lowercase_source(self):
        from term_match import _cross_term_target_covered
        terms = self._terms()
        terms['dwarven'] = dict(terms['dwarven'], case_sensitive=True)
        self.assertFalse(_cross_term_target_covered(
            'a dwarven tomb of the Dwemer', '矮人的废墟', '矮人', terms, 'dwemer'))

    def test_covering_term_with_flag_still_exempts_on_exact_casing(self):
        from term_match import _cross_term_target_covered
        terms = self._terms()
        terms['dwarven'] = dict(terms['dwarven'], case_sensitive=True)
        self.assertTrue(_cross_term_target_covered(
            'Dwemer and Dwarven alike.', '矮人的废墟', '矮人', terms, 'dwemer'))


if __name__ == '__main__':
    unittest.main()
