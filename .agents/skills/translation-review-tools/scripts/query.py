#!/usr/bin/env python3
"""跨行查询 xTranslator XML 与批次计划（只读）。

模式（四选一）：
  --src PATTERN   按源文（Source 列）搜索
  --dst PATTERN   按译文（Dest 列）搜索
  --idx N         显示第 N 行；--context K 附带上下 K 行
  --locate N      查 idx 属于哪个批次（扫 --plans glob）

Usage:
  py -3 query.py --xml mods/Artaeum.esp/Artaeum_english_chinese_translated.xml --src Thrall
  py -3 query.py --xml ... --dst 斯罗尔
  py -3 query.py --xml ... --idx 3023 --context 4
  py -3 query.py --locate 3023
  py -3 query.py --xml ... --src "Thrall|斯罗尔" --regex --case-sensitive
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def iter_rows(xml_path: Path):
    root = ET.parse(str(xml_path)).getroot()
    for i, node in enumerate(root.iter("String")):
        yield {
            "idx": i,
            "edid": node.findtext("EDID") or "",
            "rec": node.findtext("REC") or "",
            "src": node.findtext("Source") or "",
            "dst": node.findtext("Dest") or "",
        }


def fmt_row(row: dict) -> str:
    state = "未译" if row["src"] and row["src"] == row["dst"] else "已译"
    head = f"[{row['idx']}] {row['rec']}"
    if row["edid"]:
        head += f" {row['edid']}"
    head += f" {state}"
    return f"{head}\n  SRC: {row['src']}\n  DST: {row['dst']}"


def locate(idx: int, plans: list[str]) -> int:
    hits = 0
    for pattern in plans:
        raw = pattern if Path(pattern).is_absolute() else str(PROJECT_ROOT / pattern)
        for plan_file in sorted(glob.glob(raw)):
            try:
                plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(plan, dict):
                continue
            for batch in plan.get("batches", []):
                if not isinstance(batch, dict):
                    continue
                if idx in batch.get("idx", []):
                    rel = Path(plan_file).resolve().relative_to(PROJECT_ROOT) \
                        if Path(plan_file).resolve().is_relative_to(PROJECT_ROOT) else plan_file
                    print(f"[{idx}] -> {batch.get('id')} ({rel})")
                    hits += 1
    if not hits:
        print(f"[{idx}] 未在任何批次计划中找到", file=sys.stderr)
        return 1
    return 0


def report_anchors(args) -> int:
    """批量锚点核查：每个英文锚 → 全库中文 Dest 形态分布。

    验收时核专名的标配（取代手写 scan_review_terms.py 类脚本）。与
    noun-consistency-scan A pool 互补：本命令不排除对话行（INFO/DIAL），
    且以**词的整词出现**为锚（如 Elise 在 "Follow Elise." 中出现），
    因此能抓到 A pool 因按整句 Source 分组而漏掉的句内专名分裂。
    """
    if not args.xml:
        print("error: --anchors 需要 --xml", file=sys.stderr)
        return 2
    xml_path = resolve(args.xml)
    if not xml_path.is_file():
        print(f"error: XML 不存在: {xml_path}", file=sys.stderr)
        return 2
    apath = resolve(args.anchors)
    try:
        anchors = [l.strip() for l in apath.read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.lstrip().startswith("#")]
    except OSError as exc:
        print(f"error: 无法读取 anchors: {exc}", file=sys.stderr)
        return 2

    rows = list(iter_rows(xml_path))
    limit = args.limit if args.limit else 50
    split_total = 0
    for anchor in anchors:
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(anchor) + r"s?(?![A-Za-z0-9_])",
                         0 if args.case_sensitive else re.IGNORECASE)
        forms: dict[str, list[int]] = {}
        for row in rows:
            src, dst = row.get("src") or "", row.get("dst") or ""
            if not dst or dst == src or not pat.search(src):
                continue
            forms.setdefault(dst, []).append(row["idx"])
        if not forms:
            print(f"[{anchor}] 全库无已译同源")
            continue
        n = len(forms)
        if n > 1:
            split_total += 1
        print(f"[{anchor}] {n} 种中文形态" + ("   <<< 分裂" if n > 1 else ""))
        for dst, ixs in sorted(forms.items(), key=lambda x: -len(x[1]))[:limit]:
            shown = ",".join(str(i) for i in ixs[:6]) + ("..." if len(ixs) > 6 else "")
            print(f"    ({len(ixs)}处) {dst[:70]}  [{shown}]")
    print(f"\n锚点 {len(anchors)} 个，含分裂的 {split_total} 个（分裂为候选，需按语境裁决）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", help="xTranslator XML (translated or source)")
    parser.add_argument("--src", help="Search the Source column")
    parser.add_argument("--dst", help="Search the Dest column")
    parser.add_argument("--idx", type=int, help="Show one row by index")
    parser.add_argument("--locate", type=int, help="Locate which batch an index belongs to")
    parser.add_argument("--context", type=int, default=0, help="With --idx: also show +/-K neighbouring rows")
    parser.add_argument("--plans", action="append", default=None,
                        help="Batch-plan glob for --locate (repeatable; default .work/*/context/*-batches.json)")
    parser.add_argument("--regex", action="store_true", help="Treat the pattern as a regular expression")
    parser.add_argument("--case-sensitive", action="store_true", help="Case-sensitive matching")
    parser.add_argument("--limit", type=int, default=50, help="Max matches to print; 0 means all (default 50)")
    parser.add_argument("--anchors", help="File with one English anchor per line; report each anchor's "
                        "whole-XML Chinese Dest forms (covers dialogue rows too, unlike A-pool scans)")
    args = parser.parse_args()

    if args.locate is not None:
        plans = args.plans if args.plans else [".work/*/context/*-batches.json"]
        return locate(args.locate, plans)

    if args.anchors:
        return report_anchors(args)

    if not args.xml:
        print("error: --xml is required (or use --locate)", file=sys.stderr)
        return 2

    xml_path = resolve(args.xml)
    if not xml_path.is_file():
        print(f"error: XML 不存在: {xml_path}", file=sys.stderr)
        return 2

    rows = list(iter_rows(xml_path))

    if args.idx is not None:
        if not (0 <= args.idx < len(rows)):
            print(f"error: idx 越界（0..{len(rows) - 1}）: {args.idx}", file=sys.stderr)
            return 2
        lo = max(0, args.idx - args.context)
        hi = min(len(rows), args.idx + args.context + 1)
        for row in rows[lo:hi]:
            print(fmt_row(row))
        return 0

    pattern = args.src if args.src is not None else args.dst
    if pattern is None:
        print("error: 需要 --src / --dst / --idx / --locate 之一", file=sys.stderr)
        return 2
    column = "src" if args.src is not None else "dst"

    if args.regex:
        flags = 0 if args.case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern, flags)
        except re.error as exc:
            print(f"error: 非法正则: {exc}", file=sys.stderr)
            return 2
        match = lambda text: bool(matcher.search(text))  # noqa: E731
    else:
        needle = pattern if args.case_sensitive else pattern.lower()
        match = lambda text: needle in (text if args.case_sensitive else text.lower())  # noqa: E731

    hits = 0
    for row in rows:
        if match(row[column]):
            print(fmt_row(row))
            print()
            hits += 1
            if args.limit and hits >= args.limit:
                break
    if hits == 0:
        print(f"无命中（{column}={pattern!r}），共扫描 {len(rows)} 行")
        return 1
    print(f"命中 {hits}{'+' if args.limit and hits >= args.limit else ''} 行（扫描 {len(rows)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
