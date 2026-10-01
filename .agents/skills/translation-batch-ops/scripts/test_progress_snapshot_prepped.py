#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""progress_snapshot.py 的「已备料待派」派单侧告警测试。

事故锚定（TheKalpicAnomaly_GLENMORIL 2026-10-02）：INFO-368/369 备料后整轮无人
派单，canonical 里 93 行始终未译，最后是审查器逐行读 readout 才发现。根因是
**派单侧没有防线**——此前只有两道产出侧告警：

- 滞留：TRANSLATED（map 已落盘）长期未消费 → 指向交付丢失
- 已验收但未写回：VERIFIED 但 canonical 仍与 Source 相同 → 指向写回遗漏

而 PREPPED（三件套齐、可以派单、却没人接）此前**只打印一个计数**「已备料 6」。
计数不告诉人「是哪 6 批」，编排者判断下一批派什么只能凭印象，于是这两批被静默
跳过，跨轮次再也没进过任何一份「下一动作」。

本组测试锁住三件事：
1. `prepped_ids` 无条件带出已备料批的**成员**（不是只有计数）；
2. 超阈值的已备料批进 `prepped_stale` 并在文本行打出告警 + ID；
3. 产物侧状态（VERIFIED / TRANSLATED）不得混进派单侧清单。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_progress_snapshot_prepped.py"
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress_snapshot as ps  # noqa: E402
from check_batch_coverage import classify  # noqa: E402


def write_prepped_batch(batches_dir: Path, bid: str, age_minutes: int = 0) -> None:
    """造一个 classify() 认作 PREPPED 的批次：三件套齐 + 空的 translation.json 骨架。

    age_minutes 回拨 translation.json 的 mtime，模拟「备料多久没人派」。
    """
    d = batches_dir / bid
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.txt").write_text("0\n", encoding="utf-8")
    (d / "context.json").write_text("{}", encoding="utf-8")
    (d / "term-digest.md").write_text("# x\n", encoding="utf-8")
    tr = d / "translation.json"
    tr.write_text(json.dumps(
        {"translations": [{"xml_index": 0, "translation": "", "status": "PENDING"}]},
        ensure_ascii=False), encoding="utf-8")
    if age_minutes:
        t = time.time() - age_minutes * 60
        os.utime(tr, (t, t))


def write_translated_batch(batches_dir: Path, bid: str, age_minutes: int = 0) -> None:
    """TRANSLATED：有 map.json 但 translation.json 未填充。"""
    write_prepped_batch(batches_dir, bid, age_minutes)
    mp = batches_dir / bid / "map.json"
    mp.write_text(json.dumps({"0": {"translation": "译文", "status": "TRANSLATED",
                                    "confidence": "HIGH"}}, ensure_ascii=False),
                  encoding="utf-8")
    if age_minutes:
        t = time.time() - age_minutes * 60
        os.utime(mp, (t, t))


def write_verified_batch(batches_dir: Path, bid: str) -> None:
    """VERIFIED：translation.json 已填充。"""
    write_prepped_batch(batches_dir, bid)
    (batches_dir / bid / "map.json").write_text(
        json.dumps({"0": {"translation": "译文", "status": "TRANSLATED",
                          "confidence": "HIGH"}}, ensure_ascii=False), encoding="utf-8")
    (batches_dir / bid / "translation.json").write_text(json.dumps(
        {"translations": [{"xml_index": 0, "translation": "译文",
                           "status": "TRANSLATED", "confidence": "HIGH"}]},
        ensure_ascii=False), encoding="utf-8")


