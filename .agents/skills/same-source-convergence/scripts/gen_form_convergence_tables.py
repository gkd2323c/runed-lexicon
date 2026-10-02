# -*- coding: utf-8 -*-
"""gen_form_convergence_tables.py — 按「中文形→目标形」+ 锚判定生成裁决表。

用于「某个中文形跨多个英文锚、需要按锚分别收敛」这类跳批收敛：
读 canonical，按规则算出每行目标译文，**以源句为键**（不是以行为键）归并，
再按批次拆表。同源族跨批时每个相关批次都会拿到同一条 源句->目标 映射，
避免「只改一族里一半行」——那正是 same_source_split 要拦的东西。

用法：
  py -3 gen_form_convergence_tables.py --stem <STEM> \
      --rule "权柄:authority->威权" --rule "权柄:power->权力" \
      --out-dir _tmp/data/authtabs

规则语法：<现形>:<锚>-><目标形>，锚用逗号分隔表示「源文命中任一即适用」。
锚为空（写作 `<现形>::<目标形>`）表示不限锚。
"""
from __future__ import annotations

import argparse
import io
import json
import re
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
    ap.add_argument('--xml', default=None)
    ap.add_argument('--batches-dir', default=None)
    ap.add_argument('--rule', action='append', required=True,
                    help='<现形>:<锚,锚>-><目标形>')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--apply', action='store_true',
                    help='真正落盘；默认只打印计划（防手滑写出一堆错表）')
    args = ap.parse_args()

    rules = []
    for raw in args.rule:
        m = re.match(r'^([^:]+):([^:]*)->(.+)$', raw.strip())
        if not m:
            raise SystemExit(f'error: 规则格式应为 <现形>:<锚,锚>-><目标形>，收到 {raw!r}')
        form, anchors, target = m.group(1), m.group(2), m.group(3)
        if target == form:
            raise SystemExit(f'error: 目标形与现形相同，规则无意义: {raw!r}')
        pats = [re.compile(r'\b' + re.escape(a) + r'\w*\b', re.I)
                for a in anchors.split(',') if a.strip()]
        rules.append({'form': form, 'pats': pats, 'target': target, 'raw': raw})
    # 同一现形可以有多条规则（不同锚 -> 不同目标形），这正是「跨锚撞形」收敛的用法。
    # 命中时第一条匹配的规则生效（下游 break）；真出现自相矛盾时，
    # 由「同一源句算出两个不同目标即中止」那条检查兜住。

    stem = args.stem
    xml = Path(args.xml) if args.xml else (
        Path('mods') / f'{stem}.esp' / f'{stem}_english_chinese_translated.xml')
    srcs, dests = [], []
    for s in ET.parse(xml).getroot().iter('String'):
        srcs.append((s.findtext('Source') or '').strip())
        dests.append((s.findtext('Dest') or '').strip())

    owner = load_owner(args.batches_dir) if args.batches_dir else {}

    # 1) 逐行按锚判定，得到 源句 -> 目标译文
    src_target, hits, skipped = {}, defaultdict(list), []
    for i, (src, dst) in enumerate(zip(srcs, dests)):
        if not dst:
            continue
        new = None
        for r in rules:
            if r['form'] not in dst:
                continue
            if r['pats'] and not any(p.search(src) for p in r['pats']):
                skipped.append((i, src, dst, r['raw']))
                continue
            new = dst.replace(r['form'], r['target'])
            hits[r['raw']].append(i)
            break
        if new is not None and new != dst:
            if src in src_target and src_target[src] != new:
                # 注意这不是「规则自相矛盾」：同一源句必然匹配同一条规则
                # （命中即 break），所以真正触发条件是**该源句现有两形**。
                # 机械替换会忠实地把这个分裂保留下来，那不是收敛。
                raise SystemExit(
                    f'error: 同一源句现有两形，机械替换会产出两个不同目标（这不是收敛）：\n'
                    f'  SRC {src!r}\n  目标A {src_target[src]!r}\n  目标B {new!r}\n'
                    f'  须先人工定这一族的主形，或把该源句排除在本次收敛之外。')
            src_target[src] = new

    print(f'命中行（按规则）:')
    for r in rules:
        print(f'  {r["raw"]}: {len(hits[r["raw"]])} 行')
    if skipped:
        print(f'\n形在但锚不匹配、按规则跳过: {len(skipped)} 行')
        for i, src, dst, raw in skipped[:6]:
            print(f'  [{i}] {raw}  {src[:56]}  ->  {dst[:56]}')
    print(f'\n受改源句: {len(src_target)}')

    # 2) 按批次拆表：每个含该源句行的批次都要拿到同一条映射
    per_batch = defaultdict(dict)
    rows_per_batch = defaultdict(list)
    for src, tgt in src_target.items():
        for i, s in enumerate(srcs):
            if s != src:
                continue
            bid = owner.get(i)
            if not bid:
                continue
            per_batch[bid][src] = tgt
            rows_per_batch[bid].append(i)

    total = 0
    for bid in sorted(per_batch):
        total += len(rows_per_batch[bid])
        print(f'  {bid}: {len(per_batch[bid])} 源句 / {len(rows_per_batch[bid])} 行')
    print(f'合计 {len(per_batch)} 批 / {total} 行')

    if not args.apply:
        print('\n(dry-run，未落盘。加 --apply 生成表文件)')
        return 0

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for bid, tbl in per_batch.items():
        p = out / f'table-{bid}.json'
        p.write_text(json.dumps(
            {'batch': bid, 'rationale': {}, 'table': tbl},
            ensure_ascii=False, indent=1), encoding='utf-8')
        back = json.loads(p.read_text(encoding='utf-8'))
        assert back['table'] == tbl, f'回读不一致: {p}'
    print(f'\n已落盘 {len(per_batch)} 张表 -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
