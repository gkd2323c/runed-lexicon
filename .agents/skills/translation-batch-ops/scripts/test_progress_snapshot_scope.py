#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""progress_snapshot.py 的 INFO 战役交叉核对量纲测试。

事故锚定——三计划齐传（info + noninfo + info-rec）时，
`campaign_from_pipeline` 取的是 merged 三族合计的 `filled_lines`，
右侧 `campaign_from_canonical` 取的却是 canonical 的 **INFO 家族**，
两侧量纲不同，于是恒报「口径不一致」（实测 pipeline=27988 / canonical=23542）。
这个假告警会盖掉真实不一致，也让人无法按 SOP 188 的指引用三计划快照取准确口径。

本组测试锁住：交叉核对的左侧必须与右侧同量纲（都只算 INFO 族），
且批次筛选按 ID 前缀而非 --plan 传入顺序。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_progress_snapshot_scope.py"
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import progress_snapshot as ps  # noqa: E402


def write_xml(path: Path, rows) -> None:
    """rows: [(rec, source, dest)]"""
    body = "".join(
        f"<String><REC>{rec}</REC><Source>{s}</Source><Dest>{d}</Dest></String>"
        for rec, s, d in rows)
    path.write_text(f"<root>{body}</root>", encoding="utf-8")


def write_verified_batch(batches_dir: Path, bid: str, idxs) -> None:
    """造一个 classify() 认作 VERIFIED 的批次：三件套 + 已填 translation.json。"""
    d = batches_dir / bid
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.txt").write_text("\n".join(str(i) for i in idxs) + "\n",
                                encoding="utf-8")
    (d / "context.json").write_text("{}", encoding="utf-8")
    (d / "term-digest.md").write_text("# x\n", encoding="utf-8")
    (d / "map.json").write_text(json.dumps(
        {str(i): {"translation": "译文", "status": "TRANSLATED",
                  "confidence": "HIGH"} for i in idxs}, ensure_ascii=False),
        encoding="utf-8")
    (d / "translation.json").write_text(json.dumps(
        {"translations": [{"xml_index": i, "translation": "译文",
                           "status": "TRANSLATED", "confidence": "HIGH"}
                          for i in idxs]}, ensure_ascii=False), encoding="utf-8")


class InfoScopedBatchesTest(unittest.TestCase):
    def test_nam1_and_rnam_in_dial_out(self):
        rows = [{"id": "INFO-001"},                       # NAM1：无 recs → INFO
                {"id": "NI-DIAL-001", "recs": ["DIAL:FULL"]},
                {"id": "RN-INFO-001", "recs": ["INFO:RNAM"]},  # REC 以 INFO 开头
                {"id": "INFO-002"}]
        got = [b["id"] for b in ps.info_scoped_batches(rows)]
        # INFO:RNAM 在 scan_xml 的族归类里属于 INFO 家族，RN 计划必须计入；
        # DIAL 是独立族，排除。
        self.assertEqual(got, ["INFO-001", "RN-INFO-001", "INFO-002"])

    def test_dial_recs_excluded_even_with_info_in_id(self):
        rows = [{"id": "NI-INFO-DIAL-001", "recs": ["DIAL:FULL"]}]
        self.assertEqual(ps.info_scoped_batches(rows), [])

    def test_mixed_recs_kept_if_any_is_info(self):
        rows = [{"id": "X-1", "recs": ["DIAL:FULL", "INFO:RNAM"]}]
        self.assertEqual(len(ps.info_scoped_batches(rows)), 1)

    def test_preserves_input_order(self):
        rows = [{"id": "INFO-009"}, {"id": "INFO-001"}]
        self.assertEqual([b["id"] for b in ps.info_scoped_batches(rows)],
                         ["INFO-009", "INFO-001"])


