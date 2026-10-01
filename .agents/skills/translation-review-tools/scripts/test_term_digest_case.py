#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""term_digest 的大小写警示标注。

事故锚定（2026-10-02 INFO-370）：官方词典的 `The Companions=战友团`、
`Familiar=使魔[NPC_:FULL]`、`Confidence=信心[NPC_:FULL]` 都是大写专名，
但在源文里以小写普通名词出现（`the companions whose behavior…`=同行者、
`familiar shapes`=熟悉的形状、`confidence`=对自身感官的笃信）。digest 早先
只打词条不标大小写，译者看到「官方词条」自然以为必须译成派系名/召唤法术，
只能各自人工识破——本批译者主动上报了其中两条，第三条是本次修复顺带查出的。

本组测试锁住：词条首字母大写 + 本行源文出现全小写形态 → 加警示；否则不加。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_term_digest_case.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "term_digest.py"
sys.path.insert(0, str(HERE))

import term_digest as td  # noqa: E402

WARN = "⚠源文小写"


class CaseNoteTest(unittest.TestCase):
    def test_proper_noun_matched_lowercase_warns(self):
        note = td._case_mismatch_note(
            "The Companions",
            "the companions whose behavior remains consistent")
        self.assertIn(WARN, note)

    def test_proper_noun_matched_capitalized_no_warn(self):
        note = td._case_mismatch_note(
            "The Companions", "Then the Companions gave his hatred an army.")
        self.assertEqual(note, "")

    def test_leading_article_is_stripped_before_matching(self):
        # 词条带冠词，源文里的普通名词通常也带冠词；只比词干
        note = td._case_mismatch_note("The Eye", "Maps are merciful to the eye.")
        self.assertIn(WARN, note)

    def test_lowercase_term_never_warns(self):
        # 词条本身小写 = 本就是普通名词，不该被标
        self.assertEqual(td._case_mismatch_note("mechanism", "the mechanism holds"), "")

    def test_absent_term_no_warn(self):
        self.assertEqual(td._case_mismatch_note("Familiar", "nothing here at all"), "")

    def test_word_boundary_respected(self):
        # `Familiar` 不该被 `Familiarity` 之类误触
        self.assertEqual(
            td._case_mismatch_note("Familiar", "a familiarish shape"), "")

    def test_empty_inputs_safe(self):
        self.assertEqual(td._case_mismatch_note("", "x"), "")
        self.assertEqual(td._case_mismatch_note("Familiar", ""), "")


class FmtTest(unittest.TestCase):
    def test_mod_hit_carries_note(self):
        hits = [{"english": "The Companions", "chinese": "战友团",
                 "status": "CONFIRMED"}]
        out = td._fmt_mod(hits, "the companions whose behavior remains consistent")
        self.assertIn(WARN, out)
        self.assertIn("The Companions→战友团", out)
        self.assertIn("CONFIRMED", out)

    def test_off_hit_carries_note(self):
        hits = [{"source": "Familiar", "dest": "使魔", "rec": "NPC_:FULL"}]
        out = td._fmt_off(hits, "Dreams borrow familiar shapes")
        self.assertIn(WARN, out)
        self.assertIn("Familiar=使魔[NPC_:FULL]", out)

    def test_clean_hit_has_no_note(self):
        hits = [{"english": "Arkved", "chinese": "阿克维德", "status": "CONFIRMED"}]
        out = td._fmt_mod(hits, "Arkved will make another wound obvious.")
        self.assertNotIn(WARN, out)

    def test_empty_hits_still_dash(self):
        self.assertEqual(td._fmt_mod([], "anything"), "-")
        self.assertEqual(td._fmt_off([], "anything"), "-")

    def test_non_dict_entries_skipped(self):
        self.assertEqual(td._fmt_mod(["junk"], "x"), "-")
        self.assertEqual(td._fmt_off([None], "x"), "-")

    def test_default_source_arg_keeps_backcompat(self):
        hits = [{"english": "The Eye", "chinese": "眼线", "status": "CONFIRMED"}]
        out = td._fmt_mod(hits)          # 不传 source：旧调用方式不炸
        self.assertIn("The Eye→眼线", out)
        self.assertNotIn(WARN, out)


class CliTest(unittest.TestCase):
    def _context(self, td_: Path):
        ctx = {
            "schema_version": 1,
            "batches": [{
                "batch_index": 0,
                "entry_count": 1,
                "entries": [{
                    "translation_unit_id": "u1",
                    "xml": {
                        "index": 28275, "list_id": "2", "edid": "[0401C916]",
                        "rec": "INFO:NAM1",
                        "source": "Dreams borrow familiar shapes because "
                                  "minds need somewhere to stand.",
                        "dest": "", "duplicate_count": 1,
                    },
                    "terminology": {
                        "mod_terms_hits": [],
                        "official_dictionary_hits": [
                            {"source": "Familiar", "dest": "使魔",
                             "rec": "NPC_:FULL"}],
                    },
                }],
            }],
        }
        p = td_ / "ctx.json"
        p.write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
        return p

    def test_digest_output_contains_warning(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._context(Path(d))
            out = Path(d) / "digest.md"
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--context", str(p),
                 "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(WARN, out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
