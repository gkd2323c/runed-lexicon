#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""noun_consistency_scan F 池（契约专名译形漂移）的回归测试。

F 池要覆盖池 A 的两个盲区，否则专名分裂会彻底不可见：
  1. 记录族——池 A 排除 INFO:NAM1／DIAL:FULL，对白里的分裂它根本不看
  2. 分组键——池 A 按整句源文分组，同一专名出现在不同句子里的分裂不在任何组内

真实锚定事故（summersetisles）：`Tusamircil` 的「图／塔」分裂落在两条
`DIAL:FULL` 上（`4677`／`6564`），对池 A 双重不可见。

F 池实现本身也踩过两个坑，各锁一条：
  - 拿归一化字符串做子串 → `fish` 含 `Ish`(370 行)、`self` 含 `Elf`(205 行)
  - 倒排分词把撇号当词内字符 → `Tusamircil's` 成了整词，锚点查不到候选行
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from noun_consistency_scan import (  # noqa: E402
    _build_inverted, _variants, pool_f,
)


def row(idx, rec, src, dst):
    return {'idx': idx, 'rec': rec, 'edid': '', 'src': src, 'dst': dst}


def term(en, zh, **kw):
    d = {'english': en, 'zh': zh, 'term_id': 't.' + en.lower().replace(' ', '-')}
    d.update(kw)
    return d


class VariantTests(unittest.TestCase):
    def test_hyphen_and_space_and_joined(self):
        v = _variants('Arch-Mage')
        self.assertEqual(v, {'arch-mage', 'arch mage', 'archmage', 'arch_mage'})

    def test_single_word(self):
        self.assertEqual(_variants('Redli'), {'redli'})

    def test_empty(self):
        self.assertEqual(_variants('  '), set())


class InvertedTests(unittest.TestCase):
    def test_apostrophe_is_a_separator(self):
        """回归护栏：`Tusamircil's` 必须切出 `tusamircil`，不能成为整词。

        否则所有格形式让锚点查不到候选行，池静默漏报真实分裂。
        """
        idx = _build_inverted([row(0, 'DIAL:FULL', "Tusamircil's Passage", 'x')])
        self.assertIn('tusamircil', idx)
        self.assertIn(0, idx['tusamircil'])

    def test_splits_multiword(self):
        idx = _build_inverted([row(0, 'DIAL:FULL', 'Arch Mage of Winterhold', 'x')])
        self.assertIn('arch', idx)
        self.assertIn('mage', idx)


class PoolFCatchesWhatPoolACannotTests(unittest.TestCase):
    """F 池必须抓到池 A 双重看不见的分裂。"""

    def setUp(self):
        self.rows = [
            # 名称行（权威面）
            row(1427, 'NPC_:FULL', 'Tusamircil', '图萨米尔西尔'),
            # 通道 CELL：源文与上面两句都不同
            row(2847, 'CELL:FULL', "Tusamircil's Passage", '图萨米尔西尔通道'),
            row(4677, 'DIAL:FULL',
                "Right, but the cave is named Tusamircil's passage. Is there an",
                '没错，可这座洞穴就叫图萨米尔西尔通道。有什么关联吗？'),
            # 对白行 + 一字之差的坏形：池 A 对它双重不可见
            row(6564, 'DIAL:FULL',
                'What can you tell me about Tusamircil\'s Passage?',
                '你能跟我说说塔萨米尔西尔通道的事吗？'),
        ]
        self.terms = {'tusamircil': term('Tusamircil', '图萨米尔西尔',
                                         forbidden=['塔萨米尔西尔'])}

    def test_catches_possessive_bad_form(self):
        out = pool_f(self.rows, self.terms)
        self.assertEqual(len(out), 1)
        g = out[0]
        self.assertEqual(g['term_en'], 'Tusamircil')
        self.assertEqual([r['idx'] for r in g['rows']], [6564])
        self.assertIn('塔萨米尔西尔', g['rows'][0]['dest'])

    def test_cleans_when_drift_gone(self):
        for r in self.rows:
            r['dst'] = r['dst'].replace('塔萨米尔西尔', '图萨米尔西尔')
        self.assertEqual(pool_f(self.rows, self.terms), [])

    def test_forbidden_is_carried_through(self):
        out = pool_f(self.rows, self.terms)
        self.assertEqual(out[0]['forbidden'], ['塔萨米尔西尔'])


class PoolFNoiseTests(unittest.TestCase):
    """词内子串不得被当成专名命中。"""

    def setUp(self):
        self.terms = {
            'ish': term('Ish', '伊什'),
            'elf': term('Elf', '精灵'),
            'eight': term('Eight', '八圣灵'),
            'terio': term('Terio', '特里奥'),
        }

    def test_substring_words_are_not_matches(self):
        rows = [
            row(1, 'INFO:NAM1', 'I caught a fish.', '我抓到一条鱼。'),
            row(2, 'INFO:NAM1', 'I cured my disease myself.', '我治好了自己的病。'),
            row(3, 'INFO:NAM1', 'It weighs too much at night.', '它夜里重得厉害。'),
            row(4, 'INFO:NAM1', 'The Interior is dark.', '里面很黑。'),
        ]
        self.assertEqual(pool_f(rows, self.terms), [])

    def test_real_word_still_matches(self):
        rows = [row(9, 'NPC_:FULL', 'Redli the merchant', '雷德利商人')]
        terms = {'redli': term('Redli', '雷德莉')}
        out = pool_f(rows, terms)
        self.assertEqual(len(out), 1)
        self.assertEqual([r['idx'] for r in out[0]['rows']], [9])


class PoolFSkipRulesTests(unittest.TestCase):
    def setUp(self):
        self.terms = {'redli': term('Redli', '雷德莉')}

    def test_untranslated_rows_are_not_drift(self):
        rows = [row(1, 'INFO:NAM1', "Redli's shop", "Redli's shop")]
        self.assertEqual(pool_f(rows, self.terms), [])

    def test_empty_dest_is_not_drift(self):
        rows = [row(1, 'INFO:NAM1', "Redli's shop", "")]
        self.assertEqual(pool_f(rows, self.terms), [])

    def test_contract_form_present_is_not_drift(self):
        rows = [row(1, 'INFO:NAM1', "Redli's shop", '雷德莉的店')]
        self.assertEqual(pool_f(rows, self.terms), [])

    def test_min_rows_threshold(self):
        rows = [row(1, 'NPC_:FULL', 'Redli', '雷德利')]
        self.assertEqual(pool_f(rows, self.terms, min_rows=2), [])
        self.assertEqual(len(pool_f(rows, self.terms, min_rows=1)), 1)

    def test_no_terms_returns_empty(self):
        self.assertEqual(pool_f([row(1, 'NPC_:FULL', 'Redli', '雷德利')], {}), [])


if __name__ == '__main__':
    unittest.main()
