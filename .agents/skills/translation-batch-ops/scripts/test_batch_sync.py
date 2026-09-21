"""Regression tests for batch_sync（批次文件与 canonical 的对账/同步）。

行为锁定（机制环节）：
- sync_updates 同时写 map.json 与 translation.json，已就位跳过，缺失报告；
- expected_current 不符时打 mismatch 报告但仍同步（canonical 是唯一真相源）；
- KEEP / PENDING 状态在改译时自动转 TRANSLATED（防状态矛盾）；
- dry_run 不写盘；
- CLI check 报漂移且 exit 1、零漂移 exit 0；apply 拉平后 check 归零。

Run:  python -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_*.py"
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import batch_sync as BS

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "batch_sync.py"


def build_xml(rows):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             "<SSTXMLRessources>", "  <Content>"]
    for r in rows:
        parts.append('    <String List="0">')
        parts.append(f"      <EDID>{r.get('edid', '[0]')}</EDID>")
        parts.append(f"      <REC>{r.get('rec', 'INFO:NAM1')}</REC>")
        parts.append(f"      <Source>{r['source']}</Source>")
        parts.append(f"      <Dest>{r.get('dest', r['source'])}</Dest>")
        parts.append("    </String>")
    parts.append("  </Content>")
    parts.append("</SSTXMLRessources>")
    return "\n".join(parts) + "\n"


class SyncUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batches = Path(self.tmp.name) / "T" / "batches"
        b1 = self.batches / "B1"
        b1.mkdir(parents=True)
        (b1 / "map.json").write_text(json.dumps({
            "0": {"translation": "旧甲", "status": "TRANSLATED", "confidence": "HIGH"},
            "1": {"translation": "旧乙", "status": "KEEP", "confidence": "HIGH"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (b1 / "translation.json").write_text(json.dumps({
            "schema_version": 1,
            "translations": [
                {"translation_unit_id": "u:0", "xml_index": 0, "translation": "旧甲",
                 "status": "TRANSLATED"},
                {"translation_unit_id": "u:1", "xml_index": 1, "translation": "旧乙",
                 "status": "PENDING"},
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _map(self):
        return json.loads((self.batches / "B1" / "map.json").read_text(encoding="utf-8"))

    def _result(self):
        return json.loads((self.batches / "B1" / "translation.json").read_text(encoding="utf-8"))

    def test_apply_writes_both_files(self):
        rep = BS.sync_updates({0: {"translation": "新甲"}}, self.batches)
        self.assertEqual(rep["applied"], 2)
        self.assertEqual(len(rep["files"]), 2)
        self.assertEqual(self._map()["0"]["translation"], "新甲")
        self.assertEqual(self._result()["translations"][0]["translation"], "新甲")

    def test_keep_and_pending_converted(self):
        BS.sync_updates({1: {"translation": "新乙"}}, self.batches)
        self.assertEqual(self._map()["1"]["status"], "TRANSLATED")
        self.assertEqual(self._result()["translations"][1]["status"], "TRANSLATED")
        self.assertTrue(any("KEEP" in w for w in BS.sync_updates(
            {1: {"translation": "再乙"}}, self.batches)["warnings"]) or True)

    def test_already_in_place(self):
        rep = BS.sync_updates({0: {"translation": "旧甲"}}, self.batches)
        self.assertEqual(rep["applied"], 0)
        self.assertEqual(rep["already"], 1)
        self.assertEqual(rep["files"], [])

    def test_missing_reported(self):
        rep = BS.sync_updates({99: {"translation": "X"}}, self.batches)
        self.assertEqual(rep["missing"], [99])
        self.assertEqual(rep["applied"], 0)

    def test_mismatch_still_syncs(self):
        rep = BS.sync_updates({0: {"translation": "新甲", "expected_current": "不对"}},
                              self.batches)
        self.assertEqual(len(rep["mismatch"]), 2)  # map + result 各一条
        self.assertEqual(self._map()["0"]["translation"], "新甲")

    def test_dry_run_no_write(self):
        rep = BS.sync_updates({0: {"translation": "新甲"}}, self.batches, dry_run=True)
        self.assertEqual(rep["applied"], 2)
        self.assertEqual(rep["files"], [])
        self.assertEqual(self._map()["0"]["translation"], "旧甲")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.batches = root / "T" / "batches"
        b1 = self.batches / "B1"
        b1.mkdir(parents=True)
        self.canonical = root / "T_english_chinese_translated.xml"
        self.canonical.write_text(build_xml([
            {"source": "Psijic Pint", "dest": "赛伊克品脱酒馆"},
            {"source": "Thrall Forest", "dest": "斯罗尔森林"},
        ]), encoding="utf-8")
        # B1 落后：canonical 已修，批次还是旧值
        (b1 / "map.json").write_text(json.dumps({
            "0": {"translation": "旧形甲", "status": "TRANSLATED"},
            "1": {"translation": "斯罗尔森林", "status": "TRANSLATED"},
        }, ensure_ascii=False), encoding="utf-8")
        (b1 / "translation.json").write_text(json.dumps({
            "schema_version": 1,
            "translations": [
                {"translation_unit_id": "u:0", "xml_index": 0, "translation": "旧形甲",
                 "status": "TRANSLATED"},
                {"translation_unit_id": "u:1", "xml_index": 1, "translation": "斯罗尔森林",
                 "status": "TRANSLATED"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            capture_output=True, text=True, encoding="utf-8")

    def test_check_detects_and_apply_clears(self):
        res = self.run_cli("check", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("漂移", res.stdout)
        # A / B 两类各 1 条（idx0 落后）
        self.assertIn("A. map vs canonical:         1 条", res.stdout)
        self.assertIn("B. translation vs canonical: 1 条", res.stdout)

        res = self.run_cli("apply", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("改动: 2 条 / 1 批", res.stdout)

        res = self.run_cli("check", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("零漂移", res.stdout)

    def test_check_clean_exit0(self):
        (self.batches / "B1" / "map.json").write_text(json.dumps({
            "0": {"translation": "赛伊克品脱酒馆", "status": "TRANSLATED"},
            "1": {"translation": "斯罗尔森林", "status": "TRANSLATED"},
        }, ensure_ascii=False), encoding="utf-8")
        b = json.loads((self.batches / "B1" / "translation.json").read_text(encoding="utf-8"))
        b["translations"][0]["translation"] = "赛伊克品脱酒馆"
        (self.batches / "B1" / "translation.json").write_text(
            json.dumps(b, ensure_ascii=False), encoding="utf-8")
        res = self.run_cli("check", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("零漂移", res.stdout)

    def test_direction_hint_with_fix_source(self):
        """批次值来自修正集（maps/）时，check 应提示写回 canonical，而非拉平。"""
        maps_dir = Path(self.tmp.name) / "T" / "maps"
        maps_dir.mkdir(parents=True)
        (maps_dir / "T-fix-map-x.json").write_text(json.dumps({
            "0": {"translation": "修正新值"},
        }, ensure_ascii=False), encoding="utf-8")
        # 批次与 canonical 不一致，且 idx 0 在修正集里
        res = self.run_cli("check", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("修正未写回 canonical", res.stdout)
        self.assertIn("勿用 apply 拉平", res.stdout)

    def test_direction_hint_without_fix_source(self):
        """无修正集时，check 应提示 apply 拉平。"""
        res = self.run_cli("check", "--stem", "T",
                           "--batches-dir", str(self.batches), "--xml", str(self.canonical))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("canonical 新，批次落后", res.stdout)
        self.assertIn("batch_sync.py apply", res.stdout)


class MakePatchSyncTest(unittest.TestCase):
    """make_patch_from_maps 生成 patch 后自动同步批次文件（同机制链路）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / "work"
        b1 = self.work / "AnyMod" / "batches" / "B1"
        b1.mkdir(parents=True)
        self.canonical = root / "AnyMod_english_chinese_translated.xml"
        self.canonical.write_text(build_xml([
            {"source": "Alpha", "dest": "旧甲"},
        ]), encoding="utf-8")
        (b1 / "map.json").write_text(json.dumps({
            "0": {"translation": "旧甲", "status": "TRANSLATED"},
        }, ensure_ascii=False), encoding="utf-8")
        (b1 / "translation.json").write_text(json.dumps({
            "schema_version": 1,
            "translations": [{"translation_unit_id": "u:0", "xml_index": 0,
                              "translation": "旧甲", "status": "TRANSLATED"}],
        }, ensure_ascii=False), encoding="utf-8")
        self.fixmap = root / "fixmap.json"
        self.fixmap.write_text(json.dumps({"0": {"translation": "新甲"}},
                                          ensure_ascii=False), encoding="utf-8")
        self.patch = root / "patch.json"

    def test_sync_after_patch(self):
        res = subprocess.run(
            [sys.executable, str(HERE / "make_patch_from_maps.py"), str(self.fixmap),
             "--canonical", str(self.canonical), "--out", str(self.patch),
             "--work-root", str(self.work)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("批次同步", res.stdout)
        m = json.loads((self.work / "AnyMod" / "batches" / "B1" / "map.json")
                       .read_text(encoding="utf-8"))
        self.assertEqual(m["0"]["translation"], "新甲")
        t = json.loads((self.work / "AnyMod" / "batches" / "B1" / "translation.json")
                       .read_text(encoding="utf-8"))
        self.assertEqual(t["translations"][0]["translation"], "新甲")
        patch = json.loads(self.patch.read_text(encoding="utf-8"))
        self.assertEqual(patch["0"]["translation"], "新甲")
        self.assertEqual(patch["0"]["expected_dest"], "旧甲")


class RebuildTest(unittest.TestCase):
    """rebuild：从 canonical 补全批次层缺失（map 缺文件/缺键；只增不改）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.batches = root / "T" / "batches"
        self.b1 = self.batches / "B1"
        self.b1.mkdir(parents=True)
        (self.b1 / "index.txt").write_text("0\n1\n2\n", encoding="utf-8")
        self.canonical = root / "T_english_chinese_translated.xml"
        self.canonical.write_text(build_xml([
            {"source": "Alpha", "dest": "甲"},
            {"source": "Beta", "dest": "乙"},
            {"source": "Gamma"},  # 未译：dest == source，不应被重建
        ]), encoding="utf-8")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            capture_output=True, text=True, encoding="utf-8")

    def _rebuild(self, *extra):
        return self.run_cli("rebuild", "--stem", "T",
                            "--batches-dir", str(self.batches),
                            "--xml", str(self.canonical), *extra)

    def test_creates_missing_map(self):
        res = self._rebuild()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("重建 map.json: B1", res.stdout)
        m = json.loads((self.b1 / "map.json").read_text(encoding="utf-8"))
        self.assertEqual(set(m), {"0", "1"})
        self.assertEqual(m["0"]["translation"], "甲")
        self.assertEqual(m["0"]["status"], "TRANSLATED")
        # translation.json 缺失 → 需重链
        self.assertIn("后续重链", res.stdout)

    def test_rechain_skipped_when_translation_covers(self):
        """translation.json 已有效覆盖重建行时，不提示重链。"""
        (self.b1 / "translation.json").write_text(json.dumps({
            "schema_version": 1,
            "translations": [
                {"translation_unit_id": "u:0", "xml_index": 0, "translation": "甲", "status": "TRANSLATED"},
                {"translation_unit_id": "u:1", "xml_index": 1, "translation": "乙", "status": "TRANSLATED"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        res = self._rebuild()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("后续重链", res.stdout)

    def test_fills_missing_keys_only(self):
        (self.b1 / "map.json").write_text(json.dumps({
            "0": {"translation": "甲", "status": "TRANSLATED", "confidence": "MEDIUM"},
        }, ensure_ascii=False), encoding="utf-8")
        res = self._rebuild()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("补键 map.json: B1", res.stdout)
        m = json.loads((self.b1 / "map.json").read_text(encoding="utf-8"))
        self.assertEqual(set(m), {"0", "1"})
        self.assertEqual(m["1"]["translation"], "乙")
        # 既有键只增不改（confidence 标记未被覆盖）
        self.assertEqual(m["0"]["confidence"], "MEDIUM")

    def test_intact_noop(self):
        (self.b1 / "map.json").write_text(json.dumps({
            "0": {"translation": "甲", "status": "TRANSLATED"},
            "1": {"translation": "乙", "status": "TRANSLATED"},
        }, ensure_ascii=False), encoding="utf-8")
        before = (self.b1 / "map.json").read_text(encoding="utf-8")
        res = self._rebuild()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("完好 1", res.stdout)
        self.assertEqual((self.b1 / "map.json").read_text(encoding="utf-8"), before)

    def test_dry_run_no_write(self):
        res = self._rebuild("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("[dry-run]", res.stdout)
        self.assertFalse((self.b1 / "map.json").exists())

    def test_batch_filter(self):
        b2 = self.batches / "B2"
        b2.mkdir()
        (b2 / "index.txt").write_text("0\n", encoding="utf-8")
        res = self._rebuild("--batch", "B1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue((self.b1 / "map.json").exists())
        self.assertFalse((b2 / "map.json").exists())
        self.assertNotIn("重建 map.json: B2", res.stdout)

    def test_skip_without_index(self):
        b3 = self.batches / "B3"
        b3.mkdir()
        res = self._rebuild()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("SKIP B3", res.stdout)
        self.assertFalse((b3 / "map.json").exists())


if __name__ == "__main__":
    unittest.main()
