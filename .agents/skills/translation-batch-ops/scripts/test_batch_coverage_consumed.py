"""Regression tests for consumption-trace detection in the coverage classifier.

Bug being locked down: a pure-KEEP batch (Dest change is 0, `Dest == Source`
holds by definition) leaves no trace in the canonical XML, so the old
`VERIFIED` test — "at least one TRANSLATED row in translation.json" — could
never pass for it. The state machine stayed on TRANSLATED forever and
progress_snapshot reported the same fake stall every round (anchored on
TheKalpicAnomaly_GLENMORIL / NI-TES4-001: consume done, round_pipeline
`PIPELINE PASS`, `0 Dest change(s)`, yet "滞留 1 批 (253min)" -> "270min").

classify() now also accepts consume's own on-disk trace. The dangerous half of
this change is false negatives going the other way: a batch that really was
never consumed must keep being reported. That is what most of these tests
guard.

Run:  python -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_*.py"
"""

import json
import os
import tempfile
import unittest

import check_batch_coverage as C
import progress_snapshot as P


def _batch(bid, idx):
    return {"id": bid, "idx": idx, "line": "TES4"}


def _write(bdir, name, payload):
    with open(os.path.join(bdir, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _skeleton(bdir):
    """备料骨架：一条 PENDING（未派单批的 translation.json 长这样）。"""
    _write(bdir, "translation.json", {"translations": [
        {"xml_index": 0, "source": "DEFAULT", "translation": "", "status": "PENDING",
         "confidence": ""}]})


def _unit(status, translation, idx=0):
    return {"xml_index": idx, "source": "DEFAULT",
            "translation": translation, "status": status, "confidence": "HIGH"}


class _BatchDirMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _batch_dir(self, bid):
        d = os.path.join(self.dir, bid)
        os.makedirs(d, exist_ok=True)
        return d


class PureKeepConsumedTest(_BatchDirMixin, unittest.TestCase):
    """已消费的纯 KEEP 批：canonical 无痕，靠 consume 痕迹判 VERIFIED。"""

    def _consumed_keep_batch(self, bid="NI-TES4-900"):
        d = self._batch_dir(bid)
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP",
                                     "confidence": "HIGH"}})
        _write(d, "map.filled.json", {"0": {"translation": "DEFAULT", "status": "KEEP",
                                            "confidence": "HIGH"}})
        _write(d, "translation.json", {"translations": [_unit("KEEP", "DEFAULT")]})
        return d

    def test_classify_reports_verified_and_consumed(self):
        self._consumed_keep_batch()
        r = C.classify(_batch("NI-TES4-900", [0]), self.dir)
        self.assertEqual(r["state"], "VERIFIED")
        self.assertTrue(r["consumed"])
        self.assertEqual(r["resolved"], 1)
        self.assertTrue(r["map_filled"])
        # KEEP 行不进 filled_lines：它没有译文贡献，counting it would inflate
        # the INFO campaign cross-check (pipeline vs canonical).
        self.assertEqual(r["filled"], 0)

    def test_no_stall_warning_in_snapshot(self):
        self._consumed_keep_batch()
        plan = {"batches": [_batch("NI-TES4-900", [0])]}
        s = P.batch_summary(plan, self.dir, None, now=1_700_000_000)
        self.assertEqual(s["verified"], 1)
        self.assertEqual(s["translated"], 0)
        self.assertEqual(s["stalled"], 0)
        self.assertEqual(s["filled_lines"], 0)

    def test_stall_line_not_rendered(self):
        """告警行本身必须消失，不只是计数归零。"""
        d = self._batch_dir("NI-TES4-900")
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "map.filled.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "translation.json", {"translations": [_unit("KEEP", "DEFAULT")]})
        mp = os.path.join(d, "map.json")
        t = 1_700_000_000 - 5 * 3600
        os.utime(mp, (t, t))
        s = P.batch_summary({"batches": [_batch("NI-TES4-900", [0])]},
                            self.dir, None, now=1_700_000_000)
        rendered = "\n".join(P.format_batch_lines(s))
        self.assertNotIn("滞留", rendered)

    def test_keep_rows_not_flagged_unwritten(self):
        """KEEP 行与 canonical 天然相同，不得进「已验收但未写回」。"""
        self._consumed_keep_batch()
        canonical = {0: ("DEFAULT", "DEFAULT")}
        r = C.classify(_batch("NI-TES4-900", [0]), self.dir, canonical)
        self.assertEqual(r["state"], "VERIFIED")
        self.assertEqual(r["unwritten"], 0)


