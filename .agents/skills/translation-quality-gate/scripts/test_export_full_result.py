"""全库 result 导出器的回归测试。

锁定的事故：批次级 gate 报告的 units_checked 远小于全库行数（Artaeum 实测 74 /
14968），据此声明全库通过是错的——该口径下漏掉了 `圣母`（现实宗教禁令）违规，
因为它所在的批次早已写回、此后不再经过任何 gate。导出全库 result 后复跑 gate
才能覆盖已写回行。

Run:  python -m unittest discover -s .agents/skills/translation-quality-gate/scripts -p "test_*.py"
"""

import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import export_full_result as E


def _xml(rows):
    """rows: [(edid, rec, source, dest)]"""
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<SSTXMLRessources>",
        "  <Params><Addon>Test.esp</Addon></Params>",
        "  <Content>",
    ]
    for edid, rec, source, dest in rows:
        parts.append("    <String List=\"0\">")
        parts.append("      <EDID>%s</EDID>" % edid)
        parts.append("      <REC>%s</REC>" % rec)
        parts.append("      <Source>%s</Source>" % source)
        parts.append("      <Dest>%s</Dest>" % dest)
        parts.append("    </String>")
    parts.append("  </Content>")
    parts.append("</SSTXMLRessources>")
    return "\n".join(parts)


class ExportFullResultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.rows = [
            ("A", "NPC_:FULL", "Nemo", "尼莫"),
            ("B", "BOOK:DESC", "1", "1"),            # 源文无语言内容 -> KEEP
            ("C", "QUST:CNAM", "Go north.", "向北走。"),
        ]
        self.src = os.path.join(self.dir, "src.xml")
        self.canon = os.path.join(self.dir, "canon.xml")
        with open(self.src, "w", encoding="utf-8") as fh:
            fh.write(_xml(self.rows))
        with open(self.canon, "w", encoding="utf-8") as fh:
            fh.write(_xml(self.rows))

    def test_every_string_becomes_a_unit(self):
        doc = E.build_result(self.src, self.canon)
        self.assertEqual(len(doc["translations"]), len(self.rows))

    def test_translated_and_keep_are_classified_by_source_equality(self):
        doc = E.build_result(self.src, self.canon)
        by_idx = {t["xml_index"]: t for t in doc["translations"]}
        self.assertEqual(by_idx[0]["status"], "TRANSLATED")
        self.assertEqual(by_idx[1]["status"], "KEEP")
        self.assertEqual(by_idx[2]["status"], "TRANSLATED")

    def test_unit_ids_and_metadata_are_preserved(self):
        doc = E.build_result(self.src, self.canon)
        first = doc["translations"][0]
        self.assertEqual(first["translation_unit_id"], "xml-index:0")
        self.assertEqual(first["edid"], "A")
        self.assertEqual(first["rec"], "NPC_:FULL")
        self.assertEqual(first["source"], "Nemo")
        self.assertEqual(first["translation"], "尼莫")
        # result 用于校验 canonical 现状，original_dest 取 Source
        self.assertEqual(first["original_dest"], "Nemo")

    def test_source_sha_is_recorded_for_identity(self):
        doc = E.build_result(self.src, self.canon)
        self.assertEqual(len(doc["context"]["xtranslator_xml"]["sha256"]), 64)
        self.assertIn("canonical_xml", doc["context"])

    def test_string_count_mismatch_is_rejected(self):
        short = os.path.join(self.dir, "short.xml")
        with open(short, "w", encoding="utf-8") as fh:
            fh.write(_xml(self.rows[:2]))
        with self.assertRaises(ValueError):
            E.build_result(short, self.canon)

    def test_output_is_gate_consumable(self):
        """导出物必须能被 quality_gate 的 result 读取路径解析。"""
        out = os.path.join(self.dir, "full.json")
        doc = E.build_result(self.src, self.canon)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        with open(out, encoding="utf-8") as fh2:
            reloaded = json.load(fh2)
        self.assertIn("translations", reloaded)
        self.assertTrue(all("xml_index" in t and "source" in t for t in reloaded["translations"]))
        # ET 可再次解析 canonical（未破坏文件）
        ET.parse(self.canon)


if __name__ == "__main__":
    unittest.main()
