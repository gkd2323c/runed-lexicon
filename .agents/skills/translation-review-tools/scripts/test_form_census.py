#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""form_census.py 的行为锁定。

事故锚定：同锚收敛方向连续两次判错——一次套了别的锚的专名口径（`institution`
并向「组织」），一次取了**批内**多数形（并向「制度」），正确方向是全库 84:2 的
「机构」。根因是审查实例只看得到自己批内的分布。本工具的价值全在
「全库 vs 批内并排 + 背离告警」，本组测试锁住的就是这条。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_form_census.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "form_census.py"
sys.path.insert(0, str(HERE))

import form_census as fc  # noqa: E402


def xml(path: Path, rows):
    body = "".join(
        "<String><Source>{}</Source><Dest>{}</Dest></String>".format(s, d)
        for s, d in rows)
    path.write_text("<root>{}</root>".format(body), encoding="utf-8")


class StringsTest(unittest.TestCase):
    def test_reads_source_and_dest_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            xml(p, [("A", "甲"), ("B", "乙")])
            self.assertEqual(fc._strings(p), [("A", "甲"), ("B", "乙")])

    def test_empty_dest_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            xml(p, [("A", "")])
            self.assertEqual(fc._strings(p), [("A", "")])


class CensusTest(unittest.TestCase):
    ROWS = [("the institution holds", "这个机构说了算"),
            ("the institution is large", "那个制度很大"),
            ("the institution acts", "这个组织强大"),
            ("an institutional memory", "制度性的记忆"),
            ("institutionally sound", "在制度上稳固"),
            ("this institutional habit persists", "这种习性仍然存在"),  # 未归类
            ("institution unknown", ""),                                 # 未译（空 Dest）
            ("institution", "institution")]                               # 未译（Dest==Source）

    def test_untranslated_rows_excluded(self):
        res = fc.census(self.ROWS, "institution", ["机构", "制度", "组织"])
        # 空 Dest 与 Dest==Source 两行都不计
        self.assertEqual(res["translated_rows"], 6)

    def test_longest_form_wins(self):
        # 「等级体系」含「等级」，长形必须优先，否则子串吃掉长形
        rows = [("hierarchy owns it", "哪一套等级体系")]
        res = fc.census(rows, "hierarchy", ["等级", "等级体系"])
        self.assertEqual(res["forms"], [("等级体系", 1)])
        self.assertEqual(res["unclassified"], 0)

    def test_unclassified_bucket(self):
        res = fc.census(self.ROWS, "institution", ["机构", "制度", "组织"])
        self.assertEqual(res["unclassified"], 1)   # 「这种习性仍然存在」
        self.assertEqual(len(res["_unclassified_sites"]), 1)

    def test_no_forms_gives_all_unclassified(self):
        res = fc.census(self.ROWS, "institution", [])
        self.assertEqual(res["unclassified"], res["translated_rows"])
        self.assertEqual(res["forms"], [])

    def test_anchor_is_regex_and_case_insensitive(self):
        rows = [("Confidence rests", "自信"),
                ("a confident army", "自信的军队")]
        res = fc.census(rows, r"confiden(ce|t)", ["信心", "自信"])
        self.assertEqual(res["translated_rows"], 2)
        self.assertEqual(res["forms"], [("自信", 2)])


class BatchBreakdownTest(unittest.TestCase):
    def test_local_majority_detected_and_diverges(self):
        # 全库 机构 3，INFO-069 批内 制度 1 / 机构 1（平票取先到者）
        rows = [("institution", "机构"), ("institution", "机构"),
                ("institution", "机构"), ("institution", "制度"),
                ("institution", "机构")]
        owner = {0: "INFO-001", 1: "INFO-001", 2: "INFO-001",
                 3: "INFO-069", 4: "INFO-069"}
        res = fc.census(rows, "institution", ["机构", "制度"])
        bb = fc.batch_breakdown(res["_per_row"], owner)
        by = {b["batch"]: b for b in bb}
        self.assertEqual(by["INFO-001"]["local_major"], "机构")
        self.assertEqual(by["INFO-069"]["local_major"], "制度")
        self.assertNotEqual(by["INFO-069"]["local_major"],
                            res["forms"][0][0], "该场景必须构成背离")

    def test_single_row_batch_not_flagged(self):
        rows = [("institution", "制度"), ("institution", "机构")]
        owner = {0: "INFO-069", 1: "INFO-001"}
        res = fc.census(rows, "institution", ["机构", "制度"])
        bb = fc.batch_breakdown(res["_per_row"], owner)
        one = [b for b in bb if b["batch"] == "INFO-069"][0]
        # 1 行批次不构成「多数」，主会话看分布即可，不制造噪声告警
        self.assertEqual(one["rows"], 1)

    def test_unknown_idx_skipped(self):
        res = fc.census([("institution", "机构")], "institution", ["机构"])
        self.assertEqual(fc.batch_breakdown(res["_per_row"], {}), [])


