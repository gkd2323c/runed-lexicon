#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""protect_batch.py 回归测试。

覆盖：
  - mask 分桶：MASK / EXCLUDE / GRAY，以及扩展文法（方括号、XML 实体）
  - `[pagebreak]` 保持 K 类型、其余方括号进 R 类型（同起点同长度的取舍）
  - restore 还原守恒：还原后的译文与源文的运行时 token 逐字符一致
  - 哨兵丢失 / 幻觉哨兵 → 硬错误且该条不进 map.json
  - EXCLUDE 按 KEEP 落回原文
  - 漏翻（translations 缺条目）报 finding 但不算硬错误

Run:  python -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_*.py"
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import protect_batch as pb  # noqa: E402

ENTRIES = [
    # 常规 token 全齐：别名 / printf / 花括号 / 换行
    {"translation_unit_id": "INFO:NAM1:AA#1",
     "xml": {"index": 1, "rec": "INFO:NAM1",
             "source": "Kill <Alias=Target> with %s, then take {0} gold.\n"}},
    # 字体标签成对 + 方括号动作提示
    {"translation_unit_id": "INFO:NAM1:AA#2",
     "xml": {"index": 2, "rec": "INFO:NAM1",
             "source": "<font color='#fff'>Hello</font> [Intimidate] friend."}},
    # 描述性尖括号 + 分页符
    {"translation_unit_id": "BOOK:DESC:AA#3",
     "xml": {"index": 3, "rec": "BOOK:DESC",
             "source": "[pagebreak]Chance to <stagger or knock down> the target"}},
    # 整条即资源路径 -> EXCLUDE
    {"translation_unit_id": "STAT:FULL:AA#4",
     "xml": {"index": 4, "rec": "STAT:FULL", "source": "Textures\\Foo.nif"}},
    # XML 实体 + 转义换行
    {"translation_unit_id": "MESG:DESC:AA#5",
     "xml": {"index": 5, "rec": "MESG:DESC", "source": "Line A\\nLine B &amp; more"}},
]


class ProtectBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.bdir = self.work / "Probe" / "batches" / "T-001"
        self.bdir.mkdir(parents=True)
        (self.bdir / "context.json").write_text(
            json.dumps({"schema_version": 1,
                        "batches": [{"batch_index": 0, "entries": ENTRIES}]},
                       ensure_ascii=False), encoding="utf-8")

    def _run(self, *extra):
        argv = ["protect_batch.py", *extra]
        old = sys.argv
        try:
            sys.argv = argv
            return pb.main()
        finally:
            sys.argv = old

    def _mask(self):
        self.assertEqual(self._run("mask", "--stem", "Probe", "--batch", "T-001",
                                   "--work-root", str(self.work)), 0)
        return json.loads((self.bdir / "masked.json").read_text(encoding="utf-8"))

    def _restore(self, translations, *extra):
        raw = Path(self.tmp.name) / "raw.json"
        raw.write_text(json.dumps(translations, ensure_ascii=False), encoding="utf-8")
        rc = self._run("restore", "--stem", "Probe", "--batch", "T-001",
                       "--work-root", str(self.work), "--translations", str(raw), *extra)
        out = self.bdir / "map.json"
        mp = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
        return rc, mp

    def test_mask_buckets_and_extended_grammar(self):
        doc = self._mask()
        by_index = {u["index"]: u for u in doc["units"]}
        self.assertEqual(by_index[1]["bucket"], "MASK")
        self.assertEqual(by_index[4]["bucket"], "EXCLUDE")
        self.assertIsNone(by_index[4]["source_masked"])
        self.assertEqual(by_index[2]["bucket"], "GRAY")
        self.assertIn("non_engine_bracket_tag", by_index[2]["gray_reasons"])
        self.assertEqual(by_index[3]["bucket"], "GRAY")
        self.assertIn("non_engine_angle_tag", by_index[3]["gray_reasons"])

        # 扩展文法：方括号动作提示必须被掩码（protect 默认只掩 [pagebreak]）
        self.assertNotIn("[Intimidate]", by_index[2]["source_masked"])
        self.assertIn("⟪", by_index[2]["source_masked"])
        # XML 实体同样纳入
        self.assertNotIn("&amp;", by_index[5]["source_masked"])
        # [pagebreak] 仍在掩码内
        self.assertNotIn("[pagebreak]", by_index[3]["source_masked"])

    def test_restore_conserves_runtime_tokens(self):
        self._mask()
        state = json.loads((self.bdir / "mask-state.json").read_text(encoding="utf-8"))
        masked = {u["index"]: u["masked"] for u in state["units"]}
        # 模拟译者：哨兵原样保留，只在周围写中文
        translations = {
            "1": "用 ⟪0⟫ 杀死 ⟪1⟫，然后拿 ⟪2⟫ 金币。⟪3⟫",
            "2": "⟪0⟫你好⟪1⟫ ⟪2⟫，朋友。",
            "3": "⟪0⟫有几率使目标 ⟪1⟫",
            "5": "甲行⟪0⟫乙行 ⟪1⟫ 及更多",
        }
        rc, mp = self._restore(translations)
        self.assertEqual(rc, 0)
        # 还原后每条都应含它源文里的全部运行时 token
        self.assertIn("<Alias=Target>", mp["1"]["translation"])
        self.assertIn("%s", mp["1"]["translation"])
        self.assertIn("{0}", mp["1"]["translation"])
        self.assertIn("\n", mp["1"]["translation"])
        self.assertIn("<font color='#fff'>", mp["2"]["translation"])
        self.assertIn("</font>", mp["2"]["translation"])
        self.assertIn("[Intimidate]", mp["2"]["translation"])
        self.assertIn("[pagebreak]", mp["3"]["translation"])
        self.assertIn("<stagger or knock down>", mp["3"]["translation"])
        self.assertIn("&amp;", mp["5"]["translation"])
        self.assertNotIn("⟪", mp["1"]["translation"])

    def test_exclude_becomes_keep(self):
        self._mask()
        rc, mp = self._restore({"1": "用 ⟪0⟫ 杀死 ⟪1⟫，然后拿 ⟪2⟫ 金币。⟪3⟫"})
        self.assertEqual(rc, 0)
        self.assertEqual(mp["4"]["status"], "KEEP")
        self.assertEqual(mp["4"]["translation"], "Textures\\Foo.nif")

    def test_sentinel_loss_is_hard_error(self):
        self._mask()
        # 译者自行把哨兵还原成文本：译文其实正确，但哨兵不可见 -> 硬错误（已知假阳性）
        rc, mp = self._restore({"1": "用 <Alias=Target> 杀死 %s，然后拿 {0} 金币。"})
        self.assertEqual(rc, 1)
        self.assertNotIn("1", mp)

    def test_hallucinated_sentinel_is_hard_error(self):
        self._mask()
        rc, mp = self._restore({"1": "用 ⟪0⟫ 杀死 ⟪1⟫，然后拿 ⟪2⟫ 金币。⟪3⟫ ⟪9⟫"})
        self.assertEqual(rc, 1)
        self.assertNotIn("1", mp)

    def test_missing_translation_is_not_hard_error(self):
        self._mask()
        # 5 条里有 1 条漏翻：软伤，不该阻断收口
        rc, mp = self._restore({"1": "用 ⟪0⟫ 杀死 ⟪1⟫，然后拿 ⟪2⟫ 金币。⟪3⟫"})
        self.assertEqual(rc, 0)
        self.assertNotIn("2", mp)
        self.assertIn("1", mp)

    def test_restore_without_mask_state_is_usage_error(self):
        raw = Path(self.tmp.name) / "raw.json"
        raw.write_text("{}", encoding="utf-8")
        self.assertEqual(self._run("restore", "--stem", "Probe", "--batch", "T-001",
                                   "--work-root", str(self.work),
                                   "--translations", str(raw)), 2)

    def test_mask_without_context_is_usage_error(self):
        empty = Path(self.tmp.name) / "empty"
        self.assertEqual(self._run("mask", "--stem", "Nope", "--batch", "X",
                                   "--work-root", str(empty)), 2)


if __name__ == "__main__":
    unittest.main()
