"""Regression tests for scan_plan_gaps: unclaimed-row detection and gap batching.

Behavior locked in:
- INFO plans only claim linked NAM1 rows; INFO:RNAM and unlinked INFO rows can be
  claimed by no plan at all, and plan-based coverage tools cannot see them — the
  scan must surface such rows as gaps.
- Re-scans must be idempotent: an existing gaps plan counts as claimed, so a
  second scan reports the remaining gaps only (normally zero).

Run:  python -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_*.py"
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import scan_plan_gaps as G

MINI_XML = """<?xml version="1.0" encoding="utf-8"?>
<Content>
  <String List="0" sID="0001">
    <EDID>[000001]</EDID>
    <REC id="4" idMax="1">INFO:NAM1</REC>
    <Source>Alpha line</Source>
    <Dest>Alpha line</Dest>
  </String>
  <String List="0" sID="0002">
    <EDID>[000002]</EDID>
    <REC id="4" idMax="1">INFO:NAM1</REC>
    <Source>Beta line</Source>
    <Dest>译文乙</Dest>
  </String>
  <String List="0" sID="0003">
    <EDID>[000003]</EDID>
    <REC id="4" idMax="1">INFO:RNAM</REC>
    <Source>Gamma line</Source>
    <Dest>Gamma line</Dest>
  </String>
  <String List="0" sID="0004">
    <EDID>[000004]</EDID>
    <REC id="4" idMax="1">INFO:NAM1</REC>
    <Source>Alpha line</Source>
    <Dest>Alpha line</Dest>
  </String>
  <String List="0" sID="0005">
    <EDID>MESG01</EDID>
    <REC id="1" idMax="1">MESG:DESC</REC>
    <Source>Delta notify</Source>
    <Dest>Delta notify</Dest>
  </String>
  <String List="0" sID="0006">
    <EDID>MESG02</EDID>
    <REC id="1" idMax="1">MESG:DESC</REC>
    <Source></Source>
    <Dest></Dest>
  </String>
  <String List="0" sID="0007">
    <EDID>MESG03</EDID>
    <REC id="1" idMax="1">MESG:DESC</REC>
    <Source>   </Source>
    <Dest>   </Dest>
  </String>
