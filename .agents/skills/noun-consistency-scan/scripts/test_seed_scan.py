#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed_scan.py 的测试。

锁住三件事：① 已登记专名不得再被报出来（否则词表会自我循环报警）；
② 互斥覆盖只报真分裂、不报「同一个专名在两句里恰好共现不同常用词」；
③ 单词普通词（句首大写）不被当种子。

运行：
  py -3 -m unittest discover -s .agents/skills/noun-consistency-scan/scripts -p "test_seed_scan.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_scan as ss  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "seed_scan.py"


def write_canonical(path: Path, rows: list[tuple[str, str]]) -> None:
    body = "".join(f"<String><Source>{s}</Source><Dest>{d}</Dest></String>"
                   for s, d in rows)
    path.write_text(f"<root>{body}</root>", encoding="utf-8")


class CollectSeedsTest(unittest.TestCase):
    def test_finds_multiword_and_repeated(self):
        rows = [(0, "Jarl Ysgar sent the order.", "子爵伊斯加送出了命令。"),
                (1, "Jarl Ysgar waited.", "子爵伊斯加等着。"),
                (2, "Jarl Ysgar left.", "子爵伊斯加走了。")]
        seeds = ss.collect_seeds(rows)
        self.assertIn("jarl ysgar", seeds)
        self.assertEqual(len(seeds["jarl ysgar"]["occurrences"]), 3)

    def test_drops_function_words(self):
        rows = [(0, "The stone remains.", "石头还在。"),
                (1, "The stone does not.", "石头不是。"),
                (2, "The stone is here.", "石头在这儿。")]
        seeds = ss.collect_seeds(rows)
        self.assertNotIn("the", seeds)

    def test_allows_connector_inside_name(self):
        rows = [(0, "Order of the Blue reached us.", "蓝灯会的命令送到了。"),
                (1, "Order of the Blue answered.", "蓝灯会回应了。"),
                (2, "Order of the Blue waits.", "蓝灯会在等。")]
        seeds = ss.collect_seeds(rows)
        self.assertIn("order of the blue", seeds)


class SplitCandidatesTest(unittest.TestCase):
    def test_reports_mutually_exclusive_groups(self):
        dst = {0: "子爵伊斯加在等。", 1: "子爵伊斯加走了。",
               2: "爵爷伊斯加来了。", 3: "爵爷伊斯加歇了。"}
        groups = ss.split_candidates([0, 1, 2, 3], dst, min_group=2)
        self.assertIsNotNone(groups)
        forms = {g["form"] for g in groups}
        self.assertEqual(forms, {"子爵", "爵爷"})

    def test_no_split_when_one_form_covers_all(self):
        dst = {0: "子爵伊斯加在等。", 1: "子爵伊斯加走了。", 2: "子爵伊斯加来了。"}
        self.assertIsNone(ss.split_candidates([0, 1, 2], dst, min_group=2))

    def test_no_split_when_group_below_min_group(self):
        # 偶发共现：4 行里只有 1 行带另一个词，不足以判定为稳定形态
        dst = {0: "子爵伊斯加。", 1: "子爵伊斯加走了。", 2: "子爵伊斯加歇了。",
               3: "子爵，爵爷回来了。"}
        self.assertIsNone(ss.split_candidates([0, 1, 2, 3], dst, min_group=2))

    def test_no_split_when_groups_overlap(self):
        dst = {0: "子爵伊斯加。", 1: "爵爷伊斯加。", 2: "爵爷与子爵同席。", 3: "子爵说过。"}
        self.assertIsNone(ss.split_candidates([0, 1, 2, 3], dst, min_group=2))


class ScanTest(unittest.TestCase):
    def test_registered_names_are_not_reported(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            xml = td_p / "c.xml"
            write_canonical(xml, [
                ("Jarl Ysgar waited.", "子爵伊斯加等着。"),
                ("Jarl Ysgar left.", "爵爷伊斯加走了。"),
                ("Jarl Ysgar rested.", "爵爷伊斯加歇了。"),
            ])
            inv = td_p / "inv.json"
            inv.write_text(json.dumps({"items": [{"english": "Jarl Ysgar"}]}),
                           encoding="utf-8")
            res = ss.scan(xml, [inv], min_occurrences=3, min_group=2)
            self.assertEqual(res["candidate_count"], 0)

    def test_unregistered_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            xml = td_p / "c.xml"
            write_canonical(xml, [
                ("Brandt the Smith waited.", "铁匠布兰特等着。"),
                ("Brandt the Smith left.", "铁匠布兰特走了。"),
                ("Brandt the Smith rested.", "老爷布兰特歇了。"),
                ("Brandt the Smith returned.", "老爷布兰特回来了。"),
            ])
            inv = td_p / "inv.json"
            inv.write_text(json.dumps({"items": []}), encoding="utf-8")
            res = ss.scan(xml, [inv], min_occurrences=3, min_group=2)
            self.assertEqual(res["candidate_count"], 1)
            cand = res["candidates"][0]
            self.assertIn("brandt the smith", cand["english"].lower())
            self.assertEqual({g["form"] for g in cand["zh_groups"]}, {"铁匠", "老爷"})

    def test_min_occurrences_gate(self):
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "c.xml"
            write_canonical(xml, [("Zuzu laughed.", "祖祖笑了。"),
                                  ("Zuzu frowned.", "爵祖祖皱眉。")])
            res = ss.scan(xml, [], min_occurrences=3, min_group=1)
            self.assertEqual(res["candidate_count"], 0)


class CliTest(unittest.TestCase):
    def test_writes_report_and_prints_reminder(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            xml = td_p / "c.xml"
            write_canonical(xml, [("Vex paid.", "维克斯付了钱。"),
                                  ("Vex left.", "维克斯走了。"),
                                  ("Vex waited.", "维克斯等着。")])
            out = td_p / "seed.json"
            r = subprocess.run([sys.executable, str(SCRIPT), "--xml", str(xml),
                                "--registered", str(td_p / "none.json"),
                                "--out", str(out)],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.is_file())
            self.assertIn("规则零命中不等于收敛", r.stdout)
            json.loads(out.read_text(encoding="utf-8"))

    def test_requires_stem_or_xml(self):
        r = subprocess.run([sys.executable, str(SCRIPT)],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
