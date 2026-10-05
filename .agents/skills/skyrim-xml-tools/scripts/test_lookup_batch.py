"""Tests for lookup_batch.py (read-only official-dictionary probe).

The suite builds a throwaway dictionary directory, so it never depends on the
real `dictionary/` corpus and never touches MOD data.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lookup_batch as lb  # noqa: E402


DICT_XML = """<?xml version="1.0" encoding="utf-8"?>
<Translators>
  <Content>
    <String>
      <EDID>DuskRef</EDID>
      <REC>N</REC>
      <Source>Dusk</Source>
      <Dest>黄昏</Dest>
    </String>
    <String>
      <EDID>DuskglowCreviceRef</EDID>
      <REC>L</REC>
      <Source>Duskglow Crevice</Source>
      <Dest>昏光裂缝</Dest>
    </String>
    <String>
      <EDID>LongBodyRef</EDID>
      <REC>B</REC>
      <Source>Ulliallia</Source>
      <Dest>{body}</Dest>
    </String>
    <String>
      <EDID>OtherLongRef</EDID>
      <REC>B</REC>
      <Source>Ulliallia again</Source>
      <Dest>{body}</Dest>
    </String>
  </Content>
</Translators>
"""

LONG_BODY = "这是一个很长的书正文段落。" * 12


class LookupBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        cls.dict_dir = Path(cls._tmp.name) / "dictionary"
        cls.dict_dir.mkdir()
        (cls.dict_dir / "Sample_english_chinese.xml").write_text(
            DICT_XML.replace("{body}", LONG_BODY),
            encoding="utf-8",
        )
        cls.rows = lb.load_all(cls.dict_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_exact_hit_reports_the_official_rendering(self) -> None:
        res = lb.report_term(
            self.rows, "Dusk", contains=False, ignore_case=False,
            max_forms=6, retry_variants=False,
        )
        self.assertEqual(res["exact"], 1)
        self.assertIn("黄昏", res["renderings"])

    def test_contains_does_not_present_substring_hits_as_renderings(self) -> None:
        """Dusk occurs inside Duskglow Crevice; that is context, not a rendering."""
        res = lb.report_term(
            self.rows, "Dusk", contains=True, ignore_case=False,
            max_forms=6, retry_variants=False,
        )
        self.assertEqual(res["mode"], "contains")
        # The longer name is present, but the field is called "context".
        self.assertIn("昏光裂缝", res["context"])
        self.assertNotIn("renderings", res)

    def test_long_body_mentions_are_tallied_not_rendered(self) -> None:
        """Book-body prose mentioning a term is not evidence of its rendering."""
        res = lb.report_term(
            self.rows, "Ulliallia", contains=False, ignore_case=False,
            max_forms=6, retry_variants=False,
        )
        self.assertEqual(res["exact"], 0)
        self.assertEqual(res["long_mentions"], 1)
        self.assertEqual(res["renderings"], "")

    def test_missing_term_retries_spellings(self) -> None:
        """A zero must be re-spelled before it is reported as zero."""
        res = lb.report_term(
            self.rows, "dusk", contains=False, ignore_case=False,
            max_forms=6, retry_variants=True,
        )
        self.assertEqual(res["exact"], 0)
        retried = {v["term"] for v in res["variants"]}
        self.assertIn("Dusk", retried)
        self.assertTrue(any(v["exact"] for v in res["variants"]))

    def test_case_insensitive_exact_finds_the_entry(self) -> None:
        res = lb.report_term(
            self.rows, "dusk", contains=False, ignore_case=True,
            max_forms=6, retry_variants=False,
        )
        self.assertEqual(res["exact"], 1)
        self.assertIn("黄昏", res["renderings"])

    def test_spelling_variants_cover_case_and_separators(self) -> None:
        variants = lb.spelling_variants("Ra J' Dar")
        self.assertIn("Ra J' Dar".replace(" ", ""), variants)
        self.assertIn("ra j' dar", variants)
        # The original is never repeated, and variants are unique.
        self.assertNotIn("Ra J' Dar", variants)
        self.assertEqual(len(variants), len(set(variants)))

    def test_renderings_dedupes_and_orders_by_frequency(self) -> None:
        hits = [{"dest": "黄昏"}, {"dest": "黄昏"}, {"dest": "暮色"}]
        self.assertEqual(lb.renderings(hits, 6), "黄昏×2 | 暮色")

    def test_renderings_respects_max_forms(self) -> None:
        hits = [{"dest": f"译形{i}"} for i in range(10)]
        out = lb.renderings(hits, 3)
        self.assertIn("（另 7 种）", out)

    def test_long_mention_detection(self) -> None:
        self.assertTrue(lb.is_long_mention(LONG_BODY))
        self.assertFalse(lb.is_long_mention("黄昏"))
        self.assertFalse(lb.is_long_mention("  暮色  "))

    def test_missing_dictionary_dir_raises(self) -> None:
        with self.assertRaises(ValueError):
            lb.load_all(Path(self._tmp.name) / "does-not-exist")


class ReadTermsTests(unittest.TestCase):
    """Terms come from the command line; a file is optional."""

    def _args(self, words, terms=None):
        import argparse

        return argparse.Namespace(words=words, terms=terms)

    def test_positional_words_need_no_file(self) -> None:
        self.assertEqual(
            lb.read_terms(self._args(["Dusk", "Anu"])),
            ["Dusk", "Anu"],
        )

    def test_file_terms_are_merged_after_positional(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "terms.txt"
            path.write_text("# comment\nAldmeris\n\nRa'basha\n", encoding="utf-8")
            got = lb.read_terms(self._args(["Dusk"], str(path)))
        self.assertEqual(got, ["Dusk", "Aldmeris", "Ra'basha"])

    def test_duplicates_collapse(self) -> None:
        self.assertEqual(
            lb.read_terms(self._args(["Dusk", "dusk", "Dusk"])),
            ["Dusk", "dusk"],
        )

    def test_no_terms_yields_empty(self) -> None:
        self.assertEqual(lb.read_terms(self._args([])), [])


class CliTests(unittest.TestCase):
    """End-to-end runs over the throwaway dictionary.

    These exist because the text renderer has more branches than the
    per-term helpers, and a branch that is never exercised is a branch that
    ships broken.
    """

    def _run(self, argv):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = lb.main(argv)
        return rc, buf.getvalue()

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.dict_dir = Path(self._tmp.name) / "dictionary"
        self.dict_dir.mkdir()
        (self.dict_dir / "Sample_english_chinese.xml").write_text(
            DICT_XML.replace("{body}", LONG_BODY), encoding="utf-8"
        )
        self.terms_file = Path(self._tmp.name) / "terms.txt"
        self.terms_file.write_text("Dusk\nUlliallia\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_run_renders(self) -> None:
        rc, out = self._run(
            ["--terms", str(self.terms_file), "--dictionary-dir", str(self.dict_dir)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("[Dusk] 命中 1", out)
        self.assertIn("黄昏", out)
        self.assertIn("零命中 1 / 2", out)

    def test_contains_run_renders(self) -> None:
        rc, out = self._run(
            [
                "--terms", str(self.terms_file), "--dictionary-dir", str(self.dict_dir),
                "--contains",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertIn("contains 语境命中", out)
        self.assertIn("要断言官方译形请改用 exact", out)
        # Substring context is labelled, not presented as a rendering.
        self.assertIn("语境:", out)

    def test_ignore_case_run_renders(self) -> None:
        self.terms_file.write_text("dusk\n", encoding="utf-8")
        rc, out = self._run(
            [
                "--terms", str(self.terms_file), "--dictionary-dir", str(self.dict_dir),
                "--ignore-case",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertIn("ignore-case", out)
        self.assertIn("[dusk] 命中 1", out)

    def test_out_file_is_written(self) -> None:
        out_file = Path(self._tmp.name) / "report.txt"
        rc, _ = self._run(
            [
                "--terms", str(self.terms_file), "--dictionary-dir", str(self.dict_dir),
                "--out", str(out_file),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertIn("[Dusk] 命中 1", out_file.read_text(encoding="utf-8"))

    def test_json_run_renders(self) -> None:
        import json as _json

        rc, out = self._run(
            [
                "--terms", str(self.terms_file), "--dictionary-dir", str(self.dict_dir),
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        payload = _json.loads(out)
        self.assertEqual(len(payload["terms"]), 2)
        self.assertTrue(payload["dictionary_entries"] > 0)

    def test_no_terms_is_rejected(self) -> None:
        rc, _ = self._run(["--dictionary-dir", str(self.dict_dir)])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