</Content>
"""


class ScanGapsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.xml = root / "MiniMOD_english_chinese_translated.xml"
        self.xml.write_text(MINI_XML, encoding="utf-8")
        self.plan = root / "MiniMOD-info-batches.json"
        self.plan.write_text(json.dumps(
            {"batches": [{"id": "INFO-001", "idx": [2]}]}), encoding="utf-8")
        self.work_root = root / ".work"
        self.stem = "MiniMOD"

    def _run(self, *extra):
        argv = ["scan_plan_gaps.py", "--xml", str(self.xml), "--plan", str(self.plan),
                "--stem", self.stem, "--work-root", str(self.work_root), *extra]
        old = sys.argv
        try:
            sys.argv = argv
            return G.main()
        finally:
            sys.argv = old

    def test_detects_unclaimed_rows(self):
        rows = G.load_untranslated(self.xml)
        self.assertEqual([r["index"] for r in rows], [0, 2, 3, 4])
        planned, _ = G.load_planned([self.plan])
        self.assertEqual(planned, {2})
        gaps = [r for r in rows if r["index"] not in planned]
        self.assertEqual([r["index"] for r in gaps], [0, 3, 4])

    def test_whitespace_only_rows_are_not_untranslated(self):
        # Whitespace-only placeholders stay Source==Dest forever; they carry no
        # translatable content and must not surface as permanent gaps.
        rows = G.load_untranslated(self.xml)
        self.assertNotIn(5, [r["index"] for r in rows])
        self.assertNotIn(6, [r["index"] for r in rows])

    def test_build_batches_keeps_sources_whole(self):
        gaps = [
            {"index": 0, "rec": "INFO:NAM1", "source": "Alpha line"},
            {"index": 3, "rec": "INFO:NAM1", "source": "Alpha line"},
            {"index": 4, "rec": "MESG:DESC", "source": "Delta notify"},
        ]
        batches = G.build_batches(gaps, max_unique=60, max_rows=150)
        self.assertEqual([b["id"] for b in batches], ["GAP-INFO-001", "GAP-MESG-001"])
        self.assertEqual(batches[0]["idx"], [0, 3])
        self.assertEqual(batches[0]["unique_src"], 1)
        self.assertEqual(batches[1]["idx"], [4])

    def test_write_plan_and_index(self):
        rc = self._run("--write-plan")
        self.assertEqual(rc, 0)
        plan_path = self.work_root / self.stem / "context" / "MiniMOD-gaps-batches.json"
        self.assertTrue(plan_path.is_file())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        ids = [b["id"] for b in plan["batches"]]
        self.assertEqual(ids, ["GAP-INFO-001", "GAP-MESG-001"])
        idx_txt = (self.work_root / self.stem / "batches" / "GAP-INFO-001" / "index.txt")
        self.assertEqual(idx_txt.read_text(encoding="utf-8"), "0\n3\n")

    def test_rescan_after_write_reports_zero(self):
        self.assertEqual(self._run("--write-plan"), 0)
        rc = self._run("--fail-on-gaps")
        self.assertEqual(rc, 0, "claims from the gaps plan must make the rescan clean")

    def test_write_plan_refuses_existing_plan(self):
        # A stale gaps plan exists (claims nothing), while real gaps remain:
        # --write-plan must refuse to overwrite it.
        ctx = self.work_root / self.stem / "context"
        ctx.mkdir(parents=True)
        (ctx / "MiniMOD-gaps-batches.json").write_text(
            json.dumps({"batches": []}), encoding="utf-8")
        rc = self._run("--write-plan")
        self.assertEqual(rc, 2, "existing gaps plan must be protected unless --force")

    def test_fail_on_gaps_returns_one(self):
        rc = self._run("--fail-on-gaps")
        self.assertEqual(rc, 1)

    def test_refuses_batch_dir_with_active_map(self):
        bdir = self.work_root / self.stem / "batches" / "GAP-INFO-001"
        bdir.mkdir(parents=True)
        (bdir / "map.json").write_text("{}", encoding="utf-8")
        rc = self._run("--write-plan")
        self.assertEqual(rc, 2, "must refuse to rewrite an active batch dir")

    # ---- 机械匹配孤儿（已译未认领）----

    def test_matched_orphan_detection(self):
        untranslated, matched, total = G.load_all_rows(self.xml)
        self.assertEqual(total, 7)
        self.assertEqual([r["index"] for r in untranslated], [0, 2, 3, 4])
        # idx1 (Beta line -> 译文乙) 已译且不在计划内：机械匹配孤儿
        self.assertEqual([r["index"] for r in matched], [1])
        planned, _ = G.load_planned([self.plan])
        orphans = [r for r in matched if r["index"] not in planned]
        self.assertEqual([r["index"] for r in orphans], [1])

    def test_orphan_list_written(self):
        out = Path(self.tmp.name) / "orphans.json"
        rc = self._run("--orphan-list", str(out))
        self.assertEqual(rc, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([d["idx"] for d in data], [1])
        self.assertEqual(data[0]["dest"], "译文乙")

    def test_fail_on_orphans_and_verification(self):
        # 未核验：卡点退出码 1
        self.assertEqual(self._run("--fail-on-orphans"), 1)
        # 核验清单（数组形态）扣除后归零
        verified = Path(self.tmp.name) / "verified.json"
        verified.write_text(json.dumps([1]), encoding="utf-8")
        self.assertEqual(
            self._run("--fail-on-orphans", "--verified-orphans", str(verified)), 0)
        # {"verified": [...]} 对象形态同样接受
        verified.write_text(json.dumps({"verified": [1]}), encoding="utf-8")
        self.assertEqual(
            self._run("--fail-on-orphans", "--verified-orphans", str(verified)), 0)

    def test_load_verified_orphans_formats(self):
        p = Path(self.tmp.name) / "v2.json"
        p.write_text(json.dumps([5, {"idx": 7}]), encoding="utf-8")
        self.assertEqual(G.load_verified_orphans(str(p)), {5, 7})
        self.assertEqual(G.load_verified_orphans(None), set())


if __name__ == "__main__":
    unittest.main()
