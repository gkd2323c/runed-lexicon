# -*- coding: utf-8 -*-
"""same_source_family.py — 给定若干 xml_index，列出全库与它们同源的全部行。

收口前的硬前置：同源族只改一行，净效果为零（同源不同形照样触发
`close_round` 的 same_source_split 硬拦截）。族可以跨批，所以不能只看本批。

用法：
  py -3 same_source_family.py --stem <STEM> --idx 41291,41296,41300
  py -3 same_source_family.py --stem <STEM> --idx-file _tmp/data/idx.txt --batches-dir .work/<S>/batches

输出：每个 idx 的全库同源行（idx + 所属批次 + 现译），并标出「本行是否已是孤例」。
只读，不写盘。
"""
from __future__ import annotations

import argparse
import io
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def load_owner(batches_dir):
    owner = {}
    for it in sorted(Path(batches_dir).glob('*/index.txt')):
        bid = it.parent.name
        try:
            vals = [int(x) for x in it.read_text(encoding='utf-8').split()]
        except ValueError:
            continue
        for v in vals:
            owner.setdefault(v, bid)
    return owner


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--idx', default=None, help='逗号分隔的 xml_index')
    ap.add_argument('--idx-file', default=None, help='每行一个 xml_index 的文件')
    ap.add_argument('--batches-dir', default=None)
    ap.add_argument('--xml', default=None,
                    help='canonical XML（默认由 --stem 推导 mods/<stem>.esp/<stem>_..._translated.xml）')
    args = ap.parse_args()

    want = set()
    if args.idx:
        want |= {int(x) for x in args.idx.split(',') if x.strip()}
    if args.idx_file:
        want |= {int(x) for x in Path(args.idx_file).read_text(encoding='utf-8').split() if x.strip()}
    if not want:
        raise SystemExit('error: 需 --idx 或 --idx-file')

    stem = args.stem
    if args.xml:
        xml = Path(args.xml)
    else:
        xml = Path('mods') / f'{stem}.esp' / f'{stem}_english_chinese_translated.xml'
    if not xml.exists():
        raise SystemExit(f'error: canonical XML 不存在: {xml}（可用 --xml 显式指定）')
    srcs, dests = [], []
    for s in ET.parse(xml).getroot().iter('String'):
        srcs.append((s.findtext('Source') or '').strip())
        dests.append((s.findtext('Dest') or '').strip())

    owner = load_owner(args.batches_dir) if args.batches_dir else {}

    bysrc = defaultdict(list)
    for i, s in enumerate(srcs):
        bysrc[s].append(i)

    multi = solo = 0
    for i in sorted(want):
        if i >= len(srcs):
            print(f'idx {i}: 越界（XML 共 {len(srcs)} 行）')
            continue
        fam = bysrc[srcs[i]]
        tag = '孤例' if len(fam) == 1 else f'同源族 {len(fam)} 行'
        if len(fam) == 1:
            solo += 1
        else:
            multi += 1
        print(f'\nidx {i}  [{tag}]  {owner.get(i, "?")}')
        print(f'  SRC: {srcs[i][:100]}')
        for j in fam:
            mark = '>>' if j == i else '  '
            print(f'  {mark} [{j}] {owner.get(j, "未备料批")}  {dests[j][:96]}')
    print(f'\n孤例 {solo} 个 / 多行族 {multi} 个')
    return 0


if __name__ == '__main__':
    sys.exit(main())
