# -*- coding: utf-8 -*-
r"""按分片索引切分 readout.txt，生成 readout-part-*.txt。

shard_batch.py split 只切 index，不切 readout；而派单卡要求主读本就是分片文件，
所以这一步必须单独做，否则子代理会去读整批 readout、看到不属于自己那片的内容。

用法：py -3 shard_readout.py --stem <STEM> --batch <BATCH-ID> --parts a b
"""
import argparse
import re
import sys
from pathlib import Path

LINE = re.compile(r'^\[(\d+)\]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--batch', required=True)
    ap.add_argument('--parts', nargs='+', required=True)
    a = ap.parse_args()
    bdir = Path('.work') / a.stem / 'batches' / a.batch

    rd = bdir / 'readout.txt'
    if not rd.is_file():
        print('!! 缺读本：%s（先跑 read_batch.py）' % rd)
        return 1
    lines = rd.read_text(encoding='utf-8').splitlines()
    head = [l for l in lines if not LINE.match(l.strip())]
    body = [l for l in lines if LINE.match(l.strip())]
    by_idx = {LINE.match(l.strip()).group(1): l for l in body}

    total = 0
    for part in a.parts:
        idxf = bdir / ('index-part-%s.txt' % part)
        if not idxf.is_file():
            print('!! 缺分片索引：%s（先跑 shard_batch.py split）' % idxf)
            return 1
        keys = [x for x in idxf.read_text(encoding='utf-8').split() if x.strip()]
        missing = [k for k in keys if k not in by_idx]
        if missing:
            print('!! 分片 %s 有 %d 个 idx 不在 readout.txt：%s' % (part, len(missing), missing[:10]))
            return 1
        out = [by_idx[k] for k in keys]
        p = bdir / ('readout-part-%s.txt' % part)
        p.write_text('\n'.join(out) + '\n', encoding='utf-8')
        total += len(out)
        print('part %s: %d 行, %d B -> %s' % (part, len(out), p.stat().st_size, p.name))
    if total != len(body):
        print('!! 分片合计 %d 行 != readout.txt %d 行' % (total, len(body)))
        return 1
    print('合计 %d 行，与 readout.txt 对齐（表头 %d 行已省略）' % (total, len(head)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
