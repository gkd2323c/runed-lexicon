#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_fixes_from_report.py 的批次分组输出测试。

覆盖分组能力的失效点：idx 归属判错。事故锚定——多批审查修正需要
close_round 的 {batch: {idx: fix}} 形态，手转分组时若把「idx → 批次」的
归属映射写错（如字典键误写成 idx），分组结果的顶层键会变成 idx 本身，
close_round 随后报「裸格式 fixes 只能配一个 --batch」，报错点离病因很远。
机械判定归属 + 判不出即失败，正是本组测试要锁住的行为。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_make_fixes_grouping.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixes_from_report as m  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "make_fixes_from_report.py"


def write_index(batches_dir: Path, bid: str, idxs: list[int]) -> None:
    d = batches_dir / bid
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.txt").write_text("\n".join(str(i) for i in idxs) + "\n",
                                 encoding="utf-8")


def write_canonical(path: Path, srcs: list[str], dests: list[str]) -> None:
    rows = "".join(
        f"<String><Source>{s}</Source><Dest>{d}</Dest></String>"
        for s, d in zip(srcs, dests))
    path.write_text(f"<root>{rows}</root>", encoding="utf-8")


def write_report(path: Path, findings: list[dict]) -> None:
    path.write_text(json.dumps({"issues": [], "findings": findings},
                               ensure_ascii=False), encoding="utf-8")


class LoadBatchOwnerTest(unittest.TestCase):
    def test_maps_idx_to_declared_batch(self):
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            write_index(bd, "INFO-334", [10, 11, 12])
            write_index(bd, "INFO-335", [20])
            owner = m.load_batch_owner(str(bd), ["INFO-334", "INFO-335"])
            self.assertEqual(owner, {10: "INFO-334", 11: "INFO-334",
                                    12: "INFO-334", 20: "INFO-335"})

    def test_missing_index_file_is_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit) as cm:
                m.load_batch_owner(td, ["INFO-999"])
            self.assertIn("index.txt", str(cm.exception))

    def test_overlapping_idx_is_fatal(self):
        # 两批共享同一 idx 时不猜，交给人判断（计划重叠是数据问题）
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            write_index(bd, "INFO-001", [5, 6])
            write_index(bd, "INFO-002", [6, 7])
            with self.assertRaises(SystemExit) as cm:
                m.load_batch_owner(str(bd), ["INFO-001", "INFO-002"])
            self.assertIn("重叠", str(cm.exception))


class IntroduceOverridesTest(unittest.TestCase):
    """--override 引入报告外 idx 的行为锁定。

    事故锚定：institution 全库 81:4，reviewer 只在报告里点名了一侧，
    主会话按全库多数反向收敛，要改的那几行根本不在 findings 中。旧版
    override 只在 findings 循环内取值，这类裁决无处落盘。
    """

    DESTS = ["甲", "乙", "丙", "丁"]

    def test_idx_outside_report_is_added(self):
        fixes, noop, oob = m.introduce_overrides(
            {2: "新丙"}, {0}, set(), set(), self.DESTS)
        self.assertEqual(fixes, {2: {"new": "新丙", "expected_current": "丙"}})
        self.assertEqual(noop, [])
        self.assertEqual(oob, [])

    def test_idx_already_in_report_is_not_duplicated(self):
        # 报告内的 idx 走主循环的覆盖逻辑，此处不得再产生第二条修正
        fixes, _, _ = m.introduce_overrides(
            {0: "改0"}, {0}, set(), set(), self.DESTS)
        self.assertEqual(fixes, {})

    def test_drop_wins_over_override(self):
        # 显式驳回优先：驳回就是驳回，不被「特裁」复活
        fixes, _, _ = m.introduce_overrides(
            {2: "新丙"}, set(), {2}, set(), self.DESTS)
        self.assertEqual(fixes, {})

    def test_include_filter_is_respected(self):
        fixes, _, _ = m.introduce_overrides(
            {2: "新丙", 3: "新丁"}, set(), set(), {3}, self.DESTS)
        self.assertEqual(sorted(fixes), [3])

    def test_same_value_is_noop_not_a_fix(self):
        fixes, noop, _ = m.introduce_overrides(
            {1: "乙"}, set(), set(), set(), self.DESTS)
        self.assertEqual(fixes, {})
        self.assertEqual(noop, [1])

    def test_out_of_range_is_reported_not_written(self):
        fixes, _, oob = m.introduce_overrides(
            {99: "越界"}, set(), set(), set(), self.DESTS)
        self.assertEqual(fixes, {})
        self.assertEqual(oob, [99])


class GroupFixesTest(unittest.TestCase):
    def test_groups_and_keeps_declaration_order(self):
        owner = {10: "INFO-334", 20: "INFO-335", 11: "INFO-334"}
        fixes = {20: {"new": "b"}, 10: {"new": "a"}, 11: {"new": "c"}}
        out = m.group_fixes(fixes, owner, ["INFO-335", "INFO-334"])
        self.assertEqual(list(out), ["INFO-335", "INFO-334"])
        self.assertEqual(sorted(out["INFO-334"]), ["10", "11"])
        self.assertEqual(out["INFO-335"]["20"], {"new": "b"})

    def test_orphan_idx_is_fatal(self):
        # 事故形态：归属映射缺失时若放行，输出顶层键会退化成 idx
        with self.assertRaises(SystemExit) as cm:
            m.group_fixes({99: {"new": "x"}}, {10: "INFO-334"},
                          ["INFO-334"])
        self.assertIn("99", str(cm.exception))

    def test_empty_batches_are_omitted(self):
        owner = {10: "INFO-334"}
        out = m.group_fixes({10: {"new": "a"}}, owner,
                            ["INFO-334", "INFO-335"])
        self.assertEqual(list(out), ["INFO-334"])


