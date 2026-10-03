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
  --override   特裁项：{idx: 新值} JSON。覆盖报告建议值（部分采纳 / 语境重造），
               且可**引入报告外的 idx**——跨批同锚词收敛时 reviewer 往往只点名
               一侧，另一侧由主会话按全库多数裁定（见 introduce_overrides）
  --align-dups 同源句副本对齐：修正集的裁决值广播到全库同源句的所有副本
               （不传时仍检测并打印分歧清单，供人工判断是否对齐）
  --batches    配合 --batches-dir，按批次分组输出 close_round 需要的
               {batch: {idx: fix}} 形态（见下方「多批修正」）

多批修正（本选项存在的理由）：
  close_round.py 的 --fixes 接受分组形态 {batch: {idx: {new}}}，而本脚本默认输出
  扁平 {idx: {new}}；扁平形态只配一个 --batch，多批修正必须先转分组，而手转
  分组的失效点是「idx 归属哪个批次」判错——写错时 grouped 键会变成 idx 本身，
  close_round 随后报「裸格式 fixes 只能配一个 --batch」，而错误发生在更早、
  更难定位的一步。传 --batches 后归属由各批 index.txt 机械判定，判不出直接失败。

用法：
  py -3 make_fixes_from_report.py \
      --report .work/<stem>/notes/review-INFO-XXX.json \
      --xml mods/<stem>/<stem>_english_chinese_translated.xml \
      --out .work/<stem>/maps/<stem>-fix-map-review-XXX.json \
      [--drop 4960,4981] [--override ov.json] [--align-dups]

  # 多批修正（--batches 必与 --batches-dir 同用）
  py -3 make_fixes_from_report.py --report <review.json> --xml <translated.xml> \
      --batches INFO-334 INFO-335 --batches-dir .work/<stem>/batches \
      --out .work/<stem>/maps/<stem>-fix-map-334-335.json
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


def load_batch_owner(batches_dir: str, batches: list[str]) -> dict[int, str]:
    """idx → 批次名。归属只认各批 index.txt，判不出即抛错，不猜。"""
    owner: dict[int, str] = {}
    for bid in batches:
        idx_path = Path(batches_dir) / bid / "index.txt"
        if not idx_path.is_file():
            raise SystemExit(f"批次缺 index.txt，无法判定 idx 归属: {idx_path}")
        for line in idx_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            i = int(line)
            if i in owner and owner[i] != bid:
                raise SystemExit(
                    f"idx {i} 同时属于 {owner[i]} 与 {bid}，批次计划重叠，拒绝猜测")
            owner[i] = bid
    return owner


def introduce_overrides(overrides: dict[int, str], reported: set[int],
                        drops: set[int], includes: set[int],
                        dests: list[str]) -> tuple[dict[int, dict], list[int], list[int]]:
    """把 --override 中**不在报告 findings 里**的 idx 补进修正集。

    跨批收敛的常态是「审查只点名了同锚词的一侧，另一侧由主会话裁决」。
    事故锚定——institution 在全库 81:4 悬殊，reviewer 按批内少数形给出
    「统一到制度」的建议，主会话按全库多数反向裁定要改「制度」那几行；
    而那些行不在该批报告的 findings 中，旧版 override 只在 findings 循环内
    取值（`overrides.get(i, it["proposed"])`），这类裁决无处落盘，只能手改
    修正集或另写临时脚本——正是本函数要消除的缺口。

    显式 --drop 仍优先于 override：驳回就是驳回，不被「特裁」复活。
    返回 (新增修正, 现值已同的 no-op idx, 越界 idx)。
    """
    fixes: dict[int, dict] = {}
    noop: list[int] = []
    oob: list[int] = []
    for i in sorted(overrides):
        if i in reported or i in drops or (includes and i not in includes):
            continue
        if not (0 <= i < len(dests)):
            oob.append(i)
            continue
        new = overrides[i]
        if dests[i] == new:
            noop.append(i)
            continue
        fixes[i] = {"new": new, "expected_current": dests[i]}
    return fixes, noop, oob


