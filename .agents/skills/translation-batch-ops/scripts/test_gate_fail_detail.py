#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate FAIL 明细内联：验收报告必须自带「拦了什么」，不靠手工重跑 gate。

事故锚定（2026-10-03，INFO-088）：round_pipeline 报
`INFO-088 GATE verdict=FAIL fails=1`，detail 只留一句
「用 *-gate-report.json 重跑取明细」；而那个固定路径报告里躺着的是上一次**全库**
gate 的 PASS（405 units），与本次 45 unit 的 FAIL 根本对不上。真正被拦的是 8789 的
直角引号「」（CHAR001），明细明明就打在 gate 的 stdout 里，验收侧却把它丢掉了。

本组测试锁住：明细行必须内联进 fails.detail，且不得再出现「重跑取明细」式指引。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_gate_fail_detail.py"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_subagent_batch as vsb  # noqa: E402


class GateFailDetailTest(unittest.TestCase):
    def test_detail_carries_fail_lines(self):
        lines = ["FAIL [INFO:NAM1:05409C57#8789] CHAR001 : 非简体字符: '「'->'“', '」'->'”'"]
        got = vsb.gate_fail_detail('FAIL', 1, 0, lines)
        self.assertIn('verdict=FAIL', got)
        self.assertIn('CHAR001', got)
        self.assertIn('8789', got)
        self.assertIn('非简体字符', got)

    def test_detail_degrades_without_lines(self):
        got = vsb.gate_fail_detail('FAIL', 1, 0, [])
        self.assertIn('verdict=FAIL', got)
        self.assertNotIn('|', got)

    def test_detail_is_bounded(self):
        got = vsb.gate_fail_detail('FAIL', 20, 0,
                                   ['FAIL x%d : y' % i for i in range(20)])
        self.assertLessEqual(len(got), 2000)

    def test_no_manual_rerun_hint(self):
        """detail 里不得再出现把人支去别处的「重跑取明细」类指引。"""
        got = vsb.gate_fail_detail('FAIL', 1, 0, ["FAIL [u] CODE : d"])
        self.assertNotIn('重跑', got)
        self.assertNotIn('-gate-report.json', got)


if __name__ == "__main__":
    unittest.main()
