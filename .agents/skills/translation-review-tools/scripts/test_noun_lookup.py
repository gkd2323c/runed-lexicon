#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""noun_lookup「本批专名速查」段的回归测试。

它补的是 term-digest 的一个真空：`MOD:` 行只给**词表已收录**的专名，词表没收
录的专名在 digest 里一个字都不出现，译者只能自己猜译形。实测这正是新造形的
来源——`Shimmerene's Seaview` 新拟成「微光海景居」（既成面「海景」）、
`Dro Darrj` 新拟成「德罗'达吉」（既成面「德罗·达里'奇」，音与撇号位置都错）。

实现上踩了三个坑，各锁一条：
  1. 靠形态（首字母大写/驼峰）猜专名 → 漏掉句中的 `Palamay if you were I`，
     又把 `I've`/`That's`/`can't` 全当专名。改用**源 XML 名称行**做结构证据。
  2. 词边界在**归一后**的文本上找 → `Palamay if` 归一成 `palamayif`，负向前视
     永远失败，整段报「无」。边界必须在原始文本上找。
  3. 「已分裂」只看不同字符串 → `帕拉梅` 与 `科里纳尔和帕拉梅的家` 是裸形与
     派生形，被误报成分裂。改成只算**互不包含**的多形。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from noun_lookup import (  # noqa: E402
    collect_known_names, unregistered_nouns_section,
)

HEAD = '<TransFile><Data>'
TAIL = '</Data></TransFile>'


def str_xml(idx, rec, src, dst):
    return (f'<String><EDID>[{idx}]</EDID><REC>{rec}</REC>'
            f'<Source>{src}</Source><Dest>{dst}</Dest></String>')


def write_xml(path, rows):
    Path(path).write_text(HEAD + "".join(rows) + TAIL, encoding="utf-8")


def ctx_of(sources):
    """造一份最小 context.json 结构。"""
    return {"batches": [{"batch_index": 0, "entries": [
        {"xml": {"index": i, "rec": "INFO:NAM1", "source": s}}
        for i, s in enumerate(sources)]}]}


class TmpMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "src.xml"
        self.can = self.dir / "can.xml"

    def build(self, src_rows, can_rows, sources, registry=()):
        write_xml(self.src, src_rows)
        write_xml(self.can, can_rows)
        return unregistered_nouns_section(
            ctx_of(sources), str(self.can), set(registry), str(self.src))


class FoundTests(TmpMixin):
    def test_reports_existing_form(self):
        """核心：词表没收录、但库内有既成译形的专名必须被报出来。"""
        out = self.build(
            src_rows=[str_xml(1742, "NPC_:FULL", "Palamay", "Palamay")],
            can_rows=[str_xml(1742, "NPC_:FULL", "Palamay", "帕拉梅")],
            sources=["Stay away from Palamay if I were you."])
        text = "\n".join(out)
        self.assertIn("Palamay", text)
        self.assertIn("帕拉梅", text)
        self.assertIn("权威面", text)

    def test_silent_when_all_registered(self):
        out = self.build(
            src_rows=[str_xml(1, "NPC_:FULL", "Nirren", "Nirren")],
            can_rows=[str_xml(1, "NPC_:FULL", "Nirren", "尼伦")],
            sources=["Nirren is my husband."], registry=("nirren",))
        text = "\n".join(out)
        self.assertIn("（无）", text)
        self.assertNotIn("Nirren", text)

    def test_not_in_batch_not_reported(self):
        out = self.build(
            src_rows=[str_xml(1, "NPC_:FULL", "Palamay", "Palamay")],
            can_rows=[str_xml(1, "NPC_:FULL", "Palamay", "帕拉梅")],
            sources=["Nothing to do with it."])
        self.assertIn("（无）", "\n".join(out))

    def test_untranslated_name_row_still_listed_as_fresh(self):
        """名称行还没译时不能整条漏掉——那正是译者最该被提醒「全新专名」的一类。"""
        out = self.build(
            src_rows=[str_xml(1742, "NPC_:FULL", "Palamay", "Palamay")],
            can_rows=[],
            sources=["Stay away from Palamay."])
        text = "\n".join(out)
        self.assertIn("Palamay", text)
        self.assertIn("全新专名", text)