class CliTest(unittest.TestCase):
    def _fixture(self, td: str):
        bd = Path(td) / "batches"
        write_index(bd, "INFO-334", [0, 1])
        write_index(bd, "INFO-335", [2])
        xml = Path(td) / "translated.xml"
        write_canonical(xml, ["A.", "B.", "C."], ["甲", "乙", "丙"])
        rep = Path(td) / "review.json"
        write_report(rep, [{"idx": 0, "source": "A.", "proposed": "新甲"},
                           {"idx": 2, "source": "C.", "proposed": "新丙"}])
        return bd, xml, rep

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *extra],
                              capture_output=True, text=True, encoding="utf-8")

    def test_flat_mode_is_unchanged_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            bd, xml, rep = self._fixture(td)
            out = Path(td) / "flat.json"
            r = self._run("--report", str(rep), "--xml", str(xml),
                          "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(data), ["0", "2"])  # 扁平形态，无批次层
            self.assertEqual(data["0"]["new"], "新甲")
            self.assertEqual(data["0"]["expected_current"], "甲")

    def test_grouped_mode_emits_batch_layer(self):
        with tempfile.TemporaryDirectory() as td:
            bd, xml, rep = self._fixture(td)
            out = Path(td) / "grouped.json"
            r = self._run("--report", str(rep), "--xml", str(xml),
                          "--batches", "INFO-334", "INFO-335",
                          "--batches-dir", str(bd), "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(data), ["INFO-334", "INFO-335"])
            self.assertEqual(sorted(data["INFO-334"]), ["0"])
            self.assertEqual(sorted(data["INFO-335"]), ["2"])
            self.assertIn("分组:", r.stdout)

    def test_batches_without_batches_dir_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            bd, xml, rep = self._fixture(td)
            r = self._run("--report", str(rep), "--xml", str(xml),
                          "--batches", "INFO-334", "--out", str(Path(td) / "x.json"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("必须同用", r.stdout + r.stderr)

    def test_orphan_idx_aborts_without_writing_output(self):
        # idx 2 在 canonical 范围内，但所属批次 INFO-335 未声明：
        # 漏声明会让该修正静默消失，机械拦截优于事后发现
        with tempfile.TemporaryDirectory() as td:
            bd, xml, _ = self._fixture(td)
            rep = Path(td) / "bad.json"
            write_report(rep, [{"idx": 1, "source": "B.", "proposed": "新乙"},
                               {"idx": 2, "source": "C.", "proposed": "新丙"}])
            out = Path(td) / "grouped.json"
            r = self._run("--report", str(rep), "--xml", str(xml),
                          "--batches", "INFO-334", "--batches-dir", str(bd),
                          "--out", str(out))
            self.assertNotEqual(r.returncode, 0)
            # 只加载了声明批次的 index.txt，工具不该猜 idx 2 属于谁，
            # 报错须点名 idx 并给出处置指引
            self.assertIn("[2]", r.stdout + r.stderr)
            self.assertIn("--batches", r.stdout + r.stderr)
            self.assertFalse(out.exists(), "归属判不出时不得留下半成品")

    def test_out_of_range_idx_is_skipped_with_warning(self):
        # 越界 idx 不进修正集（既有的 WARN 口径），不算孤儿
        with tempfile.TemporaryDirectory() as td:
            bd, xml, rep = self._fixture(td)
            bad = Path(td) / "oob.json"
            write_report(bad, [{"idx": 99, "source": "?", "proposed": "?"}])
            out = Path(td) / "grouped.json"
            r = self._run("--report", str(bad), "--xml", str(xml),
                          "--batches", "INFO-334", "--batches-dir", str(bd),
                          "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WARN idx 越界", r.stderr)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {})

    def test_cli_override_introduces_idx_outside_report(self):
        # 端到端：报告只列 idx 0，override 引入报告外的 idx 1，
        # 须落到正确批次分组（INFO-334 拥有 idx 1）
        with tempfile.TemporaryDirectory() as td:
            bd, xml, rep = self._fixture(td)
            ov = Path(td) / "ov.json"
            ov.write_text(json.dumps({"1": "新乙"}, ensure_ascii=False),
                          encoding="utf-8")
            out = Path(td) / "grouped.json"
            r = self._run("--report", str(rep), "--xml", str(xml),
                          "--batches", "INFO-334", "INFO-335",
                          "--batches-dir", str(bd),
                          "--override", str(ov), "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(data["INFO-334"]), ["0", "1"])
            self.assertEqual(data["INFO-334"]["1"]["new"], "新乙")
            self.assertIn("报告外新增 1", r.stdout)


if __name__ == "__main__":
    unittest.main()