class UnconsumedKeepStillStalledTest(_BatchDirMixin, unittest.TestCase):
    """真没被消费的纯 KEEP 批：必须继续报滞留（本修复最容易做错的地方）。"""

    def _delivered_but_unconsumed(self, bid="NI-TES4-901"):
        """子代理交了 map.json，consume 尚未跑：translation.json 仍是 PENDING 骨架。"""
        d = self._batch_dir(bid)
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP",
                                     "confidence": "HIGH"}})
        _skeleton(d)
        mp = os.path.join(d, "map.json")
        t = 1_700_000_000 - 5 * 3600
        os.utime(mp, (t, t))
        return d

    def test_state_stays_translated(self):
        self._delivered_but_unconsumed()
        r = C.classify(_batch("NI-TES4-901", [0]), self.dir)
        self.assertEqual(r["state"], "TRANSLATED")
        self.assertFalse(r["consumed"])
        self.assertEqual(r["resolved"], 0)

    def test_snapshot_still_reports_stall(self):
        self._delivered_but_unconsumed()
        s = P.batch_summary({"batches": [_batch("NI-TES4-901", [0])]},
                            self.dir, None, now=1_700_000_000)
        self.assertEqual(s["translated"], 1)
        self.assertEqual(s["verified"], 0)
        self.assertEqual(s["stalled"], 1)
        self.assertEqual(s["stalled_batches"][0]["id"], "NI-TES4-901")
        self.assertIn("滞留", "\n".join(P.format_batch_lines(s)))

    def test_prefill_from_canonical_is_not_consumption(self):
        """inherit_prefill 会写满 map.json，但只写 map.json——不算已消费。

        预填是派单前的准备动作（把 canonical 既有译文搬进 map 给译者复用），
        拿它当消费证据等于让「备料齐」冒充「已交付」。
        """
        d = self._batch_dir("NI-TES4-902")
        _write(d, "map.json", {str(i): {"translation": "既有", "status": "TRANSLATED",
                                        "confidence": "HIGH", "notes": "inherited"}
                                for i in range(3)})
        _write(d, "translation.json", {"translations": [
            _unit("PENDING", "", 0), _unit("PENDING", "", 1), _unit("PENDING", "", 2)]})
        r = C.classify(_batch("NI-TES4-902", [0, 1, 2]), self.dir)
        self.assertEqual(r["state"], "TRANSLATED")
        self.assertFalse(r["consumed"])

    def test_consume_crashed_before_fill_is_not_consumption(self):
        """map.filled.json 在五步链第 2 步落盘，崩在 fill 之前时它是唯一痕迹。

        这时若把它当充分条件，一批从未被填进 translation.json 的交付会被永久
        藏成「已验收」——比原缺陷更危险（丢的是真洞）。
        """
        d = self._batch_dir("NI-TES4-903")
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "map.filled.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _skeleton(d)
        r = C.classify(_batch("NI-TES4-903", [0]), self.dir)
        self.assertEqual(r["state"], "TRANSLATED")
        self.assertTrue(r["map_filled"])  # 痕迹在，但不足以定案
        self.assertFalse(r["consumed"])

    def test_empty_map_filled_is_not_consumption(self):
        """空对象 `{}` 不算已消费：它只说明归一化后没有任何条目可 fill。"""
        d = self._batch_dir("NI-TES4-904")
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "map.filled.json", {})
        _skeleton(d)
        r = C.classify(_batch("NI-TES4-904", [0]), self.dir)
        self.assertFalse(r["map_filled"])
        self.assertFalse(r["consumed"])
        self.assertEqual(r["state"], "TRANSLATED")

    def test_corrupt_map_filled_is_not_consumption(self):
        d = self._batch_dir("NI-TES4-905")
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        with open(os.path.join(d, "map.filled.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        _write(d, "translation.json", {"translations": [_unit("KEEP", "DEFAULT")]})
        r = C.classify(_batch("NI-TES4-905", [0]), self.dir)
        self.assertFalse(r["map_filled"])
        # 判据本身仍成立（translation.json 落满），不因旁证损坏而误报滞留
        self.assertTrue(r["consumed"])
        self.assertEqual(r["state"], "VERIFIED")

    def test_partially_filled_batch_is_not_consumed(self):
        """还有 PENDING 残留 = 没消费完，不能整批判 VERIFIED。"""
        d = self._batch_dir("NI-TES4-906")
        _write(d, "map.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "map.filled.json", {"0": {"translation": "DEFAULT", "status": "KEEP"}})
        _write(d, "translation.json", {"translations": [
            _unit("KEEP", "DEFAULT", 0), _unit("PENDING", "", 1)]})
        r = C.classify(_batch("NI-TES4-906", [0, 1]), self.dir)
        self.assertEqual(r["resolved"], 1)
        self.assertFalse(r["consumed"])
        self.assertEqual(r["state"], "TRANSLATED")

    def test_review_status_is_not_consumption(self):
        """REVIEW 是「值已定稿、状态待转正」的中间态，转正前不算已消费。"""
        d = self._batch_dir("NI-TES4-907")
        _write(d, "map.json", {"0": {"translation": "甲", "status": "TRANSLATED"}})
        _write(d, "map.filled.json", {"0": {"translation": "甲", "status": "TRANSLATED"}})
        _write(d, "translation.json", {"translations": [_unit("REVIEW", "甲")]})
        r = C.classify(_batch("NI-TES4-907", [0]), self.dir)
        self.assertFalse(r["consumed"])
        self.assertEqual(r["state"], "TRANSLATED")


class LegacyAndUnchangedStatesTest(_BatchDirMixin, unittest.TestCase):
    """既有判定行为不得改变，另含无 map.filled.json 的早期消费批。"""

    def test_legacy_keep_batch_without_map_filled_still_verified(self):
        """consume 链落地前的老批次没有 map.filled.json；判据本身仍成立。"""
        d = self._batch_dir("NI-TES4-800")
        _write(d, "map.json", {"0": {"translation": "Kaidan Workshop", "status": "KEEP"}})
        _write(d, "translation.json", {"translations": [
            _unit("KEEP", "Kaidan Workshop")]})
        r = C.classify(_batch("NI-TES4-800", [0]), self.dir)
        self.assertFalse(r["map_filled"])
        self.assertTrue(r["consumed"])
        self.assertEqual(r["state"], "VERIFIED")

    def test_translated_verified_unchanged(self):
        d = self._batch_dir("INFO-800")
        _write(d, "map.json", {"0": {"translation": "甲", "status": "TRANSLATED"}})
        _write(d, "translation.json", {"translations": [_unit("TRANSLATED", "甲")]})
        r = C.classify(_batch("INFO-800", [0]), self.dir)
        self.assertEqual(r["state"], "VERIFIED")
        self.assertEqual(r["filled"], 1)
        self.assertEqual(r["resolved"], 1)

    def test_mixed_batch_keeps_filled_semantics(self):
        """KEEP + TRANSLATED 混合批：filled 仍只数 TRANSLATED，判定不变。"""
        d = self._batch_dir("NI-TES4-801")
        _write(d, "map.json", {"0": {"translation": "A", "status": "KEEP"},
                                "1": {"translation": "甲", "status": "TRANSLATED"}})
        _write(d, "translation.json", {"translations": [
            _unit("KEEP", "A", 0), _unit("TRANSLATED", "甲", 1)]})
        r = C.classify(_batch("NI-TES4-801", [0, 1]), self.dir)
        self.assertEqual(r["state"], "VERIFIED")
        self.assertEqual(r["filled"], 1)
        self.assertEqual(r["resolved"], 2)

    def test_prepared_requires_full_trio(self):
        d = self._batch_dir("INFO-801")
        for name, payload in (("index.txt", None), ("context.json", {"batches": []}),
                              ("term-digest.md", None)):
            if payload is None:
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write("0\n")
            else:
                _write(d, name, payload)
        self.assertEqual(C.classify(_batch("INFO-801", [0]), self.dir)["state"], "PREPPED")
        os.remove(os.path.join(d, "term-digest.md"))
        self.assertEqual(C.classify(_batch("INFO-801", [0]), self.dir)["state"], "PARTIAL")

    def test_missing_unchanged(self):
        self.assertEqual(C.classify(_batch("INFO-802", [0]), self.dir)["state"], "MISSING")

    def test_unreadable_translation_is_not_consumption(self):
        d = self._batch_dir("NI-TES4-802")
        _write(d, "map.json", {"0": {"translation": "A", "status": "KEEP"}})
        with open(os.path.join(d, "translation.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        r = C.classify(_batch("NI-TES4-802", [0]), self.dir)
        self.assertFalse(r["consumed"])
        self.assertEqual(r["filled"], -1)
        self.assertEqual(r["state"], "TRANSLATED")

    def test_empty_translations_list_is_not_consumption(self):
        d = self._batch_dir("NI-TES4-803")
        _write(d, "map.json", {"0": {"translation": "A", "status": "KEEP"}})
        _write(d, "translation.json", {"translations": []})
        r = C.classify(_batch("NI-TES4-803", [0]), self.dir)
        self.assertFalse(r["consumed"])
        self.assertEqual(r["state"], "TRANSLATED")


if __name__ == "__main__":
    unittest.main()
