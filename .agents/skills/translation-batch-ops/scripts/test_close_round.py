import tempfile
import unittest
from pathlib import Path

from close_round import same_source_split


class CloseRoundSameSourceSplitTests(unittest.TestCase):
    def test_ignores_untranslated_duplicates_but_reports_translated_forms(self):
        xml = """<root>
  <String><Source>It does not.</Source><Dest>它不会。</Dest></String>
  <String><Source>It does not.</Source><Dest>它不会。</Dest></String>
  <String><Source>It does not.</Source><Dest>It does not.</Dest></String>
</root>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translated.xml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(same_source_split(path), [])

        drifted = """<root>
  <String><Source>It does not.</Source><Dest>它不会。</Dest></String>
  <String><Source>It does not.</Source><Dest>它不能。</Dest></String>
  <String><Source>It does not.</Source><Dest>It does not.</Dest></String>
</root>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translated.xml"
            path.write_text(drifted, encoding="utf-8")
            splits = same_source_split(path)
            self.assertEqual(len(splits), 1)
            self.assertEqual(splits[0][0], "It does not.")
            self.assertEqual(set(splits[0][1]), {"它不会。", "它不能。"})


if __name__ == "__main__":
    unittest.main()
