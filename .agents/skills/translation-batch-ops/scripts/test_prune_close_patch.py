#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prune_close_patch.py 的行为锁定。

事故锚定：close-round-patch.json 是追加而非重建，跨轮重跑撞 writer 的
compare-and-swap。残留分两类——已就位（可剪）与老值（剪不掉，须删文件重建）。
本组测试锁住分类判定与「默认不写盘」的安全边界。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_prune_close_patch.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "prune_close_patch.py"
sys.path.insert(0, str(HERE))

import prune_close_patch as pcp  # noqa: E402


def write_xml(path: Path, dests) -> None:
    body = "".join("<String><Source>S{}</Source><Dest>{}</Dest></String>".format(i, d)
                   for i, d in enumerate(dests))
    path.write_text("<root>{}</root>".format(body), encoding="utf-8")


def patch_entry(translation, expected=None):
    return {"expected_dest": expected if expected is not None else translation,
            "translation": translation}


class AsItemsTest(unittest.TestCase):
    def test_dict_shape(self):
        items = pcp._as_items({"5": patch_entry("甲")})
        self.assertEqual(items, [("5", {"expected_dest": "甲", "translation": "甲"})])

    def test_empty_dict_is_valid_not_an_error(self):
        # INFO-204 的残留 patch 就是 4 字节的 {}：本轮无修正，属正常
        self.assertEqual(pcp._as_items({}), [])

    def test_list_shape_normalised(self):
        items = pcp._as_items([{"xml_index": 7, "translation": "乙"}])
        self.assertEqual(items, [("7", {"expected_dest": "", "translation": "乙"})])

    def test_wrapped_shape(self):
        items = pcp._as_items({"patches": [{"idx": 3, "new": "丙"}]})
        self.assertEqual(items, [("3", {"expected_dest": "", "translation": "丙"})])

    def test_unrecognised_shape_raises(self):
        with self.assertRaises(ValueError):
            pcp._as_items({"weird": "value"})


class ClassifyTest(unittest.TestCase):
    def test_applied_when_translation_equals_canonical(self):
        applied, stale = pcp.classify([("1", patch_entry("甲"))], ["零", "甲"])
        self.assertEqual(len(applied), 1)
        self.assertEqual(stale, [])

    def test_stale_when_translation_differs(self):
        applied, stale = pcp.classify([("1", patch_entry("新", expected="旧"))],
                                      ["零", "旧"])
        self.assertEqual(applied, [])
        self.assertEqual(len(stale), 1)
        self.assertIn("现值", stale[0][2])

    def test_out_of_range_goes_to_stale_not_dropped(self):
        applied, stale = pcp.classify([("99", patch_entry("甲"))], ["零", "甲"])
        self.assertEqual(applied, [])
        self.assertIn("越界", stale[0][2])

    def test_non_integer_idx_goes_to_stale(self):
        applied, stale = pcp.classify([("x", patch_entry("甲"))], ["零", "甲"])
        self.assertEqual(applied, [])
        self.assertIn("非整数", stale[0][2])


class CliTest(unittest.TestCase):
    def _run(self, td, *extra):
        root = Path(td)
        xml = root / "canon.xml"
        write_xml(xml, ["零", "甲", "旧值"])
        work = root / "work"
        args = [sys.executable, str(SCRIPT), "--stem", "S", "--xml", str(xml),
                "--work", str(work)]
        return subprocess.run(args + list(extra), capture_output=True,
                              text=True, encoding="utf-8")

    def _write_patch(self, td, bid, data):
        d = Path(td) / "work" / "batches" / bid
        d.mkdir(parents=True, exist_ok=True)
        p = d / "close-round-patch.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_patch(td, "B1", {"1": patch_entry("甲")})
            before = p.read_text(encoding="utf-8")
            r = self._run(td, "--batches", "B1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("mode: dry-run", r.stdout)
            self.assertIn("已就位", r.stdout)
            self.assertEqual(p.read_text(encoding="utf-8"), before,
                             "dry-run 不得写盘")

    def test_prune_removes_only_applied(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_patch(td, "B1", {
                "1": patch_entry("甲"),
                "2": patch_entry("新", expected="旧"),
            })
            r = self._run(td, "--batches", "B1", "--prune")
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["2"], "--prune 不得删老值残留")
            self.assertIn("老值", r.stdout)

    def test_drop_stale_removes_only_stale(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_patch(td, "B1", {
                "1": patch_entry("甲"),
                "2": patch_entry("新", expected="旧"),
            })
            r = self._run(td, "--batches", "B1", "--drop-stale")
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["1"], "--drop-stale 不得删已就位")

    def test_missing_patch_file_reported_not_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(td, "--batches", "NOPE")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("无 patch 文件", r.stdout)

    def test_empty_patch_file_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            self._write_patch(td, "B1", {})
            r = self._run(td, "--batches", "B1", "--prune")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_canonical_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(td, "--batches", "B1")
            r2 = subprocess.run(
                [sys.executable, str(SCRIPT), "--stem", "S",
                 "--xml", str(Path(td) / "nope.xml"),
                 "--work", str(Path(td) / "w"), "--batches", "B1"],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r2.returncode, 2)
            self.assertIn("canonical 不存在", r2.stderr)


if __name__ == "__main__":
    unittest.main()
