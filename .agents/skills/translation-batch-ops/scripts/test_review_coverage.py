# -*- coding: utf-8 -*-
"""review_coverage.py 回归测试。

真事故背景：审查覆盖率此前只有临时脚本，`_tmp` 被清后丢了两次；
且「任务报 succeeded」不等于「审查记录落盘」——派 6 个批次时曾 3 批 succeeded
却零文件产出。所以口径必须只看 `reports/*-review-record.json` 是否存在。

第二个事故：只看「存在」也不够。分步落盘约定让子代理先落 `status=IN_PROGRESS`
的占位（过新鲜度闸就写），再追加 findings，最后才 COMPLETE。实测 TheKalpicAnomaly_GLENMORIL
一度把在途占位算成已审，覆盖率虚高 2 批 / 91 行。所以还要读 status。
"""
import json
import tempfile
import unittest
from pathlib import Path

import review_coverage as rc


def make_work(root: Path, batches, reviewed=(), records=None):
    """batches: {batch: [idx...]}；reviewed: [batch...]（落 `{}`，即无 status 的老 schema）

    records: {batch: 完整 JSON 文本}，用于显式指定 status；优先于 reviewed。
    """
    bd = root / "batches"
    rd = root / "reports"
    bd.mkdir(parents=True)
    rd.mkdir(parents=True)
    for b, idxs in batches.items():
        (bd / b).mkdir()
        (bd / b / "index.txt").write_text(
            "\n".join(str(i) for i in idxs) + "\n", encoding="utf-8")
    for b in reviewed:
        (rd / ("MyMod-%s-review-record.json" % b)).write_text("{}", encoding="utf-8")
    for b, text in (records or {}).items():
        (rd / ("MyMod-%s-review-record.json" % b)).write_text(text, encoding="utf-8")
    return root


class FamilyTest(unittest.TestCase):
    def test_family_of(self):
        self.assertEqual(rc.family_of("RN-INFO-076"), "RN-INFO")
        self.assertEqual(rc.family_of("NI-DIAL-056"), "NI-DIAL")
        self.assertEqual(rc.family_of("INFO-638"), "INFO")

    def test_family_of_unnumbered(self):
        self.assertEqual(rc.family_of("SOMETHING"), "SOMETHING")


class ReadReviewedTest(unittest.TestCase):
    def test_only_exact_pattern_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1]}, ["INFO-001"])
            rd = root / "reports"
            # 干扰项：别的 stem / 别的后缀，都不该被算成已审
            (rd / "OtherMod-INFO-002-review-record.json").write_text("{}", encoding="utf-8")
            (rd / "MyMod-INFO-003-gate-report.json").write_text("{}", encoding="utf-8")
            reviewed, in_flight, unreadable = rc.read_reviewed(rd, "MyMod")
            self.assertEqual(reviewed, {"INFO-001"})
            self.assertEqual(in_flight, set())
            self.assertEqual(unreadable, set())

    def test_missing_status_is_reviewed(self):
        """老 schema 记录没有 status 字段，视为已完成（向后兼容，36 条在用）。"""
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1]}, ["INFO-001"])
            reviewed, _, _ = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, {"INFO-001"})

    def test_in_progress_not_counted_as_reviewed(self):
        """在途占位必须计入待审，否则覆盖率虚高。"""
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {}, records={
                "RN-INFO-037": '{"status": "IN_PROGRESS", "findings": []}',
                "RN-INFO-038": '{"status": "PENDING"}',
            })
            reviewed, in_flight, _ = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, set())
            self.assertEqual(in_flight, {"RN-INFO-037", "RN-INFO-038"})

    def test_complete_and_reviewed_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {}, records={
                "INFO-001": '{"status": "COMPLETE", "findings": [1]}',
                "INFO-002": '{"status": "REVIEWED"}',
            })
            reviewed, in_flight, _ = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, {"INFO-001", "INFO-002"})
            self.assertEqual(in_flight, set())

    def test_unknown_status_not_silently_dropped(self):
        """将来新增完成态名不该让批次凭空消失（用 NOT_DONE 白名单而非枚举完成态）。"""
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {}, records={"INFO-001": '{"status": "SOME_NEW_DONE"}'})
            reviewed, _, _ = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, {"INFO-001"})

    def test_unreadable_record_excluded(self):
        """坏 JSON 证明不了审过：不算已审，且要被告警出来。"""
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {}, records={"INFO-001": "{not json"})
            reviewed, in_flight, unreadable = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, set())
            self.assertEqual(in_flight, set())
            self.assertEqual(unreadable, {"INFO-001"})

    def test_non_dict_record_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {}, records={"INFO-001": "[1, 2]"})
            reviewed, _, _ = rc.read_reviewed(root / "reports", "MyMod")
            self.assertEqual(reviewed, {"INFO-001"})