class CrosscheckScopeTest(unittest.TestCase):
    """端到端：三计划齐传时两侧必须同量纲。"""

    def _fixture(self, td: str):
        bd = Path(td) / "batches"
        # INFO 计划 2 批共 3 行；另两族共 4 行
        write_verified_batch(bd, "INFO-001", [0, 1])
        write_verified_batch(bd, "INFO-002", [2])
        write_verified_batch(bd, "NI-DIAL-001", [3, 4])
        write_verified_batch(bd, "RN-INFO-001", [5, 6])
        src = Path(td) / "src.xml"
        can = Path(td) / "can.xml"
        # canonical：INFO 族 idx0-2 已译；DIAL/RNAM 族也已译
        rows = [("INFO:0001", "S0", "译文0"), ("INFO:0001", "S1", "译文1"),
                ("INFO:0001", "S2", "译文2"), ("DIAL:FULL", "S3", "译文3"),
                ("DIAL:FULL", "S4", "译文4"), ("INFO:RNAM", "S5", "译文5"),
                ("INFO:RNAM", "S6", "译文6")]
        write_xml(src, [(r, s, "") for r, s, _ in rows])   # 源侧全未译
        write_xml(can, rows)
        p_info = Path(td) / "info.json"
        p_ni = Path(td) / "ni.json"
        p_rn = Path(td) / "rn.json"
        p_info.write_text(json.dumps({"batches": [
            {"id": "INFO-001", "dials": ["L1"], "idx": [0, 1]},
            {"id": "INFO-002", "dials": ["L1"], "idx": [2]}]}),
            encoding="utf-8")
        p_ni.write_text(json.dumps({"batches": [
            {"id": "NI-DIAL-001", "recs": ["DIAL:FULL"], "idx": [3, 4]}]}),
            encoding="utf-8")
        p_rn.write_text(json.dumps({"batches": [
            {"id": "RN-INFO-001", "recs": ["INFO:RNAM"], "idx": [5, 6]}]}),
            encoding="utf-8")
        return str(src), str(can), [str(p_info), str(p_ni), str(p_rn)], str(bd)

    def _build(self, plans):
        args = SimpleNamespace(xml=self.can, source_xml=self.src, plan=plans,
                               batches_dir=self.bd, record=False, log=None,
                               note=None)
        return ps.build_snapshot(args)

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        (self.src, self.can, self.plans,
         self.bd) = self._fixture(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_three_plans_crosscheck_is_consistent(self):
        snap = self._build(self.plans)
        cc = snap["info_crosscheck"]
        # 右侧 = canonical INFO 家族 = INFO:NAM1 3 行 + INFO:RNAM 2 行 = 5
        # （INFO:RNAM 的 REC 同样以 INFO 开头，归在 INFO 家族）
        self.assertEqual(cc["campaign_from_canonical"], 5)
        # 左侧必须同量纲：INFO-001(2)+INFO-002(1)+RN-INFO-001(2)=5；
        # 若把 NI-DIAL 也算进去是 7，会恒报不一致
        self.assertEqual(cc["campaign_from_pipeline"], 5)
        self.assertTrue(cc["consistent"], "三计划齐传时交叉核对必须同量纲")
        self.assertEqual(cc.get("pipeline_scope"), "INFO 族")

    def test_single_plan_crosscheck_unchanged(self):
        snap = self._build([self.plans[0]])
        cc = snap["info_crosscheck"]
        # 单计划（info-batches）时左侧就是该计划合计，行为不变：INFO-001(2)+INFO-002(1)
        self.assertEqual(cc["campaign_from_pipeline"], 3)
        # 右侧 INFO 家族还含 RNAM 2 行 → 单计划模式仍不一致，
        # 这正是 SOP §5「快照必须三份计划齐传」那条限制的由来，行为保持原样
        self.assertEqual(cc["campaign_from_canonical"], 5)
        self.assertFalse(cc["consistent"])
        self.assertNotIn("pipeline_scope", cc)


if __name__ == "__main__":
    unittest.main()
