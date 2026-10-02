# -*- coding: utf-8 -*-
r"""合并多份 {batch: {idx: fix}} 修正集，并按 canonical 现值重同步。

make_fixes_from_report.py 的 --report 是**单数**参数，多批收口要分别生成再合并。
合并后必须按 canonical 复核 expected_current：
  · 现值已等于目标值 -> 剔除（这一轮已经落地过，重复收口会撞 CAS）
  · 现值与 expected_current 不符 -> 报漂移并中止（说明盘面在审查之后又变过，
    拿旧结论去改新行会改错东西）

用法：
  py -3 merge_and_resync.py --stem <STEM> --out <fixes.json> <fix-map.json> [<fix-map.json> ...]
  py -3 merge_and_resync.py --stem <STEM> fixes-a fixes-b        # 也接受 .work/<stem>/batches/ 下的裸名
"""
import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def resolve(stem, name):
    p = Path(name)
    if p.is_absolute() or p.exists():
        return p
    cand = Path('.work') / stem / 'batches' / (name if name.endswith('.json') else name + '.json')
    if cand.is_file():
        return cand
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('fix_maps', nargs='+')
    ap.add_argument('--out', default=None)
    ap.add_argument('--no-resync', action='store_true')
    a = ap.parse_args()

    base = Path('mods') / (a.stem + '.esp')
    canon = base / (a.stem + '_english_chinese_translated.xml')
    out = Path(a.out) if a.out else Path('.work') / a.stem / 'batches' / 'close-round-fixes.json'

    merged = defaultdict(dict)
    for name in a.fix_maps:
        p = resolve(a.stem, name)
        if not p.is_file():
            print('!! 找不到修正集：%s' % p)
            return 1
        d = json.loads(p.read_text(encoding='utf-8'))
        n = 0
        for b, items in d.items():
            for idx, fix in items.items():
                if idx in merged[b] and merged[b][idx].get('new') != fix.get('new'):
                    print('!! 冲突 %s idx=%s：%r vs %r'
                          % (b, idx, merged[b][idx].get('new', '')[:30], fix.get('new', '')[:30]))
                    return 1
                merged[b][idx] = fix
                n += 1
        print('  %s: %d 条' % (p.name, n))

    resynced = dropped = 0
    if not a.no_resync and canon.is_file():
        rows = list(ET.parse(canon).getroot().iter('String'))
        for b, items in merged.items():
            for idx in list(items):
                fix = items[idx]
                if 'new' not in fix:
                    continue
                i = int(idx)
                cur = (rows[i].findtext('Dest') or '') if 0 <= i < len(rows) else None
                if cur is None:
                    print('!! idx 越界 %s/%s' % (b, idx))
                    return 1
                if cur == fix['new']:
                    del items[idx]
                    dropped += 1
                    continue
                exp = fix.get('expected_current')
                if exp and cur != exp:
                    print('!! %s idx=%s 漂移：expected_current=%r 实际=%r'
                          % (b, idx, exp[:40], cur[:40]))
                    return 1
                fix['expected_current'] = cur
                resynced += 1

    total = sum(len(v) for v in merged.values())
    with io.open(out, 'w', encoding='utf-8', newline='') as f:
        json.dump(dict(merged), f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert json.loads(out.read_text(encoding='utf-8')) == dict(merged), '读回不一致'
    print('合并 -> %s  批次 %d / 修正 %d（重同步 %d，剔除已就位 %d）'
          % (out, len(merged), total, resynced, dropped))
    for b in sorted(merged, key=lambda x: (len(x), x)):
        if merged[b]:
            print('   %-14s %d' % (b, len(merged[b])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
