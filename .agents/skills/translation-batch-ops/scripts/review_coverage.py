#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_coverage.py — 审查覆盖率（按批 + 按行 + 分族）。

为什么需要它：项目里「翻译覆盖率」由 `check_batch_coverage.py` / `scan_plan_gaps.py`
管，但**审查覆盖率**（哪些已写回的批次真正被逐行审过）没有任何工具能报。
此前每轮更新 PROGRESS 都靠临时脚本重写，已因 `_tmp` 被清而丢失两次
（见 PROGRESS §4.6：凡下一轮还要用的能力必须沉淀进 skill）。

口径（都是机械的，不含判断）：
- **已备料批** = `.work/<stem>/batches/<B>/index.txt` 存在（判批归属靠扫它，不猜）
- **批行数** = 该 index.txt 的非空行数
- **已审批次** = `.work/<stem>/reports/<stem>-<B>-review-record.json` 存在，
  且其 `status` 不在 NOT_DONE 里（任务报 succeeded 不等于落盘，所以这里只看文件 + 状态）
- 按批口径 = 已审批次 / 已备料批；按行口径 = 已审批行数之和 / 已备料批行数之和

**为什么不能只看文件存在**：审查线的分步落盘约定让子代理**先**写一份
`status=IN_PROGRESS` 的占位记录（过新鲜度闸就落），再逐批追加 findings，最后改 COMPLETE。
只看存在性会把在途占位算成已审：实测本 MOD 有 36 条 10-02 起的 `INFO-*` 旧记录用的
是没有 `status` 字段的老 schema，同时常驻 3 个在途批——覆盖率一度虚高 2 批 / 91 行。
在途批应当计入**待审**（它本来就没审完），并单独打印出来，别让进度虚高。

用法：
    py -3 review_coverage.py --stem <S>
    py -3 review_coverage.py --stem <S> --json
    py -3 review_coverage.py --stem <S> --work-root .work
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

FAMILY_RE = re.compile(r"^([A-Z]+(?:-[A-Z]+)*)-")


def family_of(batch: str) -> str:
    """RN-INFO-076 -> RN-INFO；INFO-638 -> INFO；NI-DIAL-056 -> NI-DIAL。"""
    m = FAMILY_RE.match(batch)
    return m.group(1) if m else batch


# 审查记录里「还没审完」的状态。分步落盘约定要求子代理先落 IN_PROGRESS 占位，
# 所以这个集合是判「已审」的必要条件，不能只看文件在不在。
# 老 schema 记录没有 status 字段（视为已完成，向后兼容），故用完成态白名单的反面，
# 而不是枚举完成态——将来新增状态名时不会静默把批次丢掉。
NOT_DONE = {"IN_PROGRESS", "PENDING"}


def read_reviewed(reports_dir: Path, stem: str) -> tuple[set, set, set]:
    """审查记录分类。文件名形如 <stem>-<BATCH>-review-record.json。

    返回 (reviewed, in_flight, unreadable)：
    - reviewed   记录存在且状态未标明未完成（含无 status 字段的老 schema）
    - in_flight  记录存在但状态是 IN_PROGRESS/PENDING —— 在途，不计入已审
    - unreadable 记录存在但 JSON 解析失败 —— 证明不了审过，不计入已审并告警
    """
    reviewed: set = set()
    in_flight: set = set()
    unreadable: set = set()
    pat = re.compile(r"^" + re.escape(stem) + r"-(.+)-review-record\.json$")
    for p in sorted(reports_dir.glob("*-review-record.json")):
        m = pat.match(p.name)
        if not m:
            continue
        bid = m.group(1)
        try:
            rec = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            unreadable.add(bid)
            continue
        status = rec.get("status") if isinstance(rec, dict) else None
        if isinstance(status, str) and status in NOT_DONE:
            in_flight.add(bid)
        else:
            reviewed.add(bid)
    return reviewed, in_flight, unreadable