class BoundaryTests(TmpMixin):
    def test_word_in_middle_of_another_word_not_matched(self):
        """词边界必须生效：`Pala` 不应命中 `Palamay`。"""
        out = self.build(
            src_rows=[str_xml(1, "NPC_:FULL", "Pala", "Pala")],
            can_rows=[str_xml(1, "NPC_:FULL", "Pala", "帕拉")],
            sources=["A Pala sits here."])
        self.assertIn("（无）", "\n".join(out))

    def test_possessive_and_adjacent_word_matched(self):
        """`Palamay's House` / `Palamay if` 这类相邻词必须命中。"""
        for text in ("Palamay's house is nice.", "Stay away from Palamay if you can.",
                     "Ask Palamay today."):
            with self.subTest(text=text):
                out = self.build(
                    src_rows=[str_xml(1, "NPC_:FULL", "Palamay", "Palamay")],
                    can_rows=[str_xml(1, "NPC_:FULL", "Palamay", "帕拉梅")],
                    sources=[text])
                self.assertIn("Palamay", "\n".join(out))


class NoiseTests(TmpMixin):
    def test_task_title_family_excluded(self):
        """QUST/BOOK 等族的名称行不是专名（任务标题、书名），不得进速查段。"""
        out = self.build(
            src_rows=[str_xml(1, "QUST:NNAM", "Stay", "Stay")],
            can_rows=[str_xml(1, "QUST:NNAM", "Stay", "停留")],
            sources=["A Stay here."])
        self.assertIn("（无）", "\n".join(out))

    def test_containment_is_not_reported_as_split(self):
        """「帕拉梅」与「科里纳尔和帕拉梅的家」是派生形，不是两种译法。"""
        out = self.build(
            src_rows=[str_xml(1, "NPC_:FULL", "Palamay", "Palamay"),
                      str_xml(2, "CELL:FULL", "PalamayHouse", "PalamayHouse")],
            can_rows=[str_xml(1, "NPC_:FULL", "Palamay", "帕拉梅"),
                      str_xml(2, "CELL:FULL", "PalamayHouse",
                              "科里纳尔和帕拉梅的家")],
            sources=["Stay away from Palamay."])
        self.assertNotIn("**库内已分裂**", "\n".join(out))

    def test_genuine_split_is_reported(self):
        out = self.build(
            src_rows=[str_xml(1, "CELL:FULL", "Study", "Study")],
            can_rows=[str_xml(1, "CELL:FULL", "Study", "书斋宅邸"),
                      str_xml(2, "CELL:FULL", "Study", "书房钥匙")],
            sources=["Visit the Study."])
        self.assertIn("已分裂", "\n".join(out))

    def test_no_canonical_returns_empty(self):
        self.assertEqual(
            unregistered_nouns_section(ctx_of(["x"]), "", set(), None), [])


class CollectTests(TmpMixin):
    def test_only_shown_name_families(self):
        write_xml(self.src, [
            str_xml(1, "NPC_:FULL", "Alvaen", "Alvaen"),
            str_xml(2, "DIAL:FULL", "Some Topic", "Some Topic"),
            str_xml(3, "CELL:FULL", "Alinor", "Alinor"),
        ])
        names = collect_known_names(str(self.src))
        self.assertIn("alvaen", names)
        self.assertIn("alinor", names)
        self.assertNotIn("sometopic", names)

    def test_skips_markup_and_long_lines(self):
        # 裸 `<`/`>` 会让 XML 解析失败，必须转义——真实源文件里它们是实体
        write_xml(self.src, [
            str_xml(1, "MISC:FULL", "&lt;font face='$X'&gt;A", "x"),
            str_xml(2, "MISC:FULL", "x" * 80, "y" * 80),
        ])
        self.assertEqual(collect_known_names(str(self.src)), {})


if __name__ == "__main__":
    unittest.main()
