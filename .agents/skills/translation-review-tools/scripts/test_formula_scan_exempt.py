#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""formula_scan.py 的豁免认得与分组输出测试。

覆盖的失效点是「工具反复制造数据回归」：省略式应答句（They are. / He is.）
的差异由玩家 prompt 决定、已登记为必要差异，而整句同源分组不认这份登记时，
会把上一轮按 prompt 回正的译文重新「统一定形」成错值。本 MOD idx 26216 实测
被改回「确实令人赞叹。」（应承 They seem organized. 译「他们确实有条理。」）。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_formula_scan_exempt.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import formula_scan as fs  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "formula_scan.py"


def write_canonical(path: Path, rows: list[tuple[str, str]]) -> None:
    body = "".join(f"<String><Source>{s}</Source><Dest>{d}</Dest></String>"
                   for s, d in rows)
    path.write_text(f"<root>{body}</root>", encoding="utf-8")


def write_exempt(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class LoadExemptionsTest(unittest.TestCase):
    def test_dict_form(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "e.json"
            write_exempt(p, {"exemptions": {"They are.": {
                "idxs": [25527, 26216], "reason": "prompt 决定"}}})
            self.assertEqual(fs.load_exemptions(p), {"They are.": {25527, 26216}})

    def test_array_form(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "e.json"
            write_exempt(p, {"exemptions": {"They are.": [
                {"idxs": [26216], "reason": "prompt 决定"}]}})
            self.assertEqual(fs.load_exemptions(p), {"They are.": {26216}})

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fs.load_exemptions(Path(td) / "nope.json"), {})
            self.assertEqual(fs.load_exemptions(None), {})


class CliTest(unittest.TestCase):
    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *extra],
                              capture_output=True, text=True, encoding="utf-8")

    def test_exempt_idx_not_unified_back(self):
        # 事故形态：豁免 idx 被整句分组改回错值
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "canon.xml"
            write_canonical(xml, [("They are.", "确实令人赞叹。"),
                                  ("They are.", "他们确实有条理。")])
            ex = Path(td) / "e.json"
            write_exempt(ex, {"exemptions": {"They are.": {
                "idxs": [1], "reason": "prompt 决定：承 They seem organized."}}})
            out = Path(td) / "m.json"
            r = self._run("--xml", str(xml), "--exemptions", str(ex),
                          "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("整句同源分裂 0 组", r.stdout)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {})

    def test_without_exemptions_the_split_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "canon.xml"
            write_canonical(xml, [("They are.", "确实令人赞叹。"),
                                  ("They are.", "他们确实有条理。")])
            out = Path(td) / "m.json"
            r = self._run("--xml", str(xml), "--out", str(out))
            self.assertEqual(r.returncode, 1)
            self.assertIn("整句同源分裂 1 组", r.stdout)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("1", data)

    def test_exemption_does_not_touch_formula_head_groups(self):
        # 首句/尾句公式收敛是另一类问题，豁免只作用于整句组
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "canon.xml"
            write_canonical(xml, [
                ("Hold this. And hold that.", "守这个。抓那个。"),
                ("Hold this. And keep that.", "守住这个。留住那个。"),
            ])
            ex = Path(td) / "e.json"
            write_exempt(ex, {"exemptions": {"Hold this.": {"idxs": [0]}}})
            r = self._run("--xml", str(xml), "--exemptions", str(ex),
                          "--out", str(Path(td) / "m.json"), "--dry-run")
            self.assertIn("首句 1", r.stdout)

    def test_batches_requires_dir(self):
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "canon.xml"
            write_canonical(xml, [("A.", "甲。")])
            r = self._run("--xml", str(xml), "--batches", "INFO-001",
                          "--out", str(Path(td) / "m.json"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("必须同用", r.stdout + r.stderr)

    def test_grouped_output_shape(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            xml = td_p / "canon.xml"
            write_canonical(xml, [("X.", "旧。"), ("X.", "新。")])
            bd = td_p / "batches"
            (bd / "INFO-001").mkdir(parents=True)
            (bd / "INFO-001" / "index.txt").write_text("0\n1\n", encoding="utf-8")
            out = td_p / "m.json"
            r = self._run("--xml", str(xml), "--batches", "INFO-001",
                          "--batches-dir", str(bd), "--out", str(out))
            self.assertEqual(r.returncode, 1, r.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["INFO-001"])
            self.assertIn("close_round --batches INFO-001", r.stdout)


if __name__ == "__main__":
    unittest.main()
