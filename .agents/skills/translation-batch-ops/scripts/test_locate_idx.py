# -*- coding: utf-8 -*-
"""locate_idx.py 回归测试。

真事故背景：收口前必须知道每行归哪个批次，写错分组会被 close_round 报成
「裸格式 fixes 只能配一个 --batch」——错误发生在更早、更难定位的一步。
判批归属必须扫 batches/*/index.txt，不能查批次计划（计划可能与批次目录不同步）。
"""
import tempfile
import unittest
from pathlib import Path

import locate_idx as li


def make(root: Path, batches):
    bd = root / "batches"
    bd.mkdir(parents=True, exist_ok=True)
    for b, idxs in batches.items():
        (bd / b).mkdir(exist_ok=True)
        (bd / b / "index.txt").write_text(
            "\n".join(str(i) for i in idxs) + "\n", encoding="utf-8")
    return root


class IndexOwnerTest(unittest.TestCase):
    def test_maps_idx_to_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = make(Path(td), {"INFO-001": [1, 2, 3], "RN-INFO-001": [4, 5]})
            o = li.index_owner(root / "batches")
            self.assertEqual(o[1], "INFO-001")
            self.assertEqual(o[4], "RN-INFO-001")

    def test_empty_batches_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "batches"
            root.mkdir()
            self.assertEqual(li.index_owner(root), {})

    def test_dir_without_index_txt_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = make(Path(td), {"INFO-001": [1]})
            (root / "batches" / "INFO-002").mkdir()
            o = li.index_owner(root / "batches")
            self.assertNotIn("INFO-002", set(o.values()))


class GroupTest(unittest.TestCase):
    def test_splits_across_batches(self):
        owner = {1: "NI-DIAL-050", 2: "RN-INFO-069", 3: "NI-DIAL-050"}
        g = li.group(owner, [1, 2, 3])
        self.assertEqual(g["by_batch"], {"NI-DIAL-050": [1, 3], "RN-INFO-069": [2]})
        self.assertEqual(g["missing"], [])

    def test_unlocatable_goes_to_missing_not_silent(self):
        g = li.group({1: "INFO-001"}, [1, 99])
        self.assertEqual(g["missing"], [99])
        self.assertEqual(list(g["by_batch"]), ["INFO-001"])

    def test_dedups_and_sorts(self):
        g = li.group({5: "A", 1: "B"}, [5, 1, 5, 1])
        self.assertEqual(g["by_batch"], {"A": [5], "B": [1]})


class MainTest(unittest.TestCase):
    def test_requires_stem_and_idx(self):
        self.assertEqual(li.main(["--stem", "X"]), 2)

    def test_missing_work_dir_is_error(self):
        self.assertEqual(li.main(["--stem", "NoSuchStem", "1"]), 1)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "work" / "MiniMOD" / "batches"
            (root / "INFO-001").mkdir(parents=True)
            (root / "INFO-001" / "index.txt").write_text("7\n8\n", encoding="utf-8")
            rc = li.main(["--stem", "MiniMOD", "7", "--work-root", str(Path(td) / "work"), "--json"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
