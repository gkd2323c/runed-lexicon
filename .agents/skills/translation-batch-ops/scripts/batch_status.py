#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次真实译况核对：以 canonical 为唯一口径，回答「哪些译了、哪些没译」。

为什么需要它：
  派生物（translation.json 的 status、progress-log 快照、review 报告）都可能与
  canonical 的真实落盘状态不一致。曾把「translation.json 全 PENDING」的批
  （NI-MISC-001）当成「已写回」，因为拿 review_debt 的「有报告否」当成了总状态。
  两个维度必须分开：
    · 译了没 = canonical 里 Dest ≠ Source 的行数   ← 本脚本
    · 审了没 = notes/review-<批>.json 是否存在      ← review_debt.py

用法：
  py -3 .agents/skills/translation-batch-ops/scripts/batch_status.py --stem <stem> \
      [--plan <plan1.json> [--plan <plan2.json> ...]] [--batches-dir <dir>]

对每个批（其 index.txt 的 idx 集合）统计：
  done  = canonical 中 Dest != Source 的行数（已落中文）
  orig  = Dest == Source 的行数（仍是原文，可能未译或本就 KEEP）
  状态   = DONE(全译) / PARTIAL(部分) / UNTRANSLATED(零译)

只读，不修改任何文件。退出码 0。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET


def load_indexes(batches_dir: pathlib.Path, plans: list[pathlib.Path]):
    """返回 {批号: [idx...]}。优先取 index.txt；缺失时回落到计划文件的 idx。"""
    out: dict[str, list[int]] = {}
    if batches_dir.is_dir():
        for d in sorted(batches_dir.iterdir()):
            f = d / "index.txt"
            if d.is_dir() and f.is_file():
                vals = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
                out[d.name] = [int(v) for v in vals if v.lstrip("-").isdigit()]
    for p in plans:
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for b in (d.get("batches") or []):
            bid = b.get("id")
            if bid and bid not in out and b.get("idx"):
                out[bid] = [int(x) for x in b["idx"]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="批次真实译况（以 canonical 为准，只读）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--xml", default=None, help="canonical 路径；默认 mods/<stem>/<stem>_english_chinese_translated.xml")
    ap.add_argument("--work-root", default=".work")
    ap.add_argument("--batches-dir", default=None)
    ap.add_argument("--plan", action="append", default=[], help="可重复；index.txt 缺失的批从计划补")
    ap.add_argument("--only", choices=["done", "partial", "untranslated"], default=None)
    a = ap.parse_args()

    work = pathlib.Path(a.work_root) / a.stem
    batches_dir = pathlib.Path(a.batches_dir) if a.batches_dir else (work / "batches")
    plans = [pathlib.Path(p) for p in a.plan]
    if a.xml:
        xml = pathlib.Path(a.xml)
    else:
        # mod 目录名可能是 <stem> 或 <stem>.esp/.esm/.esl，自动探测
        cands = []
        for suffix in ("", ".esp", ".esm", ".esl"):
            cands.append(pathlib.Path(f"mods/{a.stem}{suffix}/{a.stem}_english_chinese_translated.xml"))
        xml = next((c for c in cands if c.is_file()), cands[0])
    if not xml.is_file():
        print(f"找不到 canonical: {xml}", file=sys.stderr)
        return 2

    rows = list(ET.parse(xml).getroot().iter("String"))
    idx_map = load_indexes(batches_dir, plans)

    buckets = {"done": [], "partial": [], "untranslated": []}
    for bid, idxs in sorted(idx_map.items()):
        done = orig = 0
        for i in idxs:
            if 0 <= i < len(rows):
                if (rows[i].findtext("Source") or "") != (rows[i].findtext("Dest") or ""):
                    done += 1
                else:
                    orig += 1
        total = len(idxs)
        if total == 0:
            continue
        state = "done" if orig == 0 else ("untranslated" if done == 0 else "partial")
        buckets[state].append((bid, done, total))

    for state, label in (("done", "已全译"), ("partial", "部分译"), ("untranslated", "未译")):
        rows_out = buckets[state]
        if a.only and a.only != state:
            continue
        print(f"== {label} ({len(rows_out)} 批) ==")
        for bid, done, total in rows_out:
            print(f"  {bid:16s} {done}/{total}")
    print(f"-- 合计 批 {sum(len(v) for v in buckets.values())}：全译 {len(buckets['done'])} / 部分 {len(buckets['partial'])} / 未译 {len(buckets['untranslated'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