class IndexOwnerTest(unittest.TestCase):
    def test_owner_from_index_txt(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [10, 12], "RN-INFO-001": [11]}, [])
            owner = rc.index_owner(root / "batches")
            self.assertEqual(owner[10], "INFO-001")
            self.assertEqual(owner[11], "RN-INFO-001")
            self.assertEqual(owner[12], "INFO-001")


class CensusTest(unittest.TestCase):
    def test_mixed_family_totals(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(
                Path(td),
                {"INFO-001": [1, 2, 3], "INFO-002": [4, 5], "RN-INFO-001": [6, 7, 8, 9]},
                ["INFO-001", "RN-INFO-001"],
            )
            c = rc.census(root, "MyMod")
            self.assertEqual(c["batches_total"], 3)
            self.assertEqual(c["batches_reviewed"], 2)
            self.assertEqual(c["batches_pending"], 1)
            self.assertEqual(c["rows_total"], 9)
            self.assertEqual(c["rows_reviewed"], 7)
            self.assertEqual(c["rows_pending"], 2)
            self.assertEqual(c["by_family"]["INFO"]["rows_reviewed"], 3)
            self.assertEqual(c["by_family"]["RN-INFO"]["batches_reviewed"], 1)

    def test_nothing_reviewed(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1, 2], "INFO-002": [3]}, [])
            c = rc.census(root, "MyMod")
            self.assertEqual(c["batches_reviewed"], 0)
            self.assertEqual(c["rows_reviewed"], 0)
            self.assertEqual(c["batches_pending"], 2)
            self.assertEqual(c["rows_pending"], 3)

    def test_blank_lines_not_counted_as_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1]}, [])
            (root / "batches" / "INFO-001" / "index.txt").write_text(
                "1\n\n2\n   \n3\n", encoding="utf-8")
            c = rc.census(root, "MyMod")
            self.assertEqual(c["rows_total"], 3)

    def test_dir_without_index_txt_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1]}, [])
            (root / "batches" / "INFO-002").mkdir()
            c = rc.census(root, "MyMod")
            self.assertEqual(c["batches_total"], 1)

    def test_in_flight_batch_counts_as_pending(self):
        """在途批留在总数里、但落进 pending，不进 reviewed。"""
        with tempfile.TemporaryDirectory() as td:
            root = make_work(
                Path(td),
                {"RN-INFO-037": [1, 2, 3], "RN-INFO-038": [4, 5]},
                records={"RN-INFO-037": '{"status": "IN_PROGRESS"}'},
            )
            c = rc.census(root, "MyMod")
            self.assertEqual(c["batches_total"], 2)
            self.assertEqual(c["batches_reviewed"], 0)
            self.assertEqual(c["batches_pending"], 2)
            self.assertEqual(c["rows_total"], 5)
            self.assertEqual(c["rows_reviewed"], 0)
            self.assertEqual(c["rows_pending"], 5)
            self.assertEqual(c["batches_in_flight"], ["RN-INFO-037"])
            self.assertEqual(c["by_family"]["RN-INFO"]["batches_reviewed"], 0)


class FmtPctTest(unittest.TestCase):
    def test_zero_denominator_is_dash(self):
        self.assertEqual(rc.fmt_pct(0, 0), "—")

    def test_rounds_to_one_decimal(self):
        self.assertEqual(rc.fmt_pct(1, 3), "33.3%")


class RenderTest(unittest.TestCase):
    def test_in_flight_surfaced(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"RN-INFO-037": [1]}, records={
                "RN-INFO-037": '{"status": "IN_PROGRESS"}'})
            out = rc.render(rc.census(root, "MyMod"))
            self.assertIn("在途", out)
            self.assertIn("RN-INFO-037", out)

    def test_unreadable_surfaced_as_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_work(Path(td), {"INFO-001": [1]}, records={"INFO-001": "{bad"})
            out = rc.render(rc.census(root, "MyMod"))
            self.assertIn("无法解析", out)
            self.assertIn("INFO-001", out)


if __name__ == "__main__":
    unittest.main()
