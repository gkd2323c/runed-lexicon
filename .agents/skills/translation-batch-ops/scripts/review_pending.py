#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审查待审队列统计：已写回批 − 有审查报告的批 = 集合差。

用法：
  py -3 .agents/skills/translation-batch-ops/scripts/review_debt.py --stem <stem>

口径（纪律 61）：
- 「已写回批」= batches/<批>/translation.json 存在且含 status ∈ {TRANSLATED, KEEP} 的条目。
- 「有报告批」= notes/review-<批>.json 存在（排除 review-view-* 等视图文件）。
- 欠账 = 前者减后者。**这是集合差，不是报告份数**；报告份数会把「一份报告覆盖多批」
  或被移除的批算错。

只读，不改任何文件。输出 written / reviewed / DEBT 三个数与欠账批号清单。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="审查欠账集合差统计（只读）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--work-root", default=".work")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    base = pathlib.Path(a.work_root) / a.stem
    notes = base / "notes"
    batches = base / "batches"
    if not batches.is_dir():
        print(f"找不到批次目录: {batches}", file=sys.stderr)
        return 2

    reviewed = set()
    if notes.is_dir():
        for f in notes.glob("review-*.json"):
            if f.name.startswith("review-view"):
                continue
            m = re.match(r"review-(.+)\.json$", f.name)
            if m:
                reviewed.add(m.group(1))

    written = set()
    for d in sorted(batches.iterdir()):
        if not d.is_dir():
            continue
        tj = d / "translation.json"
        if not tj.is_file():
            continue
        try:
            t = json.loads(tj.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items = t.get("translations") or []
        if any(x.get("status") in ("TRANSLATED", "KEEP") for x in items):
            written.add(d.name)

    debt = sorted(written - reviewed)
    if a.json:
        print(json.dumps({"written": sorted(written), "reviewed": sorted(reviewed),
                          "pending": debt, "pending_count": len(debt)},
                         ensure_ascii=False, indent=1))
    else:
        print(f"written(已写回)={len(written)}  reviewed(已审)={len(reviewed)}  PENDING(待审)={len(debt)}")
        print("待审:", debt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