def index_owner(batches_dir: Path) -> dict:
    """idx -> batch。靠扫各批 index.txt，不用计划文件，避免计划与批次目录不同步。"""
    owner = {}
    for f in sorted(batches_dir.glob("*/index.txt")):
        bid = f.parent.name
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            for m in re.finditer(r"\d+", line):
                owner.setdefault(int(m.group(0)), bid)
    return owner


def census(work_dir: Path, stem: str) -> dict:
    batches_dir = work_dir / "batches"
    reports_dir = work_dir / "reports"
    reviewed, in_flight, unreadable = read_reviewed(reports_dir, stem)

    per = defaultdict(lambda: {"batches": 0, "batches_reviewed": 0,
                               "rows": 0, "rows_reviewed": 0})
    missing_index = []
    for d in sorted(batches_dir.iterdir()):
        idx = d / "index.txt"
        if not d.is_dir() or not idx.is_file():
            continue
        bid = d.name
        rows = sum(1 for l in idx.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip())
        fam = family_of(bid)
        p = per[fam]
        p["batches"] += 1
        p["rows"] += rows
        if bid in reviewed:
            p["batches_reviewed"] += 1
            p["rows_reviewed"] += rows

    tb = sum(p["batches"] for p in per.values())
    tr = sum(p["batches_reviewed"] for p in per.values())
    rr = sum(p["rows"] for p in per.values())
    rrr = sum(p["rows_reviewed"] for p in per.values())
    if not batches_dir.is_dir():
        missing_index.append(str(batches_dir))
    return {
        "stem": stem,
        "batches_total": tb, "batches_reviewed": tr, "batches_pending": tb - tr,
        "rows_total": rr, "rows_reviewed": rrr, "rows_pending": rr - rrr,
        "batches_in_flight": sorted(in_flight),
        "batches_unreadable": sorted(unreadable),
        "by_family": {k: dict(v) for k, v in sorted(per.items())},
        "missing_index": missing_index,
    }


def fmt_pct(a: int, b: int) -> str:
    return "%.1f%%" % (a / b * 100.0) if b else "—"


def render(c: dict) -> str:
    out = []
    out.append("审查覆盖率  stem=%s" % c["stem"])
    out.append("  按批：%d/%d 已审 = %s"
               % (c["batches_reviewed"], c["batches_total"],
                  fmt_pct(c["batches_reviewed"], c["batches_total"])))
    out.append("  按行：%d/%d 已审 = %s"
               % (c["rows_reviewed"], c["rows_total"],
                  fmt_pct(c["rows_reviewed"], c["rows_total"])))
    out.append("")
    out.append("  %-12s %14s %18s %10s" % ("族", "批次已审", "行已审", "待审行"))
    for fam, p in sorted(c["by_family"].items()):
        out.append("  %-12s %6d/%-6d %7d/%-8d %7d/%-8d %6s"
                   % (fam, p["batches_reviewed"], p["batches"],
                      p["rows_reviewed"], p["rows"],
                      p["rows"] - p["rows_reviewed"], p["rows"],
                      fmt_pct(p["rows_reviewed"], p["rows"])))
    for m in c["missing_index"]:
        out.append("  !! 批次目录缺失：%s" % m)
    if c["batches_in_flight"]:
        out.append("  在途（占位已落、status 未完成，不计入已审）：%d 批  %s"
                   % (len(c["batches_in_flight"]), ", ".join(c["batches_in_flight"])))
    if c["batches_unreadable"]:
        out.append("  !! 记录无法解析（不计入已审）：%d 批  %s"
                   % (len(c["batches_unreadable"]), ", ".join(c["batches_unreadable"])))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="审查覆盖率（按批/按行/分族）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--work-root", default=".work")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    work = Path(a.work_root) / a.stem
    if not work.is_dir():
        print("!! 工作目录不存在：%s" % work, file=sys.stderr)
        return 1
    c = census(work, a.stem)
    print(json.dumps(c, ensure_ascii=False, indent=1) if a.json else render(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
