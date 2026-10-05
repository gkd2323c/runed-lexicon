#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_fixes 对「空占位 unit」的容错回归。

失效形态：子代理交 `map.json`（含译文），`translation.json` 仍是上一轮
`consume_batch` 留下的**空模板**（translation 为空串、status=PENDING）。
原实现对两份都做 CAS 校验，于是对着「实际 ''」报「现值不符」，让收口链在
交卷数据完全正确时失败；它还会把 map.json 的现值覆盖成空，污染
`expected_current` 守卫。

修复：`result_unit_placeholder` 按**内容**识别占位行并逐 unit 跳过。
**不是按 mtime** —— 实测踩过：apply_fixes 每次写 map.json 都会让 map.json
变新，纯 mtime 判据会导致第一次调用之后所有调用都跳过 translation.json，
双侧同步与 CAS 守卫一起失效（`test_review_tools.py` 两条当场变红）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply_fixes import apply_to_batch, result_unit_placeholder  # noqa: E402


class PlaceholderPredicateTests(unittest.TestCase):
    def test_empty_pending_is_placeholder(self):
        self.assertTrue(result_unit_placeholder({"translation": "", "status": "PENDING"}))

    def test_missing_status_is_placeholder(self):
        self.assertTrue(result_unit_placeholder({"translation": "   "}))

    def test_filled_is_not_placeholder(self):
        self.assertFalse(
            result_unit_placeholder({"translation": "旧译文", "status": "TRANSLATED"}))

    def test_keep_with_source_text_is_not_placeholder(self):
        """KEEP 的 translation 等于源文（非空），不是占位，必须照常校验。"""
        self.assertFalse(
            result_unit_placeholder({"translation": "Some Source Line", "status": "KEEP"}))

    def test_whitespace_only_counts_as_empty(self):
        self.assertTrue(result_unit_placeholder({"translation": "  \n", "status": "PENDING"}))


class ApplyToBatchPlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.fixes = {1: {"new": "新译文", "expected_current": "旧译文"}}

    def _write(self, name, payload):
        (self.dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _map_holder(self):
        self._write("map.json", {"1": {"translation": "旧译文", "status": "TRANSLATED"}})

    def _template_holder(self):
        self._write("translation.json", {
            "translations": [{"xml_index": 1, "translation": "", "status": "PENDING"}]})

    def test_placeholder_template_does_not_fail(self):
        """核心回归：空模板不得触发「现值不符」。"""
        self._map_holder()
        self._template_holder()
        out = apply_to_batch(self.dir, self.fixes)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["map_applied"], 1)
        self.assertEqual(out["result_applied"], 0)
        self.assertEqual(
            json.loads((self.dir / "map.json").read_text(encoding="utf-8"))["1"]["translation"],
            "新译文")

    def test_placeholder_not_rewritten(self):
        """占位行不得被改写——它从未持有译文，填充它等于伪造一份没跑过门禁的数据。"""
        self._map_holder()
        self._template_holder()
        result = self.dir / "translation.json"
        before = result.read_text(encoding="utf-8")
        apply_to_batch(self.dir, self.fixes)
        self.assertEqual(result.read_text(encoding="utf-8"), before)

    def test_real_divergence_still_errors(self):
        """核心反向回归：有真实译文却与 map 不一致，必须仍然拒绝。"""
        self._map_holder()
        self._write("translation.json", {
            "translations": [{"xml_index": 1, "translation": "另一版", "status": "TRANSLATED"}]})
        out = apply_to_batch(self.dir, self.fixes)
        self.assertTrue(out["errors"])
        self.assertTrue(any("不符" in e for e in out["errors"]))

    def test_map_cas_guard_untouched(self):
        """map.json 自身的 CAS 守卫不得因本修复放宽。"""
        self._map_holder()
        self._template_holder()
        out = apply_to_batch(self.dir, {1: {"new": "X", "expected_current": "别的东西"}})
        self.assertTrue(out["errors"])

    def test_filled_translation_still_synced(self):
        """有真实译文的 translation.json 仍按原逻辑同步。"""
        self._map_holder()
        self._write("translation.json", {
            "translations": [{"xml_index": 1, "translation": "旧译文", "status": "TRANSLATED"}]})
        out = apply_to_batch(self.dir, self.fixes)
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["has_result"])
        self.assertEqual(out["result_applied"], 1)
        unit = json.loads(
            (self.dir / "translation.json").read_text(encoding="utf-8"))["translations"][0]
        self.assertEqual(unit["translation"], "新译文")
        self.assertEqual(unit["status"], "TRANSLATED")

    def test_extra_fields_still_reach_filled_translation(self):
        """waived_tokens 等附加字段在有译文的侧仍要同步过去。"""
        self._map_holder()
        self._write("translation.json", {
            "translations": [{"xml_index": 1, "translation": "旧译文", "status": "TRANSLATED"}]})
        out = apply_to_batch(self.dir, {1: {"waived_tokens": ["[Show Ring]"]}})
        self.assertEqual(out["errors"], [])
        unit = json.loads(
            (self.dir / "translation.json").read_text(encoding="utf-8"))["translations"][0]
        self.assertEqual(unit.get("waived_tokens"), ["[Show Ring]"])

    def test_dry_run_validates_without_writing(self):
        self._map_holder()
        self._template_holder()
        out = apply_to_batch(self.dir, self.fixes, dry_run=True)
        self.assertEqual(out["errors"], [])
        self.assertEqual(
            json.loads((self.dir / "map.json").read_text(encoding="utf-8"))["1"]["translation"],
            "旧译文")


if __name__ == "__main__":
    unittest.main()
