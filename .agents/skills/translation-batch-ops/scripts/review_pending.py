#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_pending.py — 审查欠账清单：已写回批 − 已审批 = 集合差。

与 review_coverage.py 的分工：
  · review_coverage 报「覆盖率」（分母 = 已备料批，看审查动作做了多少）；
  · 本脚本报「欠账清单」（分母 = 已写回批，看多少已进 canonical 的译文还没被审）。
两者共享同一「已审」判定（reports/<stem>-<B>-review-record.json 存在且 status
不在 NOT_DONE），reviewed 集合永远一致；差异只在分母口径。

口径（都是机械的）：
- **已写回批** = 该批 index.txt 的每个 idx 在 canonical 中满足：
  Dest != Source（译文已落），**或** Dest == Source 且该 idx 在批次
  translation.json 中 status == KEEP（KEEP 行本就应保留原文）。
  纯 KEEP 批在 canonical 无痕，故 KEEP 豁免依赖消费痕迹；translation.json
  缺失 / 损坏 / 仍有 PENDING 的批按未写回处理（保守侧倒）。
- **已审批** = review_coverage.read_reviewed()（同一函数，不另写一份）。
- 欠账 PENDING = written − reviewed。IN_PROGRESS/PENDING 占位记录不算已审，
  所在批计入欠账并单列「在途」。
- 「已审但未写回」的批（审查先于写回发生，或写回被回滚）单列提示，不计欠账。

用法：
  py -3 .agents/skills/translation-batch-ops/scripts/review_pending.py --stem <stem>
  py -3 ... review_pending.py --stem <stem> --json [--xml <canonical>]

只读，不改任何文件。退出码 0 正常 / 2 用法错误（canonical 或批次目录缺失）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mod_paths import resolve_mod_dir  # noqa: E402
from review_coverage import read_reviewed  # noqa: E402


def find_canonical(stem: str, xml_arg: str | None) -> pathlib.Path:
    if xml_arg:
        return pathlib.Path(xml_arg)
    base = resolve_mod_dir(None, stem, pathlib.Path.cwd())
    return base / f"{stem}_english_chinese_translated.xml"


def load_rows(xml_path: pathlib.Path) -> list[tuple[str, str]]:
    """[(source, dest)] 按 String 元素序（idx = 0-based 序号）。"""
    root = ET.parse(xml_path).getroot()
    return [(s.findtext("Source") or "", s.findtext("Dest") or "")
            for s in root.iter("String")]


def keep_status_map(tj: pathlib.Path) -> dict[int, str] | None:
    """translation.json -> {xml_index: status}；缺失/损坏返回 None。"""
    if not tj.is_file():
        return None
    try:
        d = json.loads(tj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    out: dict[int, str] = {}
    for x in d.get("translations") or []:
        try:
            out[int(x.get("xml_index"))] = x.get("status") or ""
        except (TypeError, ValueError):
            continue
    return out


def is_written(idxs: list[int], rows: list[tuple[str, str]],
               smap: dict[int, str] | None) -> bool:
    for i in idxs:
        if i < 0 or i >= len(rows):
            return False  # idx 越界：证明不了写回，保守判否
        src, dst = rows[i]
        if dst != src:
            continue
        if smap is not None and smap.get(i) == "KEEP":
            continue  # KEEP 行 dest==source 合法
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="审查欠账清单（canonical 已写回批 − 已审批，只读）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--work-root", default=".work")
    ap.add_argument("--xml", default=None, help="canonical 路径；默认自动探测 mods/<stem>[.esp]/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    base = pathlib.Path(a.work_root) / a.stem
    batches = base / "batches"
    reports = base / "reports"
    if not batches.is_dir():
        print(f"找不到批次目录: {batches}", file=sys.stderr)
        return 2
    canonical = find_canonical(a.stem, a.xml)
    if not canonical.is_file():
        print(f"找不到 canonical: {canonical}", file=sys.stderr)
        return 2

    rows = load_rows(canonical)
    reviewed, in_flight, unreadable = read_reviewed(reports, a.stem)

    written: set[str] = set()
    for d in sorted(batches.iterdir()):
        idx_f = d / "index.txt"
        if not d.is_dir() or not idx_f.is_file():
            continue
        idxs = [int(v) for v in
                (l.strip() for l in idx_f.read_text(encoding="utf-8").splitlines())
                if v.lstrip("-").isdigit()]
        if idxs and is_written(idxs, rows, keep_status_map(d / "translation.json")):
            written.add(d.name)

    pending = sorted(written - reviewed)
    reviewed_not_written = sorted(reviewed - written)
    pending_in_flight = sorted(b for b in pending if b in in_flight)
    if a.json:
        print(json.dumps({
            "stem": a.stem,
            "written": sorted(written), "reviewed": sorted(reviewed & written),
            "pending": pending, "pending_count": len(pending),
            "pending_in_flight": pending_in_flight,
            "reviewed_not_written": reviewed_not_written,
            "unreadable_records": sorted(unreadable),
        }, ensure_ascii=False, indent=1))
    else:
        print(f"written(已写回)={len(written)}  reviewed(已审)={len(reviewed & written)}"
              f"  PENDING(待审)={len(pending)}")
        print("待审:", pending)
        if pending_in_flight:
            print("  其中在途（占位已落、未完成）:", pending_in_flight)
        if reviewed_not_written:
            print("  提示·已审但未写回（不计欠账）:", reviewed_not_written)
        if unreadable:
            print("  !! 记录无法解析（不计入已审）:", sorted(unreadable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