class ContractFormsTest(unittest.TestCase):
    def _contract(self, td):
        p = Path(td) / "c.json"
        p.write_text(json.dumps({"terms": {
            "personhood": {"source": "personhood", "target": "为人",
                           "match": {"accepted": ["为人", "人格", "独立人格"]}},
            "mechanism": {"source": "mechanism", "target": "机制",
                          "match": {"accepted": ["机制"]}},
        }}, ensure_ascii=False), encoding="utf-8")
        return p

    def test_forms_pulled_from_contract(self):
        with tempfile.TemporaryDirectory() as td:
            got = fc.forms_from_contract(self._contract(td), "personhood")
            self.assertEqual(set(got), {"为人", "人格", "独立人格"})

    def test_longest_first_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            got = fc.forms_from_contract(self._contract(td), "personhood")
            self.assertEqual(got[0], "独立人格", "长形必须排前面")

    def test_missing_file_returns_empty_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fc.forms_from_contract(Path(td) / "nope.json", "x"), [])

    def test_unknown_anchor_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fc.forms_from_contract(self._contract(td),
                                                    "institution"), [])


class AutoFormsTest(unittest.TestCase):
    """开集形态发现（词表无该锚时的兜底）。

    要裁决的词往往还没进词表——这恰恰是最需要查分布的情形，
    此时若全部落「未归类」，工具等于不可用。
    """

    def test_dominant_form_ranks_first(self):
        dests = ["他们不同意", "这不是同意", "未经同意", "他的同意", "同意早已不在"]
        got = fc.auto_forms(dests, top=3)
        self.assertEqual(got[0][0], "同意")

    def test_splits_sense_clusters(self):
        dests = ["这不是营救", "那是营救", "营救没有发生", "某种营救", "一次营救"]
        forms = dict(fc.auto_forms(dests, top=6))
        self.assertIn("营救", forms)
        self.assertEqual(forms["营救"], 5)

    def test_counts_each_gram_once_per_row(self):
        # 同一行里「同意」出现两次只计一次，否则长句会刷榜
        got = dict(fc.auto_forms(["同意…同意…同意"]))
        self.assertEqual(got["同意"], 1)

    def test_ignores_non_cjk(self):
        got = dict(fc.auto_forms(["reach and hold abc"]))
        self.assertEqual(got, {})

    def test_empty_input_is_safe(self):
        self.assertEqual(fc.auto_forms([]), [])
        self.assertEqual(fc.auto_forms([None, ""]), [])

    def test_sites_exposed_for_auto_forms(self):
        rows = [("consent", "这算同意"), ("consent", "那不算同意"),
                ("consent", "")]
        res = fc.census(rows, "consent", [])
        self.assertEqual(len(res["sites"]), 2, "sites 应只含已译行")


class CliTest(unittest.TestCase):
    def _args(self, td, *extra):
        root = Path(td)
        src, can = root / "s.xml", root / "c.xml"
        xml(src, [("the institution holds", "这个机构说了算"),
                  ("the institution acts", "那个制度很强")])
        xml(can, [("the institution holds", "这个机构说了算"),
                  ("the institution acts", "那个制度很强")])
        return [sys.executable, str(SCRIPT), "--stem", "S",
                "--source-xml", str(src), "--xml", str(can),
                "--contract", str(root / "none.json")] + list(extra)

    def test_missing_xml_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--stem", "S",
                 "--source-xml", str(Path(td) / "nope.xml"),
                 "--xml", str(Path(td) / "nope2.xml"),
                 "--anchor", "institution"],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 2)
            self.assertIn("不存在", r.stderr)

    def test_reports_corpus_majority(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                self._args(td, "--anchor", "institution",
                           "--forms", "机构", "制度"),
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("全库主形: 机构", r.stdout)
            self.assertIn("收敛方向应取主形", r.stdout)

    def test_out_writes_json_without_private_keys(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sub" / "census.json"
            r = subprocess.run(
                self._args(td, "--anchor", "institution",
                           "--forms", "机构", "制度", "--out", str(out)),
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["anchors"][0]["forms"],
                             [["机构", 1], ["制度", 1]])
            self.assertIn("divergences", data)
            # 内部下划线键不得进落盘结果
            self.assertNotIn("_per_row", data["anchors"][0])

    def test_count_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, c = root / "s.xml", root / "c.xml"
            xml(s, [("a", "甲"), ("b", "乙")])
            xml(c, [("a", "甲")])
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--stem", "S",
                 "--source-xml", str(s), "--xml", str(c),
                 "--contract", str(root / "n.json"), "--anchor", "a"],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 2)
            self.assertIn("不一致", r.stderr)


if __name__ == "__main__":
    unittest.main()
