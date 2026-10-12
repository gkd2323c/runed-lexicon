#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_pending.py 回归测试：canonical 已写回判定 + 与 review_coverage 共享的已审判定。

fixture 布局（tmpdir）：
  canonical 10 行；批 A/B 已译，C 纯 KEEP 已消费，D 纯 KEEP 未消费，
  E 备料未派，F 已译且记录 IN_PROGRESS，G 未写回但记录 COMPLETE。
预期：written={A,B,C,F}  pending={B,F}  in_flight 含 F  reviewed_not_written={G}
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import review_pending  # noqa: E402

STEM = "TestMod"

ROWS = [
    ("s0", "译0"), ("s1", "译1"),          # A: INFO-001 written
    ("s2", "译2"), ("s3", "译3"),          # B: INFO-002 written, no record
    ("s4", "s4"),                          # C: NI-KEEP-001 pure KEEP consumed
    ("s5", "s5"),                          # D: NI-KEEP-002 pure KEEP not consumed
    ("s6", "s6"), ("s7", "s7"),            # E: INFO-003 prepped, never dispatched
    ("s8", "译8"),                         # F: INFO-004 written, record IN_PROGRESS
    ("s9", "s9"),                          # G: INFO-005 not written, record COMPLETE
]

BATCHES = {
    "INFO-001": ([0, 1], {"0": "TRANSLATED", "1": "TRANSLATED"}),
    "INFO-002": ([2, 3], {"2": "TRANSLATED", "3": "TRANSLATED"}),
    "NI-KEEP-001": ([4], {"4": "KEEP"}),
    "NI-KEEP-002": ([5], {"5": "PENDING"}),
    "INFO-003": ([6, 7], None),  # no translation.json
    "INFO-004": ([8], {"8": "TRANSLATED"}),
    "INFO-005": ([9], {"9": "PENDING"}),
}

RECORDS = {
    "INFO-001": "COMPLETE",
    "NI-KEEP-001": "COMPLETE",
    "INFO-004": "IN_PROGRESS",
    "INFO-005": "COMPLETE",
}


def build(tmp: pathlib.Path) -> pathlib.Path:
    xml = tmp / "canonical.xml"
    body = "".join(
        f'<String List="0"><EDID>[{i:08X}]</EDID><REC>INFO:NAM1</REC>'
        f"<Source>{s}</Source><Dest>{d}</Dest></String>"
        for i, (s, d) in enumerate(ROWS))
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<SSTXMLRessources><Params><Addon>t.esp</Addon></Params><Content>{body}</Content></SSTXMLRessources>",
        encoding="utf-8")
    batches = tmp / ".work" / STEM / "batches"
    for bid, (idxs, smap) in BATCHES.items():
        d = batches / bid
        d.mkdir(parents=True)
        (d / "index.txt").write_text("\n".join(str(i) for i in idxs) + "\n", encoding="utf-8")
        if smap is not None:
            tj = {"schema_version": 1, "translations": [
                {"xml_index": int(k), "status": v, "translation": "x" if v != "PENDING" else ""}
                for k, v in smap.items()]}
            (d / "translation.json").write_text(json.dumps(tj), encoding="utf-8")
    reports = tmp / ".work" / STEM / "reports"
    reports.mkdir(parents=True)
    for bid, status in RECORDS.items():
        (reports / f"{STEM}-{bid}-review-record.json").write_text(
            json.dumps({"stem": STEM, "batch": bid, "status": status, "findings": []}),
            encoding="utf-8")
    return xml


def run(tmp: pathlib.Path, xml: pathlib.Path) -> dict:
    import contextlib
    import io
    buf = io.StringIO()
    cwd = pathlib.Path.cwd()
    try:
        import os
        os.chdir(tmp)  # find_canonical 用相对 mods/ 探测；这里显式 --xml，chdir 只为 work-root
        with contextlib.redirect_stdout(buf):
            rc = review_pending.main(["--stem", STEM, "--work-root", str(tmp / ".work"),
                                      "--xml", str(xml), "--json"])
    finally:
        os.chdir(cwd)
    assert rc == 0
    return json.loads(buf.getvalue())


class TestReviewPending(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = pathlib.Path(cls._tmp.name)
        cls.xml = build(cls.tmp)
        cls.out = run(cls.tmp, cls.xml)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_written_set(self):
        self.assertEqual(self.out["written"], ["INFO-001", "INFO-002", "INFO-004", "NI-KEEP-001"])

    def test_pending_is_written_minus_reviewed(self):
        self.assertEqual(self.out["pending"], ["INFO-002", "INFO-004"])
        self.assertEqual(self.out["pending_count"], 2)

    def test_in_flight_counts_as_pending(self):
        self.assertIn("INFO-004", self.out["pending_in_flight"])

    def test_keep_unconsumed_not_written(self):
        self.assertNotIn("NI-KEEP-002", self.out["written"])

    def test_prepped_never_dispatched_not_written(self):
        self.assertNotIn("INFO-003", self.out["written"])

    def test_reviewed_not_written_surfaced(self):
        self.assertEqual(self.out["reviewed_not_written"], ["INFO-005"])

    def test_reviewed_intersection_only(self):
        # reviewed 只报与 written 的交集（358 场景：审查先于写回）
        self.assertEqual(self.out["reviewed"], ["INFO-001", "NI-KEEP-001"])


if __name__ == "__main__":
    unittest.main()
