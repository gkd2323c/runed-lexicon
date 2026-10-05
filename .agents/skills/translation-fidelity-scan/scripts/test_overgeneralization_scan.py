#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""overgeneralization_scan.py 的回归测试。

CI 的 `unittest discover` 会跳过不含字面量 `unittest` 的测试文件，故用 unittest。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "translation-quality-gate" / "scripts"))

from overgeneralization_scan import load_rows, load_terms, scan  # noqa: E402

XML_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<SSTXMLRessources>\n  <Params>\n    <Source>english</Source>\n'
            '    <Dest>chinese</Dest>\n  </Params>\n  <Content>\n')
XML_TAIL = '  </Content>\n</SSTXMLRessources>\n'


def _string(src, dst, rec="DIAL:FULL", edid="Test01"):
    return (f'    <String List="0">\n      <EDID>{edid}</EDID>\n'
            f'      <REC>{rec}</REC>\n      <Source>{src}</Source>\n'
            f'      <Dest>{dst}</Dest>\n    </String>\n')


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.rows = [
            ("Is it because he's an Elf?", "是因为他是傲特莫吗？"),  # 0 真缺陷
            ("Bolwing of Artaeum", "阿塔姆的博尔温"),                  # 1 源文有锚点
            ("Xyzzy", "Xyzzy"),                                    # 2 未译行
            ("Find the Vampire Hunter", "找到那位猎魔人"),            # 3 子串误报位
            ("I caught a rockbug", "我抓到一只岩石虫"),               # 4 源文无该词条锚
        ]
        self.xml = root / "canon.xml"
        self.xml.write_text(
            XML_HEAD + "".join(_string(*p) for p in self.rows) + XML_TAIL, encoding="utf-8")
        self.contract = root / "c.json"
        self.contract.write_text(json.dumps({"terms": {
            "ssi.altmer": {"english": "Altmer", "zh": "傲特莫"},
            "ssi.artaeum": {"english": "Artaeum", "zh": "阿塔姆"},
            "ssi.dremora": {"english": "Dremora", "zh": "魔人"},
        }}, ensure_ascii=False), encoding="utf-8")

    def test_untranslated_rows_excluded(self):
        # 未译行 Source == Dest，没有「译名」可言
        self.assertEqual(len(load_rows(self.xml)), 4)

    def test_terms_without_zh_or_english_skipped(self):
        self.contract.write_text(json.dumps({"terms": {
            "a": {"english": "A", "zh": "甲"},
            "b": {"english": "B"},
            "c": {"zh": "丙"},
        }}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual([t["term_id"] for t in load_terms(self.contract)], ["a"])

    def test_core_rule(self):
        """核心：译文有中文形、源文无英文锚 → 报。"""
        found = scan(load_rows(self.xml), load_terms(self.contract))
        idxs = {f["xml_index"] for f in found}
        self.assertIn(0, idxs)                 # Elf 类别被译成傲特莫
        self.assertNotIn(1, idxs)              # 源文明写 Artaeum
        self.assertNotIn(4, idxs)              # 岩石虫 不属任何词条
        by_idx = {f["xml_index"]: f for f in found}
        self.assertEqual(by_idx[0]["english"], "Altmer")
        self.assertEqual(by_idx[0]["dest"], "是因为他是傲特莫吗？")

    def test_reports_substring_artifact_as_candidate(self):
        """中文无词边界：魔人 命中 猎魔人。工具照报，裁决是人的事。"""
        found = scan(load_rows(self.xml), load_terms(self.contract))
        by_idx = {f["xml_index"]: f for f in found}
        self.assertIn(3, by_idx)
        self.assertEqual(by_idx[3]["english"], "Dremora")

    def test_only_term_filter(self):
        self.assertEqual(
            scan(load_rows(self.xml), load_terms(self.contract), only_term="Artaeum"), [])

    def test_findings_sorted(self):
        found = scan(load_rows(self.xml), load_terms(self.contract))
        keys = [(f["term_id"], f["xml_index"]) for f in found]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main(verbosity=2)
