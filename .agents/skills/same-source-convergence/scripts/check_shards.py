# -*- coding: utf-8 -*-
r"""分片批次合并与交卷门槛自检：{批次}/map-part-*.json -> map.json

派单卡要求主读本就是分片文件（readout-part-*.txt），所以大批次走分片；
分片两片看不到对方的 readout，同一句重复出现时很容易各起一形。
本脚本在合并前把八条交卷门槛逐条打出来，让问题在合并前就暴露。

门槛：键集与分片索引严格相等、无空译文、无 PENDING/REVIEW 残留、
confidence 是字符串档位、无半角双引号/直角引号/U+FFFD。任一不过则不落盘。

用法：py -3 check_shards.py --stem <STEM> --batch <BATCH-ID> --parts a b [--merge]
"""
import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

BAD_CHARS = re.compile(r'[\u300c\u300d\u300e\u300f\ufffd"]')
OK_STATUS = ('TRANSLATED', 'KEEP')


def load(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)


def check_part(bdir, part, idx_keys):
    p = bdir / ('map-part-%s.json' % part)
    if not p.is_file():
        print('!! 缺分片产物：%s' % p)
        return {}, set(), False
    m = load(p)
    ks = set(int(k) for k in m)
    st = Counter((v or {}).get('status') for v in m.values())
    empty = [k for k, v in m.items() if not str((v or {}).get('translation') or '').strip()]
    pend = [k for k, v in m.items() if (v or {}).get('status') not in OK_STATUS]
    conf = [k for k, v in m.items() if not isinstance((v or {}).get('confidence'), str)]
    bad = [k for k, v in m.items() if BAD_CHARS.search(str((v or {}).get('translation') or ''))]
    print('part %s: idx=%d map=%d 缺=%s 多=%s 空=%s PENDING=%s conf异常=%s 字符集=%s %s'
          % (part, len(idx_keys), len(m), sorted(idx_keys - ks), sorted(ks - idx_keys),
             empty, pend, conf, bad, dict(st)))
    good = not (idx_keys - ks) and not (ks - idx_keys) and not empty and not pend \
        and not conf and not bad
    return m, ks, good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--batch', required=True)
    ap.add_argument('--parts', nargs='+', required=True)
    ap.add_argument('--merge', action='store_true')
    a = ap.parse_args()
    bdir = Path('.work') / a.stem / 'batches' / a.batch

    merged, ok = {}, True
    for part in a.parts:
        idxf = bdir / ('index-part-%s.txt' % part)
        if not idxf.is_file():
            print('!! 缺分片索引：%s' % idxf)
            return 1
        idx_keys = {int(x) for x in idxf.read_text(encoding='utf-8').split() if x.strip()}
        m, ks, good = check_part(bdir, part, idx_keys)
        ok = ok and good
        for k, v in m.items():
            if k in merged:
                print('!! 重复 key %s' % k)
                return 1
            merged[k] = v

    idxf = bdir / 'index.txt'
    if not idxf.is_file():
        print('!! 缺整批索引：%s' % idxf)
        return 1
    allidx = {int(x) for x in idxf.read_text(encoding='utf-8').split() if x.strip()}
    got = {int(k) for k in merged}
    if got != allidx:
        print('!! 分片合集 %d != index.txt %d，缺 %s 多 %s'
              % (len(merged), len(allidx), sorted(allidx - got), sorted(got - allidx)))
        ok = False
    if not ok:
        print('门槛未过，不合并')
        return 1
    print('merged %d keys -> map.json' % len(merged))
    if not a.merge:
        return 0
    out = bdir / 'map.json'
    with io.open(out, 'w', encoding='utf-8', newline='') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert load(out) == merged, '读回不一致'
    print('OK（已落盘并读回校验）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
