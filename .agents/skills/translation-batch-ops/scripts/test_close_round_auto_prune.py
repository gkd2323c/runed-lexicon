#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""close_round 收口前 patch 残留自动对账的回归测试。

要锁的是**真实失效形态**：上一轮 close_round 留下的 `close-round-patch.json`
是追加而非重建，残留条目会让 writer 的 compare-and-swap 整轮 FAIL。处置原本
是人工跑两步（`prune_close_patch --prune` 再 `--drop-stale`），缺一不可——
实测连续两轮都卡在这里。现已收进 close_round 自身（`--no-auto-prune` 可关）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from close_round import auto_prune_patch

XML = """<root>
  <String><Source>Here you go.</Source><Dest>拿去吧。</Dest></String>
  <String><Source>Hello.</Source><Dest>Hello.</Dest></String>
  <String><Source>Gone.</Source><Dest>走了。</Dest></String>
</root>"""


class AutoPrunePatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.xml = root / "translated.xml"
        self.xml.write_text(XML, encoding="utf-8")
        self.patch = root / "close-round-patch.json"

    def _load(self):
        return json.loads(self.patch.read_text(encoding="utf-8"))

    def test_no_patch_file_is_noop(self):
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 0)
        self.assertFalse(self.patch.exists())

    def test_empty_patch_is_noop(self):
        self.patch.write_text("{}", encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 0)

    def test_clears_already_applied_entry(self):
        """已就位：translation == canonical 现值，纯残留。"""
        self.patch.write_text(json.dumps(
            {0: {"translation": "拿去吧。", "expected_dest": "拿去吧。"}}, ensure_ascii=False),
            encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 1)
        self.assertEqual(self._load(), {})

    def test_clears_stale_entry(self):
        """老值：expected_dest 已过期，留着必然让 writer CAS 失败。"""
        self.patch.write_text(json.dumps(
            {2: {"translation": "走远了。", "expected_dest": "走远了。"}}, ensure_ascii=False),
            encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 1)
        self.assertEqual(self._load(), {})

    def test_clears_mixed_residue(self):
        """两类混合都要清干净——这正是手工两步缺一不可的成因。"""
        self.patch.write_text(json.dumps({
            0: {"translation": "拿去吧。", "expected_dest": "拿去吧。"},   # 已就位
            2: {"translation": "走远了。", "expected_dest": "走远了。"},   # 老值
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 2)
        self.assertEqual(self._load(), {})

    def test_preserves_list_container_shape(self):
        """实际观测到 list 形态；清空后必须仍是合法 list，不能变成 dict。"""
        self.patch.write_text(json.dumps(
            [{"xml_index": 0, "translation": "拿去吧。", "expected_dest": "拿去吧。"}],
            ensure_ascii=False), encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 1)
        self.assertEqual(self._load(), [])

    def test_corrupt_patch_does_not_crash(self):
        self.patch.write_text("{not json", encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 0)

    def test_out_of_range_index_counts_as_stale_and_is_dropped(self):
        """idx 越界归入老值并清掉；绝不静默保留（保留必然 CAS 失败）。"""
        self.patch.write_text(json.dumps(
            {99: {"translation": "x", "expected_dest": "y"}}, ensure_ascii=False),
            encoding="utf-8")
        self.assertEqual(auto_prune_patch(self.patch, self.xml, "B1"), 1)
        self.assertEqual(self._load(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
