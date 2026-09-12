"""Regression tests for the dictionary-matching tokenizer.

Incident this locks in: the game wraps spell and creature names in single
quotes ("cast 'Flame Atronach'", "conjure a 'Familiar'") and marks possessives
with a trailing apostrophe ("Magnus' notes"). WORD_RE counts an apostrophe as a
word character, so those tokens came out as "'familiar'", "'flame", "atronach'"
and could never equal a dictionary index key. The official-name hint silently
came back empty for every quoted term, the per-batch digest printed
"OFF: -", and the translator invented a form: Familiar became 魔宠 instead of
the official 使魔, Flame Atronach became 火焰元素 instead of 火焰侍灵, and
Soul Trap became 灵魂陷阱 instead of 摄魂陷阱.

Run:  python -m unittest discover -s .agents/skills/translation-context-builder/scripts -p "test_*.py"
"""

import unittest

import build_translation_context as B


class DequoteStrayApostrophesTest(unittest.TestCase):
    def test_quoted_term_loses_its_wrapping_quotes(self):
        # The replacement is a space, so surrounding whitespace is not collapsed
        # here; normalize_phrase is what collapses it (see NormalizePhraseTest).
        self.assertEqual(B.dequote_stray_apostrophes("conjure a 'Familiar'"), "conjure a  Familiar ")

    def test_apostrophe_inside_a_word_survives(self):
        self.assertEqual(B.dequote_stray_apostrophes("it's"), "it's")
        self.assertEqual(B.dequote_stray_apostrophes("Mara's Blessing"), "Mara's Blessing")

    def test_trailing_possessive_apostrophe_is_dropped(self):
        self.assertEqual(B.dequote_stray_apostrophes("Magnus' notes"), "Magnus  notes")

    def test_typographic_apostrophe_is_normalized_too(self):
        # \u2019 = right single quotation mark, used as both a quote and an apostrophe.
        self.assertEqual(B.dequote_stray_apostrophes("\u2019Familiar\u2019"), " Familiar ")
        self.assertEqual(B.dequote_stray_apostrophes("it\u2019s"), "it's")

    def test_plain_text_is_untouched(self):
        self.assertEqual(B.dequote_stray_apostrophes("no quotes here"), "no quotes here")


class NormalizePhraseTest(unittest.TestCase):
    def test_quoted_term_normalizes_to_the_bare_index_key(self):
        self.assertEqual(B.normalize_phrase("conjure a 'Familiar'"), "conjure a familiar")
        self.assertEqual(B.normalize_phrase("'Familiar'"), "familiar")
        self.assertEqual(B.normalize_phrase("a 'Flame Atronach',"), "a flame atronach")
        self.assertEqual(B.normalize_phrase("'Soul trap'-spell"), "soul trap spell")

    def test_possessive_normalizes_to_the_bare_noun(self):
        self.assertEqual(B.normalize_phrase("Magnus' notes"), "magnus notes")

    def test_interior_apostrophe_shape_matches_index_side(self):
        # Index side and query side must produce the same key for the same text.
        self.assertEqual(B.normalize_phrase("Mara's Blessing"), "mara's blessing")


class SourceNgramsTest(unittest.TestCase):
    def test_quoted_term_yields_a_bare_ngram(self):
        source = "Zeus has given me my first task, which is to conjure a 'Familiar'. I should do this now."
        grams = list(B.source_ngrams(source))
        self.assertIn("familiar", grams)
        self.assertNotIn("'familiar'", grams)

    def test_multiword_quoted_term_yields_a_bare_bigram(self):
        source = "Zeus asked me to conjure a 'Flame Atronach', in order to prove that I am worthy."
        grams = list(B.source_ngrams(source))
        self.assertIn("flame atronach", grams)

    def test_quoted_term_joined_by_hyphen_yields_a_bare_bigram(self):
        source = "I should hit it with my 'Soul trap'-spell."
        grams = list(B.source_ngrams(source))
        self.assertIn("soul trap", grams)

    def test_max_words_still_bounds_the_ngrams(self):
        grams = list(B.source_ngrams("a b c d e f g", max_words=3))
        self.assertIn("a b c", grams)
        self.assertNotIn("a b c d", grams)


class OfficialDictionaryLookupTest(unittest.TestCase):
    """End-to-end: quoted terms must resolve to their official Chinese form."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        dictionary_dir = B.PROJECT_ROOT / "dictionary"
        # The directory itself is tracked (README/EXPORT_GUIDE) while the XML
        # exports are gitignored local evidence, so is_dir() alone is not a
        # usable guard: a fresh clone has the folder but no corpus. Skip on
        # missing data; malformed data still fails loudly.
        if not dictionary_dir.is_dir() or not any(dictionary_dir.rglob("*.xml")):
            raise unittest.SkipTest(f"official dictionary XML not present: {dictionary_dir}")
        cls.index, _ = B.build_official_dictionary_index(Path(dictionary_dir))

    def _hits(self, source):
        return {(h["source"], h["dest"]) for h in B.hits_for_source(source, self.index, 24, target_rec="QUST:CNAM")}

    def test_quoted_familiar_resolves_to_official_form(self):
        hits = self._hits("Zeus has given me my first task, which is to conjure a 'Familiar'.")
        self.assertIn(("Familiar", "使魔"), hits)

    def test_quoted_flame_atronach_resolves_to_official_form(self):
        hits = self._hits("Zeus asked me to conjure a 'Flame Atronach', in order to prove that I am worthy.")
        self.assertIn(("Flame Atronach", "火焰侍灵"), hits)

    def test_quoted_soul_trap_resolves_to_official_form(self):
        hits = self._hits("I should hit it with my 'Soul trap'-spell, to ensure that it's soul is not lost.")
        self.assertIn(("Soul Trap", "摄魂陷阱"), hits)


if __name__ == "__main__":
    unittest.main()