class ClassifyPrepMtimeTest(unittest.TestCase):
    """classify() 必须为 PREPPED 批次回传备料时间戳，否则超期判定无从下手。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.bd = Path(self._td.name) / "batches"

    def tearDown(self):
        self._td.cleanup()

    def test_prepped_batch_carries_prep_mtime(self):
        write_prepped_batch(self.bd, "INFO-001", age_minutes=90)
        row = classify({"id": "INFO-001", "idx": [0]}, str(self.bd))
        self.assertEqual(row["state"], "PREPPED")
        self.assertIsNotNone(row["prep_mtime"], "PREPPED 必须回传备料时间戳")

    def test_missing_batch_has_no_prep_mtime(self):
        row = classify({"id": "INFO-404", "idx": [0]}, str(self.bd))
        self.assertEqual(row["state"], "MISSING")
        self.assertIsNone(row["prep_mtime"])


class PreppedMembersTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.bd = Path(self._td.name) / "batches"
        # 三个已备料，备料越久排越前
        write_prepped_batch(self.bd, "INFO-368", age_minutes=200)
        write_prepped_batch(self.bd, "INFO-369", age_minutes=180)
        write_prepped_batch(self.bd, "INFO-375", age_minutes=5)
        # 产物侧的两个，不该进派单侧清单
        write_translated_batch(self.bd, "INFO-374")
        write_verified_batch(self.bd, "INFO-370")
        self.plan = {"batches": [{"id": b} for b in
                                 ["INFO-368", "INFO-369", "INFO-370",
                                  "INFO-374", "INFO-375"]]}

    def tearDown(self):
        self._td.cleanup()

    def test_prepped_ids_lists_members_not_just_count(self):
        s = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=60)
        self.assertEqual(s["prepped"], 3)
        self.assertEqual(s["prepped_ids"], ["INFO-368", "INFO-369", "INFO-375"])
        # 产物侧状态不得混入派单侧
        self.assertNotIn("INFO-374", s["prepped_ids"])
        self.assertNotIn("INFO-370", s["prepped_ids"])

    def test_prepped_ids_sorted_by_longest_waiting_first(self):
        """排序按「躺了多久」而不是批号——下一轮该派的是最久没人接的那个。"""
        s = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=60)
        ages = {b["id"]: b["age_minutes"] for b in
                [e for e in s["prepped_stale_batches"]]}
        self.assertEqual(s["prepped_ids"][0], "INFO-368")
        self.assertGreaterEqual(ages["INFO-368"], ages["INFO-369"])

    def test_stale_only_beyond_threshold(self):
        s = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=60)
        stale = [b["id"] for b in s["prepped_stale_batches"]]
        self.assertEqual(s["prepped_stale"], 2)
        self.assertEqual(sorted(stale), ["INFO-368", "INFO-369"])
        self.assertNotIn("INFO-375", stale, "5 分钟的正常在途不算长期未派单")

    def test_no_stale_when_threshold_wide(self):
        s = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=600)
        self.assertEqual(s["prepped_stale"], 0)
        self.assertEqual(s["prepped_stale_batches"], [])

    def test_translated_stall_does_not_leak_into_prepped(self):
        """TRANSLATED 长期未消费走「滞留」，与「已备料未派单」是两种欠账，不能混。"""
        s = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=60)
        self.assertEqual(s["stalled"], 0)          # INFO-374 刚写，未超 stall_minutes
        self.assertNotIn("INFO-374", s["prepped_ids"])


class RenderTest(unittest.TestCase):
    """事故的最后一环：成员必须出现在**主状态行**，而不只存在于 JSON。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.bd = Path(self._td.name) / "batches"
        write_prepped_batch(self.bd, "INFO-368", age_minutes=200)
        write_prepped_batch(self.bd, "INFO-369", age_minutes=180)
        self.plan = {"batches": [{"id": b} for b in ["INFO-368", "INFO-369"]]}
        self.snap = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=60)
        self.lines = ps.format_batch_lines(self.snap, {})

    def tearDown(self):
        self._td.cleanup()

    def test_main_status_line_shows_member_ids(self):
        main_line = self.lines[0]
        self.assertIn("已备料 2", main_line)
        self.assertIn("INFO-368", main_line,
                      "主状态行必须带出已备料成员，只给计数就是本次事故的成因")
        self.assertIn("INFO-369", main_line)

    def test_stale_alert_line_lists_ids_with_age(self):
        alert = [ln for ln in self.lines if "已备料未派单" in ln]
        self.assertEqual(len(alert), 1, "超期未派单必须单独告警")
        self.assertIn("INFO-368", alert[0])
        self.assertIn("INFO-369", alert[0])
        self.assertIn("min", alert[0], "告警要带等待时长，便于判断该先派哪批")

    def test_no_alert_when_all_fresh(self):
        fresh = ps.batch_summary(
            {"batches": [{"id": "INFO-375"}]}, str(self.bd), prep_stall_minutes=9999)
        lines = ps.format_batch_lines(
            ps.batch_summary({"batches": [{"id": "INFO-375"}]}, str(self.bd)),
            {})
        self.assertFalse([ln for ln in lines if "已备料未派单" in ln],
                         "阈值内不应报未派单告警，否则每轮都在喊")

    def test_derived_alert_absent_when_threshold_wide(self):
        wide = ps.batch_summary(self.plan, str(self.bd), prep_stall_minutes=99999)
        lines = ps.format_batch_lines(wide, {})
        self.assertFalse([ln for ln in lines if "已备料未派单" in ln])
        self.assertIn("INFO-368", lines[0], "成员仍要在主行，只是超期告警不响")

    def test_cap_truncation_keeps_overflow_marker(self):
        bd2 = self.bd
        for i in range(ps.PREPPED_ID_CAP + 3):
            write_prepped_batch(bd2, f"INFO-9{i:02d}", age_minutes=30)
        plan = {"batches": [{"id": f"INFO-9{i:02d}"} for i in range(ps.PREPPED_ID_CAP + 3)]}
        s = ps.batch_summary(plan, str(bd2), prep_stall_minutes=99999)
        main_line = ps.format_batch_lines(s, {})[0]
        self.assertIn(f"+3", main_line,
                      "成员超出上限要给出溢出数，不能静默截断成看不出漏了几批")


if __name__ == "__main__":
    unittest.main()
