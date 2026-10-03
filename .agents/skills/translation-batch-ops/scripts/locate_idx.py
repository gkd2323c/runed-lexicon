#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""locate_idx.py — 查一批 xml_index 分别属于哪些批次。

为什么需要它：收口前必须知道每行归哪个批次（`close_round --fixes` 的分组形态按批次组织，
写错键会被更早、更难定位的一步报成「裸格式 fixes 只能配一个 --batch」）。口径统一为
**扫 `.work/<stem>/batches/*/index.txt`**，不查批次计划——计划与批次目录可能不同步，
而且行可能被 `inherit_prefill` / `sync_batch_artifacts` 改过归属。

这个脚本此前是临时脚本，因 `_tmp` 被清而丢失三次（见 MOD PROGRESS §4.6：
凡下一轮还要用的能力必须沉淀进 skill）。

用法：
    py -3 locate_idx.py --stem <S> 6689 6690 6691
    py -3 locate_idx.py --stem <S> --from-file idx.txt
    py -3 locate_idx.py --stem <S> 6689 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def index_owner(batches_dir: Path) -> dict:
    """idx -> batch。首个命中的批为准（批次目录内 idx 正常不重叠）。"""
    owner = {}
    for f in sorted(batches_dir.glob("*/index.txt")):
        bid = f.parent.name
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            for m in re.finditer(r"\d+", line):
                owner.setdefault(int(m.group(0)), bid)
    return owner


def group(owner: dict, idxs) -> dict:
    by = defaultdict(list)
    missing = []
    for i in sorted(set(idxs)):
        b = owner.get(i)
        if b is None:
            missing.append(i)
        else:
            by[b].append(i)
    return {"by_batch": {k: v for k, v in sorted(by.items())}, "missing": missing}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="查 xml_index 的批次归属（扫 batches/*/index.txt）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("idx", nargs="*", type=int, help="xml_index 列表")
    ap.add_argument("--from-file", help="从文件读 idx（每行一个，允许空行与其它字符）")
    ap.add_argument("--work-root", default=".work")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    idxs = list(a.idx)
    if a.from_file:
        for line in Path(a.from_file).read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"\d+", line)
            if m:
                idxs.append(int(m.group(0)))
    if not idxs:
        print("!! 未给任何 idx（位置参数或 --from-file）", file=sys.stderr)
        return 2

    bd = Path(a.work_root) / a.stem / "batches"
    if not bd.is_dir():
        print("!! 批次目录不存在：%s" % bd, file=sys.stderr)
        return 1

    g = group(index_owner(bd), idxs)
    if a.json:
        print(json.dumps(g, ensure_ascii=False, indent=1))
        return 0
    for b, lst in g["by_batch"].items():
        print("  %-16s %3d  %s" % (b, len(lst), lst))
    if g["missing"]:
        print("  未能定位: %s" % g["missing"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
