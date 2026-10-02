# -*- coding: utf-8 -*-
"""按裁决表为已写回批次生成 close_round 修正集（每条带 expected_current 以便 CAS 复核）。

用法：py -3 make_close_fixes.py --stem <STEM> --batch <B> --table <table.json> [--out <fixes.json>]
只收「canonical 现值 != 裁决表」的源句；已一致的自动跳过。
字面中文直写，不手算 \\uXXXX。
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
    ap.add_argument('--batch', required=True)
    ap.add_argument('--table', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    spec = json.loads(Path(a.table).read_text(encoding='utf-8'))
    T, R = spec['table'], spec.get('rationale') or {}
    base = Path('mods') / (a.stem + '.esp')
    srcs, dests = [], []
    for s in ET.parse(base / (a.stem + '_english_chinese_translated.xml')).getroot().iter('String'):
        srcs.append((s.findtext('Source') or '').strip())
        dests.append((s.findtext('Dest') or '').strip())

    bdir = Path('.work') / a.stem / 'batches' / a.batch
    idx = [int(x) for x in (bdir / 'index.txt').read_text(encoding='utf-8').split() if x.strip()]
    bysrc = defaultdict(list)
    for i in idx:
        bysrc[srcs[i]].append(i)

    fixes, same, skipped = {}, 0, []
    for s, tgt in T.items():
        if s not in bysrc:
            skipped.append(s)
            continue
        for i in bysrc[s]:
            cur = dests[i]
            if not cur or cur == tgt:
                same += 1
                continue
            fixes.setdefault(a.batch, {})[str(i)] = {
                'new': tgt, 'expected_current': cur,
                'notes': (R.get(s) or '')[:300],
            }
    if skipped:
        print('!! 表里 %d 条源句不在本批，已跳过：%s' % (len(skipped), skipped[:3]))
    out = Path(a.out) if a.out else Path('.work') / a.stem / 'batches' / 'close-round-fixes.json'
    with io.open(out, 'w', encoding='utf-8', newline='') as f:
        json.dump(fixes, f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert json.loads(out.read_text(encoding='utf-8')) == fixes, '读回不一致'
    n = sum(len(v) for v in fixes.values())
    print('已一致 %d 行；fix map -> %s（%d 行）' % (same, out, n))
    for b in fixes:
        for idx, fx in sorted(fixes[b].items(), key=lambda kv: int(kv[0])):
            print('  %s/%s' % (b, idx))
            print('     旧 %s' % fx['expected_current'])
            print('     新 %s' % fx['new'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
