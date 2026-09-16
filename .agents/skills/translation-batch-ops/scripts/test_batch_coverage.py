"""Regression tests for the coverage classifier's unwritten-row detection.

Behavior locked in: `VERIFIED` must mean only "translation.json is filled", not
"written back". A batch can have filled translations and PASS verify reports
while never reaching the canonical XML; under a state-only definition it would
count as finished forever. classify() therefore cross-checks the canonical when
one is supplied and reports such rows as `unwritten`.

Run:  python -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_*.py"
"""

import json
import os
import tempfile
import unittest

import check_batch_coverage as C


def _batch(bid, idx):
    return {"id": bid, "idx": idx, "line": "QUST"}


class UnwrittenDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.bid = "NI-QUST-900"
        bdir = os.path.join(self.dir, self.bid)
        os.makedirs(bdir)
        with open(os.path.join(bdir, "translation.json"), "w", encoding="utf-8") as f:
            json.dump({
                "translations": [
                    {"xml_index": 10, "translation": "已译甲", "status": "TRANSLATED"},
                    {"xml_index": 11, "translation": "已译乙", "status": "TRANSLATED"},
                    {"xml_index": 12, "translation": "", "status": "KEEP"},
                ]
            }, f, ensure_ascii=False)

    def test_rows_missing_from_canonical_are_counted(self):
        # 10 landed in canonical, 11 did not; 12 is KEEP and must not be counted.
        canonical = {10: ("Src A", "译文甲"), 11: ("Src B", "Src B"), 12: ("Src C", "Src C")}
        r = C.classify(_batch(self.bid, [10, 11, 12]), self.dir, canonical)
        self.assertEqual(r["state"], "VERIFIED")
        self.assertEqual(r["unwritten"], 1)
        self.assertEqual(r["unwritten_idx"], [11])

    def test_no_canonical_means_unwritten_is_unknown(self):
        r = C.classify(_batch(self.bid, [10, 11]), self.dir)
        self.assertIsNone(r["unwritten"])

    def test_fully_written_batch_reports_zero(self):
        canonical = {10: ("Src A", "译文甲"), 11: ("Src B", "译文乙")}
        r = C.classify(_batch(self.bid, [10, 11]), self.dir, canonical)
        self.assertEqual(r["unwritten"], 0)

    def test_keep_rows_are_not_flagged_as_unwritten(self):
        canonical = {10: ("Src A", "译文甲"), 11: ("Src B", "译文乙"), 12: ("Src C", "Src C")}
        r = C.classify(_batch(self.bid, [10, 11, 12]), self.dir, canonical)
        self.assertEqual(r["unwritten"], 0)


if __name__ == "__main__":
    unittest.main()
