#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shard_batch.py — split an oversized batch or merge shard maps back.

Why this exists
---------------
Large BOOK batches (single batch source >10000 chars) overload one subagent
instance and abort with zero output. The fix is to shard by *character weight*
(not row count — one 2700-char book outweighs dozens of short titles) and to
merge the shard maps deterministically on the main session.

Commands
--------
split  --stem S --batch B --parts 2     -> index-part-a.txt / index-part-b.txt
merge  --stem S --batch B --parts a b   -> map.json (from map-part-a/b.json)

split is read-only w.r.t. batch artefacts (writes only index-part-*.txt).
merge writes map.json and refuses on key-set mismatch against index.txt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_weights(batch_dir: Path) -> list[tuple[int, int]]:
    """[(idx, source_len)] from context.json (fallback: index.txt, weight 1)."""
    ctx = batch_dir / "context.json"
    if ctx.is_file():
        data = json.loads(ctx.read_text(encoding="utf-8"))
        out = []
        for b in data.get("batches") or []:
            for e in b.get("entries") or []:
                x = e.get("xml") or {}
                if x.get("index") is not None:
                    out.append((int(x["index"]), len(x.get("source") or "")))
        if out:
            return out
    idx = batch_dir / "index.txt"
    return [(int(l), 1) for l in idx.read_text(encoding="utf-8").split() if l.strip()]


def cmd_split(a) -> int:
    batch_dir = Path(a.work_root) / a.stem / "batches" / a.batch
    if not batch_dir.is_dir():
        print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
        return 2
    weights = _load_weights(batch_dir)
    if not weights:
        print("error: 无可拆分的 idx", file=sys.stderr)
        return 2
    n = max(2, a.parts)
    # 贪心：重的先走，每次进当前最轻的一片
    order = sorted(weights, key=lambda x: -x[1])
    buckets: list[list[int]] = [[] for _ in range(n)]
    loads = [0] * n
    for idx, w in order:
        j = loads.index(min(loads))
        buckets[j].append(idx)
        loads[j] += w
    labels = [chr(ord("a") + i) for i in range(n)]
    for lab, idxs, load in zip(labels, buckets, loads):
        idxs.sort()
        p = batch_dir / f"index-part-{lab}.txt"
        p.write_text("\n".join(str(i) for i in idxs) + "\n", encoding="utf-8")
        print(f"part {lab}: {len(idxs)} 条, {load} 字符 -> {p.name}")
    return 0


def cmd_merge(a) -> int:
    batch_dir = Path(a.work_root) / a.stem / "batches" / a.batch
    if not batch_dir.is_dir():
        print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
        return 2
    merged: dict = {}
    for lab in a.parts:
        # maps/ 分片兼容：分片文件不在批次目录时，尝试 maps/<BID>{lab}-map.json
        # （翻译子代理按分片各自交付的扁平 map 命名，如 GAP-INFO-002a-map.json）
        p = batch_dir / a.pattern.format(lab=lab)
        if not p.is_file():
            alt = batch_dir.parent.parent / "maps" / f"{a.batch}{lab}-map.json"
            if alt.is_file():
                p = alt
        if not p.is_file():
            print(f"error: 分片文件不存在: {p}", file=sys.stderr)
            return 2
        part = json.loads(p.read_text(encoding="utf-8"))
        # 值形态归一：扁平字符串分片（子代理交付形态）与 object 分片均可合并
        for k, v in part.items():
            if isinstance(v, str) and v.strip():
                part[k] = {"translation": v.strip(), "status": "TRANSLATED", "confidence": "HIGH"}
        overlap = set(part) & set(merged)
        if overlap:
            print(f"error: {p.name} 与已合并分片键重叠: {sorted(overlap)[:8]}", file=sys.stderr)
            return 2
        merged.update(part)

    expected = {l.strip() for l in (batch_dir / "index.txt").read_text(encoding="utf-8").split() if l.strip()}
    got = set(merged)
    if got != expected:
        print(f"error: 合并键集 != index.txt（缺 {len(expected - got)}、多 {len(got - expected)}）",
              file=sys.stderr)
        print(f"  missing: {sorted(expected - got)[:10]}", file=sys.stderr)
        print(f"  extra:   {sorted(got - expected)[:10]}", file=sys.stderr)
        return 1

    out = batch_dir / "map.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {len(merged)} keys -> {out.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split", help="按字符权重拆分 index")
    sp.add_argument("--stem", required=True)
    sp.add_argument("--batch", required=True)
    sp.add_argument("--parts", type=int, default=2)
    sp.add_argument("--work-root", default=".work")
    sp.set_defaults(func=cmd_split)

    mp = sub.add_parser("merge", help="合并分片 map")
    mp.add_argument("--stem", required=True)
    mp.add_argument("--batch", required=True)
    mp.add_argument("--parts", nargs="+", required=True, help="分片标签，如 a b；或 blockA blockB（配合 --pattern）")
    mp.add_argument("--pattern", default="map-part-{lab}.json",
                    help="分片文件名模式，{lab} 替换为分片标签（默认 map-part-{lab}.json；"
                         "手动拆半场景可传 map-{lab}.json）")
    mp.add_argument("--work-root", default=".work")
    mp.set_defaults(func=cmd_merge)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
