#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次覆盖率核查：对批次计划与批次目录做全量状态扫描，输出缺口清单。

状态定义（逐批次）：
- VERIFIED  ：translation.json 已填充（≥1 条 TRANSLATED 译文）——已验收（或已写回）
- TRANSLATED：map.json 存在但 translation.json 未填充——子代理已交卷，待主会话消费
- PREPPED   ：context.json / translation.json 存在但无产出——已备料待翻译
- MISSING   ：批次目录不存在或无任何工作文件——未备料

用途：声明批次范围完成前核对（防漏批）；每轮收口时定位下一批候选。

用法：
  py -3 check_batch_coverage.py --plan .work/<plugin>/context/<plugin>-info-batches.json \
      --batches-dir .work/<plugin>/batches [--json]
"""
import argparse
import json
import os
import sys
from collections import Counter


def classify(batch, batches_dir, canonical=None):
    bid = batch['id']
    d = os.path.join(batches_dir, bid)
    has_dir = os.path.isdir(d)
    has_tr = os.path.isfile(os.path.join(d, 'translation.json'))
    has_map = os.path.isfile(os.path.join(d, 'map.json'))
    has_ctx = os.path.isfile(os.path.join(d, 'context.json'))
    map_path = os.path.join(d, 'map.json')
    map_mtime = os.path.getmtime(map_path) if has_map else None
    total = len(batch.get('idx') or [])
    filled = 0
    entries = []
    if has_tr:
        try:
            with open(os.path.join(d, 'translation.json'), 'r', encoding='utf-8') as f:
                res = json.load(f)
            entries = res.get('translations', [])
            filled = sum(
                1 for t in entries
                if t.get('translation') and t.get('status') == 'TRANSLATED'
            )
        except Exception:
            filled = -1
    # "filled" means the batch was translated and verified; it does NOT mean the
    # rows reached the canonical XML. Cross-check when a canonical map is given,
    # otherwise a batch that passed verification but was never written back looks
    # finished forever.
    # KEEP rows legitimately stay equal to Source and are not counted.
    unwritten = None
    unwritten_idx = []
    if canonical is not None and entries:
        unwritten = 0
        for t in entries:
            if not t.get('translation') or t.get('status') != 'TRANSLATED':
                continue
            i = t.get('xml_index')
            pair = canonical.get(i)
            if pair is not None and pair[0] == pair[1]:
                unwritten += 1
                if len(unwritten_idx) < 12:
                    unwritten_idx.append(i)
    if filled > 0:
        state = 'VERIFIED'
    elif has_map:
        state = 'TRANSLATED'
    elif has_tr or has_ctx:
        state = 'PREPPED'
    else:
        state = 'MISSING'
    return {
        'id': bid,
        'line': batch.get('line'),
        'state': state,
        'filled': filled,
        'total': total,
        'unwritten': unwritten,
        'unwritten_idx': unwritten_idx,
        'map_mtime': map_mtime,
    }


def load_canonical_map(path):
    """Map xml_index -> (Source, Dest) from the canonical XML."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    out = {}
    for i, node in enumerate(root.iter('String')):
        src = node.findtext('Source') or ''
        dst = node.findtext('Dest') or ''
        out[i] = (src, dst)
    return out


def main():
    ap = argparse.ArgumentParser(description='批次覆盖率核查')
    ap.add_argument('--plan', required=True, help='批次计划 JSON（plan-info-batches.py 产物）')
    ap.add_argument('--batches-dir', required=True, help='批次目录（.work/<plugin>/batches）')
    ap.add_argument('--xml', help='canonical 译文 XML；给出后额外核算“已验收但未写回”的行')
    ap.add_argument('--json', action='store_true', help='输出 JSON 而非文本')
    args = ap.parse_args()

    with open(args.plan, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    canonical = load_canonical_map(args.xml) if args.xml else None
    rows = [classify(b, args.batches_dir, canonical) for b in plan['batches']]
    cnt = Counter(r['state'] for r in rows)
    unwritten_rows = [r for r in rows if (r['unwritten'] or 0) > 0]
    out = {
        'total_batches': len(rows),
        'summary': {k: cnt.get(k, 0) for k in ('VERIFIED', 'TRANSLATED', 'PREPPED', 'MISSING')},
        'non_verified': [r for r in rows if r['state'] != 'VERIFIED'],
        'unwritten_batches': len(unwritten_rows),
        'unwritten_rows': sum(r['unwritten'] or 0 for r in unwritten_rows),
        'unwritten': [
            {'id': r['id'], 'unwritten': r['unwritten'], 'sample_idx': r['unwritten_idx']}
            for r in sorted(unwritten_rows, key=lambda x: -(x['unwritten'] or 0))
        ],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        s = out['summary']
        print(f"batches={out['total_batches']} VERIFIED={s['VERIFIED']} "
              f"TRANSLATED={s['TRANSLATED']} PREPPED={s['PREPPED']} MISSING={s['MISSING']}")
        if canonical is not None:
            print(f"unwritten: {out['unwritten_batches']} 批 / {out['unwritten_rows']} 行"
                  f"（已填充译文但 canonical 仍与 Source 相同）")
        print()
        for r in out['unwritten']:
            print(f"!! {r['id']} | 未写回 {r['unwritten']} 行 | 例 {r['sample_idx']}")
        for r in out['non_verified']:
            print(f"{r['id']} | {r['state']:10s} | filled={r['filled']}/{r['total']} | {r['line']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
