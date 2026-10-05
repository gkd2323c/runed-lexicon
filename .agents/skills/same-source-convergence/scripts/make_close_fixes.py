# -*- coding: utf-8 -*-
"""按裁决表为已写回批次生成 close_round 修正集（每条带 expected_current 以便 CAS 复核）。

用法：py -3 make_close_fixes.py --stem <STEM> --batch <B> [<B2> ...] --table <table.json> [--out <fixes.json>]
只收「canonical 现值 != 裁决表」的源句；已一致的自动跳过。
字面中文直写，不手算 \\uXXXX。

为什么 --batch 收多个：
  一次跨批裁决会同时落在若干已写回批上，而输出只写一个 fixes 文件。
  收单个 --batch 时，逐批调用会用同一个默认输出路径**互相覆盖**，
  最后只剩最后一批的修正，前面几批要人肉合并——这一步没有任何判断，纯搬运，
  却是最容易出错的一步（漏批、错 idx）。收多个之后一次调用直接产出合并好的全量 fixes。
"""

import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--batch', required=True, nargs='+',
                    help='一个或多个批次；表内 batches 可覆盖')
    ap.add_argument('--table', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    spec = json.loads(Path(a.table).read_text(encoding='utf-8'))
    batches = list(spec.get('batches') or []) or list(a.batch)
    T, R = spec['table'], spec.get('rationale') or {}
    base = Path('mods') / (a.stem + '.esp')
    srcs, dests = [], []
    for s in ET.parse(base / (a.stem + '_english_chinese_translated.xml')).getroot().iter('String'):
        srcs.append((s.findtext('Source') or '').strip())
        dests.append((s.findtext('Dest') or '').strip())

    fixes, same, touched, hit_src = {}, 0, defaultdict(int), set()
    for batch in batches:
        bdir = Path('.work') / a.stem / 'batches' / batch
        idx = [int(x) for x in (bdir / 'index.txt').read_text(encoding='utf-8').split() if x.strip()]
        bysrc = defaultdict(list)
        for i in idx:
            bysrc[srcs[i]].append(i)
        for s, tgt in T.items():
            if s not in bysrc:
                continue
            hit_src.add(s)
            for i in bysrc[s]:
                cur = dests[i]
                if not cur or cur == tgt:
                    same += 1
                    continue
                fixes.setdefault(batch, {})[str(i)] = {
                    'new': tgt, 'expected_current': cur,
                    'notes': (R.get(s) or '')[:300],
                }
                touched[batch] += 1

    unused = [s for s in T if s not in hit_src]
    if unused:
        print('!! 表里 %d 条源句在这 %d 个批次里都不存在：%s'
              % (len(unused), len(batches), unused[:3]))

    out = Path(a.out) if a.out else Path('.work') / a.stem / 'batches' / 'close-round-fixes.json'
    with io.open(out, 'w', encoding='utf-8', newline='') as f:
        json.dump(fixes, f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert json.loads(out.read_text(encoding='utf-8')) == fixes, '读回不一致'
    n = sum(len(v) for v in fixes.values())
    print('已一致 %d 行；fix map -> %s（%d 行 / %d 个批次）'
          % (same, out, n, len(fixes)))
    for b in sorted(fixes):
        for idx, fx in sorted(fixes[b].items(), key=lambda kv: int(kv[0])):
            print('  %s/%s' % (b, idx))
            print('     旧 %s' % fx['expected_current'])
            print('     新 %s' % fx['new'])
    print('\n下一步：close_round.py --batches %s --fixes %s ...'
          % (' '.join(sorted(fixes)), out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
