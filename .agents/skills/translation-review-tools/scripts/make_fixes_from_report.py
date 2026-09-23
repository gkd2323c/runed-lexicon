#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_fixes_from_report.py — 审查报告 → 修正集（fix map）一键生成。

把「审查报告 findings → 修正清单」这个每轮审查核销都在重复的手工转录步骤
固化为工具（此前每次重写临时脚本，属流程缺陷）：

  report.json（findings[] 或旧式 issues[] 两种 schema）
    + canonical translated XML（读现值 / 同源副本扫描）
    → fix map {idx: {new, expected_current}}（apply_fixes.py 直接消费）

处置选项（对应核销中的三类人工裁决）：
  --drop       驳回项（如「官方实证现状正确，不采纳该建议」），从修正集剔除
  --override   特裁项：{idx: 新值} JSON，覆盖报告建议值（部分采纳 / 语境重造）
  --align-dups 同源句副本对齐：修正集的裁决值广播到全库同源句的所有副本
               （不传时仍检测并打印分歧清单，供人工判断是否对齐）

用法：
  py -3 make_fixes_from_report.py \
      --report .work/<stem>/notes/review-INFO-XXX.json \
      --xml mods/<stem>/<stem>_english_chinese_translated.xml \
      --out .work/<stem>/maps/<stem>-fix-map-review-XXX.json \
      [--drop 4960,4981] [--override ov.json] [--align-dups]
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_idx_set(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.replace(",", " ").split():
        out.add(int(part))
    return out


def parse_report(data) -> list[dict]:
    """兼容两种报告 schema，返回 [{idx, proposed, source}]。"""
    items: list[dict] = []
    if isinstance(data, dict):
        for f in data.get("findings") or []:
            idx = f.get("xml_index", f.get("idx"))
            proposed = f.get("proposed") or f.get("suggestion") or f.get("suggested")
            if idx is None or not proposed:
                continue
            items.append({"idx": int(idx), "proposed": proposed,
                          "source": f.get("source") or ""})
    elif isinstance(data, list):
        for grp in data:
            if not isinstance(grp, dict):
                continue
            for it in grp.get("issues") or []:
                idx = it.get("idx")
                proposed = it.get("suggested")
                if idx is None or not proposed:
                    continue
                items.append({"idx": int(idx), "proposed": proposed,
                              "source": it.get("source") or ""})
    else:
        raise ValueError("报告格式无法识别（应为 findings[] 对象或 issues[] 列表）")
    return items


def load_canonical(path: str):
    root = ET.parse(path).getroot()
    strings = list(root.iter("String"))
    dests = [s.findtext("Dest") or "" for s in strings]
    srcs = [s.findtext("Source") or "" for s in strings]
    return srcs, dests


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="审查报告 → 修正集生成")
    ap.add_argument("--report", required=True, help="审查报告 JSON（findings[] 或 issues[]）")
    ap.add_argument("--xml", required=True, help="canonical 译文 XML（读现值与同源副本）")
    ap.add_argument("--out", required=True, help="fix map 输出路径")
    ap.add_argument("--drop", default=None, help="驳回的 idx（逗号/空格分隔）")
    ap.add_argument("--override", default=None, help="特裁 JSON：{idx: 新值}")
    ap.add_argument("--align-dups", action="store_true",
                    help="把裁决值广播到全库同源句所有副本（默认仅检测并打印）")
    ap.add_argument("--include", default=None, help="仅处理指定 idx（逗号/空格分隔）")
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    items = parse_report(report)
    srcs, dests = load_canonical(args.xml)
    drops = parse_idx_set(args.drop)
    includes = parse_idx_set(args.include)
    overrides = {}
    if args.override:
        overrides = {int(k): v for k, v in
                     json.loads(Path(args.override).read_text(encoding="utf-8")).items()}

    fixes: dict[int, dict] = {}
    dropped, noop, overridden = [], [], []
    for it in items:
        i = it["idx"]
        if includes and i not in includes:
            continue
        if i in drops:
            dropped.append(i)
            continue
        new = overrides.get(i, it["proposed"])
        if i in overrides:
            overridden.append(i)
        if not (0 <= i < len(dests)):
            print(f"  WARN idx 越界: {i}", file=sys.stderr)
            continue
        cur = dests[i]
        if cur == new:
            noop.append(i)
            continue
        fixes[i] = {"new": new, "expected_current": cur}

    # 同源副本检测：修正集内 idx 的源文在别处的副本译文是否与裁决值不一致。
    by_src: dict[str, list[int]] = {}
    for idx, src in enumerate(srcs):
        if src:
            by_src.setdefault(src, []).append(idx)
    dup_groups = []
    dup_aligned = 0
    for i, fix in list(fixes.items()):
        group = by_src.get(srcs[i]) or []
        others = [j for j in group if j != i]
        if not others:
            continue
        divergent = [j for j in others if dests[j] != fix["new"] and j not in fixes]
        if divergent:
            dup_groups.append((i, divergent))
            if args.align_dups:
                for j in divergent:
                    fixes[j] = {"new": fix["new"], "expected_current": dests[j]}
                    dup_aligned += 1

    out_path = Path(args.out)
    out_path.write_text(json.dumps({str(k): v for k, v in sorted(fixes.items())},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report: {args.report}")
    print(f"报告条目 {len(items)}；修正 {len(fixes) - dup_aligned}；"
          f"特裁 {len(overridden)}；驳回 {len(dropped)}；no-op {len(noop)}")
    if dropped:
        print(f"  驳回: {sorted(dropped)}")
    if noop:
        print(f"  no-op（现值已同）: {sorted(noop)}")
    if dup_groups:
        mode = "已对齐" if args.align_dups else "仅报告（未对齐，加 --align-dups 广播）"
        print(f"同源副本分歧 {len(dup_groups)} 组（{mode}）：")
        for trigger, div in dup_groups:
            print(f"  基准 [{trigger}] → 副本 {div}")
            print(f"    {fixes[trigger]['new'][:70]}")
        if args.align_dups:
            print(f"  → 已扩展修正 {dup_aligned} 条")
    print(f"fix map -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
