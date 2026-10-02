# -*- coding: utf-8 -*-
"""anchor_overlap.py — 核某个中文形到底在渲染哪些英文锚（跨锚撞形诊断）。

用途：一个中文形可能同时落在两个不同英文锚上（如「权柄」既渲染 authority
又渲染 power）。词形普查只给中文形分布，看不出这一点；跨锚撞形是独立一类，
不能当同源漂移处理，也不能按中文形多数收敛——必须先知道每行归哪个锚。

用法：py -3 anchor_overlap.py --xml <canonical.xml> --form 权柄 --anchors authority,authoritative,power
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--xml', required=True)
    ap.add_argument('--form', required=True, help='要诊断的中文形')
    ap.add_argument('--anchors', required=True, help='逗号分隔的英文锚')
    ap.add_argument('--show', type=int, default=3, help='每组样例行数')
    args = ap.parse_args()

    anchors = [a.strip().lower() for a in args.anchors.split(',') if a.strip()]
    pats = {a: re.compile(r'\b' + re.escape(a) + r'\w*\b', re.I) for a in anchors}

    root = ET.parse(args.xml).getroot()
    groups = defaultdict(list)
    unmatched = []
    for i, s in enumerate(root.findall('.//String')):
        dest = (s.findtext('Dest') or '')
        if args.form not in dest:
            continue
        src = (s.findtext('Source') or '')
        hit = [a for a, p in pats.items() if p.search(src)]
        if not hit:
            unmatched.append(i)
            key = '（无锚命中）'
        else:
            key = hit[0] if len(hit) == 1 else '多锚:' + '+'.join(hit)
        groups[key].append((i, src, dest))

    print(f'中文形「{args.form}」共 {sum(len(v) for v in groups.values())} 行')
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        rows = groups[k]
        print(f'\n  锚 {k}: {len(rows)} 行')
        for i, src, dest in rows[:args.show]:
            print(f'    [{i}] {src[:88]}')
            print(f'         -> {dest[:88]}')
    if unmatched:
        print(f'\n  源文不含任一锚: {len(unmatched)} 行（样例 idx: {unmatched[:8]}）')


if __name__ == '__main__':
    main()