def group_fixes(fixes: dict[int, dict], owner: dict[int, str],
                batches: list[str]) -> dict[str, dict]:
    """扁平修正集 → close_round 的分组形态；归属判不出的 idx 直接失败。"""
    grouped: dict[str, dict] = {}
    orphans = sorted(i for i in fixes if i not in owner)
    if orphans:
        raise SystemExit(
            f"以下 idx 不属于任何声明批次，无法分组：{orphans}；"
            f"把所属批次加进 --batches，或确认报告里的 idx 是否写错")
    for i, fix in sorted(fixes.items()):
        grouped.setdefault(owner[i], {})[str(i)] = fix
    # 保持 --batches 的声明顺序，便于人工核对输出
    return {bid: grouped[bid] for bid in batches if bid in grouped}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="审查报告 → 修正集生成")
    ap.add_argument("--report", required=True, nargs="+",
                    help="审查报告 JSON（findings[] 或 issues[]）；可给多份，"
                         "同源副本与批次归属跨报告一并处理（每轮常审 3 批）")
    ap.add_argument("--xml", required=True, help="canonical 译文 XML（读现值与同源副本）")
    ap.add_argument("--out", required=True, help="fix map 输出路径")
    ap.add_argument("--drop", default=None, help="驳回的 idx（逗号/空格分隔）")
    ap.add_argument("--override", default=None, help="特裁 JSON：{idx: 新值}")
    ap.add_argument("--align-dups", action="store_true",
                    help="把裁决值广播到全库同源句所有副本（默认仅检测并打印）")
    ap.add_argument("--include", default=None, help="仅处理指定 idx（逗号/空格分隔）")
    ap.add_argument("--batches", nargs="+", default=None,
                    help="按批次分组输出（close_round 的 {batch: {idx: fix}} 形态）")
    ap.add_argument("--batches-dir", default=None,
                    help="批次目录（默认 .work/<stem>/batches），--batches 必与它同用")
    args = ap.parse_args()
    if bool(args.batches) != bool(args.batches_dir):
        raise SystemExit("--batches 与 --batches-dir 必须同用")

    items: list[dict] = []
    for rp in args.report:
        items.extend(parse_report(json.loads(Path(rp).read_text(encoding="utf-8"))))
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

    # 特裁可引入报告外的 idx（跨批收敛时 reviewer 往往只点名一侧）
    reported = {it["idx"] for it in items}
    extra, extra_noop, extra_oob = introduce_overrides(
        overrides, reported, drops, includes, dests)
    for i in extra_oob:
        print(f"  WARN override idx 越界: {i}", file=sys.stderr)
    fixes.update(extra)
    noop.extend(extra_noop)
    introduced = sorted(extra)
    overridden.extend(introduced)

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
    if args.batches:
        batches_dir = args.batches_dir
        if not batches_dir:
            stem = Path(args.report).parts[2] if len(Path(args.report).parts) > 2 else None
            if not stem:
                raise SystemExit("无法从报告路径推断 stem，请显式传 --batches-dir")
            batches_dir = str(Path(".work") / stem / "batches")
        owner = load_batch_owner(batches_dir, args.batches)
        payload = {bid: {k: v for k, v in fx.items()}
                   for bid, fx in group_fixes(fixes, owner, args.batches).items()}
    else:
        payload = {str(k): v for k, v in sorted(fixes.items())}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"report: {len(args.report)} 份")
    for rp in args.report:
        print(f"  - {rp}")
    print(f"报告条目 {len(items)}；修正 {len(fixes) - dup_aligned}；"
          f"特裁 {len(overridden)}（报告外新增 {len(introduced)}）；"
          f"驳回 {len(dropped)}；no-op {len(noop)}")
    if dropped:
        print(f"  驳回: {sorted(dropped)}")
    if introduced:
        print(f"  特裁新增（报告外，reviewer 未点名）: {introduced}")
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
    if args.batches:
        print("分组: " + "，".join(f"{bid} {len(fx)} 条" for bid, fx in payload.items()))
        print("  → close_round --batches " + " ".join(payload))
    print(f"fix map -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
