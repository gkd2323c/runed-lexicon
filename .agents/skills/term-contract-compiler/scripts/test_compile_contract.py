"""Tests for compile_contract term_id handling.

The regression these guard: `_slug()` folds every run of non-alphanumerics
into a single '-', so two genuinely different English spellings can normalize
to one term_id.  Assigning into the dict then silently drops one of them, and
the term-list author has no way to tell -- which is how a spelling variant
gets "registered" while contributing nothing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compile_contract as cc  # noqa: E402


def term(english: str, zh: str, note: str = "", **extra) -> dict:
    return {
        "english": english,
        "zh": zh,
        "status": "PROVISIONAL",
        "enforcement": "REQUIRED",
        "note": note,
        **extra,
    }


class SlugTests(unittest.TestCase):
    def test_punctuation_folds_to_single_dash(self) -> None:
        # Runs of non-alphanumerics collapse, so the apostrophe-plus-space in
        # "Ra' Hna" is one separator, not two.
        self.assertEqual(cc._slug("Ra' Hna"), "ra-hna")
        self.assertEqual(cc._slug("Ra Hna"), "ra-hna")
        self.assertEqual(cc._slug("Ra'basha"), "ra-basha")

    def test_slug_is_length_capped(self) -> None:
        self.assertEqual(len(cc._slug("A" * 80)), 48)


class BuildTermsJsonTests(unittest.TestCase):
    def build(self, terms, prefix="ssi."):
        return cc.build_terms_json(terms, conf={}, id_prefix=prefix)

    def test_distinct_terms_get_distinct_ids(self) -> None:
        out, _ = self.build(
            [term("Dusk", "黄昏城"), term("Anu", "阿努"), term("Eton Nir", "伊顿尼尔")]
        )
        self.assertEqual(len(out), 3)
        self.assertEqual(sorted(out), ["ssi.anu", "ssi.dusk", "ssi.eton-nir"])

    def test_spelling_variant_collision_is_rejected(self) -> None:
        """The regression: these two must not silently collapse to one id."""
        with self.assertRaises(SystemExit) as ctx:
            self.build([term("Ra' Hna", "拉'赫纳"), term("Ra Hna", "拉'赫纳")])
        message = str(ctx.exception)
        self.assertIn("term_id", message)
        self.assertIn("Ra' Hna", message)
        self.assertIn("Ra Hna", message)

    def test_collision_message_points_at_both_spellings(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.build([term("J' Dirrja", "杰'迪尔贾"), term("J Dirrja", "杰'迪尔贾")])
        message = str(ctx.exception)
        # Both source spellings must be named, so the author can find them.
        self.assertIn("J' Dirrja", message)
        self.assertIn("J Dirrja", message)
        self.assertIn("ssi.j-dirrja", message)

    def test_hyphen_and_space_forms_collide_too(self) -> None:
        with self.assertRaises(SystemExit):
            self.build([term("Eton Nir", "伊顿尼尔"), term("Eton-Nir", "伊顿尼尔")])

    def test_same_spelling_twice_is_still_a_collision(self) -> None:
        with self.assertRaises(SystemExit):
            self.build([term("Dusk", "黄昏城"), term("Dusk", "黄昏")])

    def test_keep_entries_do_not_collide(self) -> None:
        """KEEP routes to the keep list, so it never reaches the id space."""
        keep_term = term("Beta", "贝塔")
        keep_term["enforcement"] = "KEEP"
        out, keep = self.build([keep_term, term("Beta", "贝塔")])
        self.assertEqual(keep, ["Beta"])
        self.assertEqual(len(out), 1)

    def test_entries_without_zh_are_skipped(self) -> None:
        out, _ = self.build([term("Dusk", ""), term("Anu", "阿努")])
        self.assertEqual(list(out), ["ssi.anu"])

    def test_case_sensitive_flag_survives(self) -> None:
        out, _ = self.build([term("Dominion", "先祖神洲", case_sensitive=True)])
        self.assertTrue(out["ssi.dominion"]["case_sensitive"])


class BuildTermsMarkdownTests(unittest.TestCase):
    """The Markdown path needs the same guard as the JSON path."""

    def test_markdown_path_also_rejects_collisions(self) -> None:
        entries = [
            {"english": "Ra' Hna", "zh": "拉'赫纳", "note": ""},
            {"english": "Ra Hna", "zh": "拉'赫纳", "note": ""},
        ]
        with self.assertRaises(SystemExit) as ctx:
            cc.build_terms(entries, conf_overrides={"id_prefix": "ssi."})
        self.assertIn("term_id", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
