# -*- coding: utf-8 -*-
"""check_review_proposed.py — 核 review-record 的 proposed 是否真的给出了改后译文。

事故锚定：INFO-574 的审查记录 7 条 finding 的 `proposed` **全部等于 canonical 现译**——
子代理把改法写进了 `rationale` 散文（「改动仅一处词」「改后既顺搭配又落回主形」），
`proposed` 字段却原样留成现译。这类记录喂给 `make_fixes_from_report.py` 是**静默空转**：
不报错、不改字节、看起来收口成功，实际一条缺陷都没修。`prune_close_patch` 事后
只会把它们报成「已就位」，掩盖掉「本该改却没改」。

本脚本在收口前把这类 finding 全部揪出来，并区分三种不可用形态：
  EMPTY         proposed 为空
  SAME_AS_NOW   proposed 与 canonical 现译逐字相同（散文里有改法）
  NOT_IN_BATCH  proposed 根本没给译文

用法：
  py -3 check_review_proposed.py --xml <canonical.xml> --reports <rec1.json> [rec2.json ...]
  py -3 check_review_proposed.py --xml <canonical.xml> --reports <rec.json> --allow-same 2

退出码：有不可用 finding 且未给 --allow-same 时为 1（阻断收口）。
只读输入；不写盘。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

UNUSABLE = ('EMPTY', 'SAME_AS_NOW', 'NOT_IN_BATCH')


def load_xml_index(xml_path):
    """idx(0-based String 序号) -> {rec, edid, source, dest}。"""
    root = ET.parse(xml_path).getroot()
    out = {}
    for i, s in enumerate(root.findall('.//String')):
        out[i] = {
            'rec': s.findtext('REC') or '',
            'edid': s.findtext('EDID') or '',
            'source': s.findtext('Source') or '',
            'dest': s.findtext('Dest') or '',
        }
    return out


def classify(item, xml):
    """返回 (kind, now)；kind 为 None 表示这条 proposed 可用。"""
    idx = item.get('xml_index')
    if not isinstance(idx, int):
        return 'NOT_IN_BATCH', ''
    prop = item.get('proposed')
    if prop is None or not isinstance(prop, str) or not prop.strip():
        return 'EMPTY', (xml.get(idx, {}).get('dest') or '')
    now = (xml.get(idx, {}).get('dest') or '').strip()
    if prop.strip() == now:
        return 'SAME_AS_NOW', now
    return None, now


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='核 review-record 的 proposed 可用性')
    ap.add_argument('--xml', required=True, help='canonical translated XML')
    ap.add_argument('--reports', nargs='+', required=True, help='review-record JSON，可多份')
    ap.add_argument('--allow-same', action='store_true',
                    help='容忍 SAME_AS_NOW（子代理本意就是「此处无需改动」时用）')
    args = ap.parse_args()

    xml = load_xml_index(args.xml)
    total = 0
    bad = []
    for rp in args.reports:
        rec = json.load(open(rp, encoding='utf-8-sig'))
        batch = rec.get('batch') or Path(rp).stem
        for n, item in enumerate(rec.get('findings') or [], 1):
            total += 1
            kind, now = classify(item, xml)
            if kind and (kind != 'SAME_AS_NOW' or not args.allow_same):
                bad.append((batch, n, item.get('xml_index'), kind, now))

    print(f'findings={total}  proposed-unusable={len(bad)}')
    for b, n, idx, kind, now in bad:
        print(f'  {b} #{n} idx={idx} {kind}  now={now[:70]}')
    if not bad:
        print('proposed 全部为可直接落盘的纯译文，可进 make_fixes_from_report.py。')
        return 0
    print('\n=> 这些 finding 不能直接进 make_fixes_from_report.py：'
          '\n   SAME_AS_NOW 说明改法只写在 rationale 散文里，字段留了现译。'
          '\n   处置：主会话按 rationale 自己重写 proposed（逐条回源文判读），'
          '\n   或确认该条本无缺陷、从 findings 里剔除。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
