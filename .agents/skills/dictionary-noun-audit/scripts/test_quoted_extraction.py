"""Regression tests for entity extraction against quoted source text.

Incident this locks in: the game wraps spell and creature names in single
quotes ("cast 'Flame Atronach'") and marks possessives with a trailing
apostrophe. The tokenizer regex starts at a letter, so the closing quote stays
glued to the token: "'Flame Atronach'" arrived as ["Flame", "Atronach'"] and
"'Soul trap'-spell" as ["Soul", "trap'", "spell"]. Only the LAST token had its
possessive stripped, so "atronach'" and "trap'" never equalled the index keys
"flame atronach" / "soul trap" and the audit reported no candidate. That is how
Familiar/使魔, Flame Atronach/火焰侍灵 and Soul Trap/摄魂陷阱 passed earlier
audits while the translate step rendered 魔宠 / 火焰元素 / 灵魂陷阱.

Run:  python -m unittest discover -s .agents/skills/dictionary-noun-audit/scripts -p "test_*.py"
"""

import unittest

import dictionary_noun_audit as A


class ExtractCandidatesQuotedTextTest(unittest.TestCase):
    def setUp(self):
        self.enabled = {"Familiar", "Flame Atronach", "Soul Trap", "Whiterun", "M'aiq"}
        self.enabled_map, self.optional_map = A.build_lookup_maps(self.enabled, set())

    def _extract(self, source):
        return A.extract_candidates(source, self.enabled_map, self.optional_map, False, None)

    def test_single_word_in_wrapping_quotes_is_found(self):
        found = self._extract("Zeus has given me my first task, which is to conjure a 'Familiar'.")
        self.assertIn("Familiar", found)

    def test_multiword_name_in_wrapping_quotes_is_found(self):
        found = self._extract("Zeus asked me to conjure a 'Flame Atronach', in order to prove that I am worthy.")
        self.assertIn("Flame Atronach", found)

    def test_name_quoted_and_hyphen_glued_is_found(self):
        found = self._extract("I should hit it with my 'Soul trap'-spell, to ensure that its soul is not lost.")
        self.assertIn("Soul Trap", found)

    def test_possessive_form_still_resolves(self):
        self.assertIn("Whiterun", self._extract("Whiterun's guards are on alert."))

    def test_interior_apostrophe_name_survives(self):
        self.assertIn("M'aiq", self._extract("M'aiq the Liar was here."))

    def test_unquoted_sentence_still_resolves(self):
        self.assertIn("Familiar", self._extract("You may conjure a Familiar and a Flame Atronach."))

    def test_unrelated_word_is_not_reported(self):
        self.assertEqual(self._extract("The guards of Whiterun keep watch."), ["Whiterun"])


if __name__ == "__main__":
    unittest.main()
