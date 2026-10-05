#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_batch_evidence.py 的回归测试。

要锁的失效形态是**结构化伪证**——子代理把查证写进 notes（纯中文散文），
证据不可核验因而不可信。改成 `evidence` 结构字段后，伪证必须能被重跑抓出来。
测试用假词典，不依赖真实词典内容。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "skyrim-xml-tools" / "scripts"))

import check_batch_evidence as cbe  # noqa: E402

DICT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SSTXMLRessources>
  <Params><Addon>x</Addon><Source>english</Source><Dest>chinese</Dest></Params>
  <Content>
    <String List="0"><EDID>a</EDID><REC>SPEL:FULL</REC><Source>Shrine</Source><Dest>祭坛</Dest></String>
    <String List="0"><EDID>b</EDID><REC>SPEL:FULL</REC><Source>Coffer</Source><Dest>箱子</Dest></String>
    <String List="0"><EDID>c</EDID><REC>SPEL:FULL</REC><Source>Coffer</Source><Dest>箱子</Dest></String>
    <String List="0"><EDID>d</EDID><REC>SPEL:FULL</REC><Source>Coffer</Source><Dest>箱子</Dest></String>
  </Content>
</SSTXMLRessources>"""


class EvidenceCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        d = root / "dict"
        d.mkdir()
        (d / "Skyrim_english_chinese.xml").write_text(DICT_XML, encoding="utf-8")
        self.dictdir = d
        self.official = cbe.load_official(["Shrine", "Coffer", "Nonexistent"], d)
        self.bd = root / "NI-TEST-001"
        self.bd.mkdir()

    def _write_map(self, entries):
        (self.bd / "map.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def test_official_probe_reads_dictionary(self):
        self.assertEqual(self.official["Shrine"]["hits"], 1)
        self.assertEqual(self.official["Coffer"]["hits"], 3)
        self.assertEqual(self.official["Nonexistent"]["hits"], 0)

    def test_honest_citation_passes(self):
        ev = {"lookup": "Shrine", "exact_hits": 1, "zh_forms": ["祭坛"]}
        self.assertEqual(cbe.check_entry("1", ev, self.official), [])

    def test_fabricated_zh_form_is_caught(self):
        """实测失效形态：库内零命中的形被写成「库内既成面」。"""
        ev = {"lookup": "Shrine", "exact_hits": 1, "zh_forms": ["神龛"]}
        problems = cbe.check_entry("1", ev, self.official)
        self.assertTrue(any("无重合" in p for p in problems))

    def test_fabricated_hit_count_is_caught(self):
        ev = {"lookup": "Nonexistent", "exact_hits": 15}
        problems = cbe.check_entry("1", ev, self.official)
        self.assertTrue(any("零命中" in p for p in problems))

    def test_hit_count_within_tolerance_passes(self):
        """词典会更新，个位数差异不该判编造。"""
        ev = {"lookup": "Coffer", "exact_hits": 2, "zh_forms": ["箱子"]}
        self.assertEqual(cbe.check_entry("1", ev, self.official), [])

    def test_missing_lookup_flagged(self):
        problems = cbe.check_entry("1", {"exact_hits": 3}, self.official)
        self.assertTrue(any("缺 lookup" in p for p in problems))

    def test_end_to_end_exit_code_and_findings(self):
        self._write_map({
            "1": {"translation": "祭坛", "notes": "纯中文",
                  "evidence": {"lookup": "Shrine", "exact_hits": 1, "zh_forms": ["祭坛"]}},
            "2": {"translation": "神龛", "notes": "纯中文",
                  "evidence": {"lookup": "Shrine", "exact_hits": 1, "zh_forms": ["神龛"]}},
            "3": {"translation": "无据", "notes": "纯中文"},
        })
        rc = cbe.main(["--batch-dir", str(self.bd), "--dictionary", str(self.dictdir)])
        self.assertEqual(rc, 1)  # 有不一致
        data = json.loads(self._write_and_read_json())
        self.assertEqual([f["xml_index"] for f in data["findings"]], [2])
        self.assertEqual(data["no_evidence"], 1)

    def test_end_to_end_clean_exit_zero(self):
        self._write_map({
            "1": {"translation": "祭坛", "notes": "纯中文",
                  "evidence": {"lookup": "Shrine", "exact_hits": 1, "zh_forms": ["祭坛"]}},
        })
        rc = cbe.main(["--batch-dir", str(self.bd), "--dictionary", str(self.dictdir)])
        self.assertEqual(rc, 0)

    def _write_and_read_json(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cbe.main(["--batch-dir", str(self.bd), "--dictionary", str(self.dictdir), "--json"])
        return buf.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)
