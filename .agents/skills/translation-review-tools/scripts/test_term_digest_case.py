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


class JudgeNoteTest(unittest.TestCase):
    """判据型 note 必须到达译者眼前（2026-10-02 整轮术语漂移的根因修复）。

    事故：`machinery→机械` 被无条件套用，盖掉 note 里写明的「实物装置＝机械／
    抽象体系＝机制」；`vessel`、`frame`、`change` 各漂 1~2 处。根因是
    `_fmt_mod()` 只打 `en→zh (STATUS)`，把 note 整条丢掉——译者根本看不到
    分域判据。同时全库 1,471 词条都有 note，全量带出会把 digest 撑成
    context 的复刻，所以只挑带语义判据的。
    """

    JUDGE = "〔判据:"

    def tearDown(self):
        td._SHOW_JUDGE = True

    def test_sense_split_note_is_surfaced(self):
        hits = [{"english": "vessel", "chinese": "容器", "status": "CONFIRMED",
                 "note": "三义分域（2026-09-30 定）：①实体名＝容器；②小写 the vessel "
                         "在飞艇语境＝舰船／船"}]
        out = td._fmt_mod(hits, "the vessel holds them")
        self.assertIn(self.JUDGE, out)
        self.assertIn("分域", out)
        # 主形仍必须在，判据是附加信息不是替换
        self.assertIn("vessel→容器", out)

    def test_evidence_only_note_is_not_surfaced(self):
        # 只记录取证过程、与「这一行怎么判」无关的 note 不带出，保持 digest 紧凑
        hits = [{"english": "Arkved", "chinese": "阿克维德", "status": "CONFIRMED",
                 "note": "官方词典实证，2026-09-12 清点 12 处，此前无词条。"}]
        out = td._fmt_mod(hits, "Arkved will make another wound obvious.")
        self.assertNotIn(self.JUDGE, out)

    def test_note_is_truncated(self):
        hits = [{"english": "x", "chinese": "y", "status": "CONFIRMED",
                 "note": "分域" + "很长的判据内容" * 200}]
        out = td._fmt_mod(hits, "x")
        self.assertIn("…", out)
        self.assertLess(len(out), 400)

    # 2026-10-02 事故：communion 的 note 里「③34842 未译…按②取共融」是对该行的
    # 逐行预裁，却落在 180 字截断线之后，译者看不到就自行取了「共食」并标 REVIEW。
    # 修法是「note 点名了本行 xml_index 就不截断」，不是调大上限。
    _CITED_NOTE = (
        "两义分域（锚级普查 4 行后定形）：①本作那条食人仪式，底座实证 共食い => 自噬："
        "共食いの儀礼 => 自噬仪式、共食いの丘 => 自噬之丘，＝自噬，全库 1 行"
        "（11969 The communion choice is near enough to touch，INFO-052 定形的那处）。"
        "②共享/交融义，与 possession/unity 对立而用，保留他者之真实＝共融，全库 2 行"
        "（13985 Its unity is possession, not communion、"
        "30795 Communion preserves the reality of the other）。"
        "③34842 未译（The communion with Ja'bal is where the question becomes personal），"
        "按②取「共融」——与神明的交融不是自我吞食。门禁警示：本条是契约 REQUIRED，"
        "只登记「自噬」一个 target 时，30795 写回当场报 TERM001: required target 未出现，"
        "而「共融」正确。修法是补 additional_accepted 登记②的中形，不是改译文。"
    )

    def test_row_cited_note_is_not_truncated(self):
        hits = [{"english": "communion", "chinese": "自噬", "status": "PROVISIONAL",
                 "note": self._CITED_NOTE}]
        out = td._fmt_mod(hits, "The communion with Ja'bal…", 34842)
        self.assertIn("③34842", out)
        self.assertIn("共融", out)
        self.assertNotIn("…", out)

    def test_uncited_row_still_truncated(self):
        hits = [{"english": "communion", "chinese": "自噬", "status": "PROVISIONAL",
                 "note": self._CITED_NOTE}]
        out = td._fmt_mod(hits, "The communion with Ja'bal…", 40000)
        self.assertIn("…", out)
        self.assertNotIn("③34842", out)

    def test_numeric_substring_is_not_a_citation(self):
        """数字子串不得被误判成点名本行（348421 / 134842 / 3484 都不是 34842）。"""
        hits = [{"english": "communion", "chinese": "自噬", "status": "PROVISIONAL",
                 "note": self._CITED_NOTE}]
        for bogus in (348421, 134842, 3484, 48420):
            out = td._fmt_mod(hits, "x", bogus)
            self.assertNotIn("③34842", out, "idx=%s 被误判为点名本行" % bogus)

    def test_whitespace_flattened(self):
        hits = [{"english": "x", "chinese": "y", "status": "CONFIRMED",
                 "note": "分域\n判据   带\n换行"}]
        self.assertNotIn("\n", td._fmt_mod(hits, "x"))

    def test_missing_note_is_safe(self):
        hits = [{"english": "Arkved", "chinese": "阿克维德", "status": "CONFIRMED"}]
        self.assertNotIn(self.JUDGE, td._fmt_mod(hits, "Arkved"))

    def test_flag_suppresses_judge_notes_and_legend(self):
        td._SHOW_JUDGE = False
        hits = [{"english": "vessel", "chinese": "容器", "status": "CONFIRMED",
                 "note": "三义分域"}]
        self.assertNotIn(self.JUDGE, td._fmt_mod(hits, "the vessel"))
        self.assertEqual(td._legend(), [])

    def test_legend_present_by_default(self):
        self.assertTrue(td._legend())
        self.assertIn(self.JUDGE, "\n".join(td._legend()))


class CliTest(unittest.TestCase):
    def _context(self, td_: Path, mod_hits=None):
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
                        "mod_terms_hits": mod_hits or [],
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

    def _run(self, ctx: Path, out: Path, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--context", str(ctx),
             "--out", str(out), *extra],
            capture_output=True, text=True, encoding="utf-8")

    def test_digest_output_contains_warning(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._context(Path(d))
            out = Path(d) / "digest.md"
            r = self._run(p, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(WARN, out.read_text(encoding="utf-8"))

    def test_cli_judge_note_on_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._context(Path(d), mod_hits=[
                {"english": "vessel", "chinese": "容器", "status": "CONFIRMED",
                 "note": "三义分域：小写 the vessel 在飞艇语境＝船"}])
            out = Path(d) / "digest.md"
            r = self._run(p, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("〔判据:", text)
            self.assertIn("== 读法 ==", text)

    def test_cli_no_judge_notes_flag(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._context(Path(d), mod_hits=[
                {"english": "vessel", "chinese": "容器", "status": "CONFIRMED",
                 "note": "三义分域：小写 the vessel 在飞艇语境＝船"}])
            out = Path(d) / "digest.md"
            r = self._run(p, out, "--no-judge-notes")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertNotIn("〔判据:", text)
            self.assertNotIn("== 读法 ==", text)
            # 关掉判据不影响主形本身
            self.assertIn("vessel→容器", text)


if __name__ == "__main__":
    unittest.main()
