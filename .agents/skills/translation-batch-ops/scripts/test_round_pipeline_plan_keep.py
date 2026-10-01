#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""round_pipeline.py 的计划族解析与段核对 KEEP 处理测试。

两条事故锚定（TheKalpicAnomaly_GLENMORIL 2026-10-02）：

1. **非 INFO 族批次无法收口**——`--plan` 默认空，原实现只在显式传了才转发给
   consume，于是 `NI-*` 永远拿 info 计划，consume 报 `unknown batch`，
   round_pipeline 停在写回前。症状是「这批已备料却怎么都收不掉」且每次只报
   unknown batch，看不出是计划选错。`NI-QUST-001` / `NI-TES4-001` 因此静默
   躺了 37 小时。修法：`plan_for_batch()` 按批次 ID 前缀解析所属计划。

2. **KEEP 行被段核对误判为未译残留**——KEEP 的定义就是「保留原文不译」，
   Dest==Source 正是它的正确终态；原代码只判 Source==Dest 不看 status，
   于是纯 KEEP 批（0 处 Dest 变更）在段核对被报「未译残留 1/1」把整轮卡死。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_round_pipeline_plan_keep.py"
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import round_pipeline as rp  # noqa: E402


class PlanForBatchTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.work = Path(self._td.name)
        (self.work / "context").mkdir()
        for suf in ("-info-batches.json", "-noninfo-batches.json",
                    "-info-rec-batches.json", "-gaps-batches.json"):
            (self.work / "context" / f"Mod{suf}").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_each_family_routes_to_its_own_plan(self):
        cases = {
            "INFO-001": "-info-batches.json",
            "NI-DIAL-001": "-noninfo-batches.json",
            "NI-QUST-001": "-noninfo-batches.json",
            "NI-TES4-001": "-noninfo-batches.json",
            "RN-INFO-001": "-info-rec-batches.json",
            "GAP-INFO-001": "-gaps-batches.json",
        }
        for bid, suf in cases.items():
            got = rp.plan_for_batch(self.work, "Mod", bid)
            self.assertTrue(got.endswith(suf),
                            f"{bid} 应走 {suf}，实得 {got}")

    def test_explicit_plan_wins(self):
        got = rp.plan_for_batch(self.work, "Mod", "NI-QUST-001", "custom.json")
        self.assertEqual(got, "custom.json")

    def test_missing_plan_file_yields_empty(self):
        got = rp.plan_for_batch(self.work, "Mod", "INFO-001")
        self.assertTrue(got.endswith("-info-batches.json"))
        (self.work / "context" / "Mod-info-batches.json").unlink()
        self.assertEqual(rp.plan_for_batch(self.work, "Mod", "INFO-001"), "",
                         "计划文件不存在时应回退空串让 consume 用内置默认，而不是抛错")


class KeepExcludedFromResidualTest(unittest.TestCase):
    """段核对的 KEEP 排除逻辑（复刻 round_pipeline 内的判定）。"""

    def _rows(self, sources, dests):
        root = ET.Element("root")
        for s, d in zip(sources, dests):
            el = ET.SubElement(root, "String")
            ET.SubElement(el, "Source").text = s
            ET.SubElement(el, "Dest").text = d
        return list(root.iter("String"))

    def _residual(self, rows, m):
        u = 0
        for k, v in m.items():
            if isinstance(v, dict) and v.get("status") == "KEEP":
                continue
            if (rows[int(k)].findtext("Source") or "") == (rows[int(k)].findtext("Dest") or ""):
                u += 1
        return u

    def test_keep_row_is_not_counted_as_untranslated(self):
        # KEEP：Dest 保持 DEFAULT == Source，这是正确终态，不是残留
        rows = self._rows(["DEFAULT"], ["DEFAULT"])
        m = {"0": {"translation": "DEFAULT", "status": "KEEP"}}
        self.assertEqual(self._residual(rows, m), 0,
                         "KEEP 行不得计入未译残留，否则纯 KEEP 批永远收不掉")

    def test_translated_row_with_equal_text_still_counted(self):
        # 非 KEEP 却 Source==Dest：是真的漏译，必须被抓出来
        rows = self._rows(["Hello"], ["Hello"])
        m = {"0": {"translation": "Hello", "status": "TRANSLATED"}}
        self.assertEqual(self._residual(rows, m), 1,
                         "TRANSLATED 行若 Source==Dest 是真漏译，不能被 KEEP 规则放过")

    def test_mixed_batch_counts_only_real_residual(self):
        rows = self._rows(["A", "DEFAULT", "C"], ["甲", "DEFAULT", "丙"])
        m = {
            "0": {"translation": "甲", "status": "TRANSLATED"},
            "1": {"translation": "DEFAULT", "status": "KEEP"},
            "2": {"translation": "丙", "status": "TRANSLATED"},
        }
        self.assertEqual(self._residual(rows, m), 0)

        # 让第 2 行在 canonical 里真的是 Source==Dest（= 漏译），只有它该被计入
        leak_rows = self._rows(["A", "DEFAULT", "C"], ["甲", "DEFAULT", "C"])
        m2 = dict(m)
        self.assertEqual(self._residual(leak_rows, m2), 1,
                         "混批里只有真漏译那行应被计入；KEEP 与正常译行都不计")


if __name__ == "__main__":
    unittest.main()
