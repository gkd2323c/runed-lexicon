#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_batch.py 的 term-digest 新鲜度检查回归测试。

事故锚定（2026-10-02）：全库普查发现 585 批 term-digest.md 缺 `== 读法 ==`
图例（本 MOD 551 / VIGILANT 34），全部是判据型 note 机制上线前生成的。判据型
note 正是为了根治整轮术语漂移（machinery / virtue / cause / curse / vessel）而
加的——译者看不到〔判据:…〕就等于把分域判据藏回词表。手工「派审查前先重生成」
靠人记，所以把检查塞进 read_batch。

本测试覆盖：图例判定、重生成、context 缺失、构建失败、--no-digest-check 旁路。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import read_batch  # noqa: E402
import term_digest  # noqa: E402


LEGEND = "== 读法 ==\n  MOD 的 en→zh 只是该词条的**主形记录**。\n  〔判据:…〕= 判据。\n\n"


def make_context(path: Path, idx: int = 100) -> None:
    """写一个最小可被 digest() 消费的 context.json。"""
    payload = {
        "schema_version": 1,
        "batches": [
            {
                "batch_index": 0,
                "entry_count": 1,
                "entries": [
                    {
                        "translation_unit_id": "u1",
                        "xml": {
                            "index": idx,
                            "list_id": "INFO:TEST",
                            "edid": "[0401TEST]",
                            "rec": "INFO:TEST",
                            "source": "The communion preserves the other.",
                            "dest": "共融保存着他者。",
                            "duplicate_count": 1,
                        },
                        "dialogue_context": {"status": "unmatched"},
                        "terminology": {
                            "mod_terms_hits": [
                                {
                                    "english": "communion",
                                    "chinese": "自噬",
                                    "status": "PROVISIONAL",
                                    "note": "两义分域：仪式义与共享义",
                                }
                            ],
                            "official_dictionary_hits": [],
                        },
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class DigestFreshnessTextTests(unittest.TestCase):
    """图例判定的纯文本层面。"""

    def test_legend_present_is_fresh(self):
        self.assertTrue(read_batch.digest_is_fresh_text(LEGEND))

    def test_legacy_digest_without_legend_is_stale(self):
        legacy = "== batch 0 (1 entries) ==\n[100] INFO:TEST | The communion...\n    MOD: -\n"
        self.assertFalse(read_batch.digest_is_fresh_text(legacy))

    def test_legend_without_judge_probe_is_stale(self):
        """只有标题没有判据说明 = 半旧版，判为不新鲜（宁可多重生成一次）。"""
        partial = "== 读法 ==\n  MOD 的 en→zh 只是主形记录。\n"
        self.assertFalse(read_batch.digest_is_fresh_text(partial))

    def test_empty_text_is_stale(self):
        self.assertFalse(read_batch.digest_is_fresh_text(""))

    def test_file_variant_matches_text_variant(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "term-digest.md"
            p.write_text(LEGEND, encoding="utf-8")
            self.assertTrue(read_batch.digest_is_fresh(p))
            p.write_text("== batch 0 ==\n", encoding="utf-8")
            self.assertFalse(read_batch.digest_is_fresh(p))

    def test_missing_file_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(read_batch.digest_is_fresh(Path(td) / "nope.md"))


class EnsureFreshDigestTests(unittest.TestCase):
    """重生成路径。"""

    def test_fresh_digest_left_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            (bd / "context.json").write_text("{}", encoding="utf-8")
            digest = bd / read_batch.DIGEST_NAME
            original = LEGEND + "MARKER\n"
            digest.write_text(original, encoding="utf-8")
            state, _ = read_batch.ensure_fresh_digest(bd)
            self.assertEqual(state, "fresh")
            self.assertEqual(digest.read_text(encoding="utf-8"), original)

    def test_legacy_digest_is_regenerated_with_judge_notes(self):
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            make_context(bd / "context.json")
            (bd / read_batch.DIGEST_NAME).write_text("== batch 0 ==\n[100] ...\n", encoding="utf-8")
            state, detail = read_batch.ensure_fresh_digest(bd)
            self.assertEqual(state, "regenerated", detail)
            text = (bd / read_batch.DIGEST_NAME).read_text(encoding="utf-8")
            self.assertTrue(read_batch.digest_is_fresh_text(text))
            # 关键：判据必须真的回到材料里，否则这个检查只是装饰
            self.assertIn("判据", text)
            self.assertIn("communion", text)
            self.assertIn("两义分域", text)

    def test_missing_digest_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            make_context(bd / "context.json")
            state, _ = read_batch.ensure_fresh_digest(bd)
            self.assertEqual(state, "regenerated")
            self.assertTrue((bd / read_batch.DIGEST_NAME).is_file())

    def test_no_context_and_no_digest_reports_no_context(self):
        with tempfile.TemporaryDirectory() as td:
            state, detail = read_batch.ensure_fresh_digest(Path(td))
            self.assertEqual(state, "no-context")
            self.assertTrue(detail)

    def test_legacy_digest_without_context_does_not_overwrite(self):
        """无 context 就只能警告，不能把旧 digest 覆盖成空的——旧料比没料强。"""
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            original = "== batch 0 ==\n[100] 老料\n"
            (bd / read_batch.DIGEST_NAME).write_text(original, encoding="utf-8")
            state, detail = read_batch.ensure_fresh_digest(bd)
            self.assertEqual(state, "no-context")
            self.assertIn("缺图例", detail)
            self.assertEqual(
                (bd / read_batch.DIGEST_NAME).read_text(encoding="utf-8"), original
            )

    def test_broken_context_reports_failed_without_crashing(self):
        with tempfile.TemporaryDirectory() as td:
            bd = Path(td)
            (bd / "context.json").write_text("{ this is not json", encoding="utf-8")
            state, detail = read_batch.ensure_fresh_digest(bd)
            self.assertEqual(state, "failed")
            self.assertTrue(detail)
            self.assertFalse((bd / read_batch.DIGEST_NAME).exists())


class BuildDigestTests(unittest.TestCase):
    """term_digest 抽出的共用构建路径。"""

    def test_build_digest_matches_digest_function(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / "context.json"
            make_context(cp)
            payload = json.loads(cp.read_text(encoding="utf-8"))
            self.assertEqual(
                term_digest.build_digest(cp),
                term_digest.digest(payload, {}),
            )

    def test_build_digest_embeds_legend_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / "context.json"
            make_context(cp)
            self.assertTrue(read_batch.digest_is_fresh_text(term_digest.build_digest(cp)))

    def test_discover_registry_prefers_explicit_path(self):
        with tempfile.TemporaryDirectory() as td:
            explicit = str(Path(td) / "x-registry.md")
            Path(explicit).write_text("| a | b |\n", encoding="utf-8")
            self.assertEqual(term_digest.discover_spell_registry(Path(td) / "c.json", explicit), explicit)

    def test_discover_registry_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            # .work/<stem>/notes 下无 registry 时应返回 None 而非抛错
            self.assertIsNone(term_digest.discover_spell_registry(Path(td) / "c.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
