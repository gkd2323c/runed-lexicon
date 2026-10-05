#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_batch 数据源新鲜度选择的回归测试。

失效形态：`translation.json` 是上一轮 `consume_batch` 的产物，子代理随后写的
`map.json` 更新。原实现只判「存在」，陈旧的那份会遮蔽刚交卷的那份——
实测踩过一次，把空模板（PENDING 占位）当成交卷读，据此做的判断全错。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_batch import pick_data_source  # noqa: E402


class PickDataSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.result = root / "translation.json"
        self.mapf = root / "map.json"

    def test_neither_file(self):
        self.assertIsNone(pick_data_source(self.result, self.mapf))

    def test_only_translation(self):
        self.result.write_text("{}", encoding="utf-8")
        self.assertEqual(pick_data_source(self.result, self.mapf), "translation")

    def test_only_map(self):
        self.mapf.write_text("{}", encoding="utf-8")
        self.assertEqual(pick_data_source(self.result, self.mapf), "map")

    def test_newer_map_wins_over_stale_translation(self):
        """核心：map.json 较新时必须采信它，而不是被 translation.json 遮蔽。"""
        self.result.write_text("{}", encoding="utf-8")
        self.mapf.write_text("{}", encoding="utf-8")
        os.utime(self.result, (1000, 1000))          # 旧
        os.utime(self.mapf, (2000, 2000))           # 新
        self.assertEqual(pick_data_source(self.result, self.mapf), "map")

    def test_newer_translation_wins(self):
        """反过来也成立：流水线刚 consume 出的 translation.json 更新，不该被 map 遮蔽。"""
        self.result.write_text("{}", encoding="utf-8")
        self.mapf.write_text("{}", encoding="utf-8")
        os.utime(self.mapf, (1000, 1000))
        os.utime(self.result, (2000, 2000))
        self.assertEqual(pick_data_source(self.result, self.mapf), "translation")

    def test_real_mtime_ordering(self):
        """用真实写入顺序（不手工设 mtime）也必须选对。"""
        self.result.write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        self.mapf.write_text("{}", encoding="utf-8")
        self.assertEqual(pick_data_source(self.result, self.mapf), "map")


if __name__ == "__main__":
    unittest.main(verbosity=2)
