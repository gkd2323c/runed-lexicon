# -*- coding: utf-8 -*-
"""dump_review_findings.py — 把一份 review-record 的 findings 拉成裁决视图。

每条 finding 打一行块：idx / severity / category / 源文 / 现译(canonical) /
proposed / rationale。用于主会话逐条裁决。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def load_xml_index(xml_path):
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


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', required=True)
    ap.add_argument('--xml', required=True)
    ap.add_argument('--only', default=None, help='只显示这些逗号分隔的 idx')
    ap.add_argument('--full', action='store_true', help='连 not_reported / risk_notes 一起打')
    args = ap.parse_args()

    rec = json.load(open(args.report, encoding='utf-8-sig'))
    xml = load_xml_index(args.xml)
    keep = set(int(x) for x in args.only.split(',')) if args.only else None

    print(f"=== {rec.get('batch')}  status={rec.get('status')}  "
          f"summary={json.dumps(rec.get('findings_summary', {}), ensure_ascii=False)}")

    f = rec.get('findings') or []
    print(f"--- findings: {len(f)} ---")
    for n, item in enumerate(f, 1):
        idx = item.get('xml_index')
        if keep is not None and idx not in keep:
            continue
        x = xml.get(idx, {})
        print(f"\n[{n}] idx={idx} sev={item.get('severity')} cat={item.get('category')}")
        print(f"    EDID : {x.get('edid','')}  REC={x.get('rec','')}")
        print(f"    SRC  : {x.get('source','')}")
        print(f"    NOW  : {x.get('dest','')}")
        print(f"    PROP : {item.get('proposed')}")
        if item.get('confidence'):
            print(f"    CONF : {item.get('confidence')}")
        rat = item.get('rationale') or ''
        print(f"    WHY  : {rat}")

    if args.full:
        for key in ('not_reported_as_defects', 'line_risk_notes'):
            arr = rec.get(key) or []
            print(f"\n--- {key}: {len(arr)} ---")
            for item in arr:
                print(f"  * {json.dumps(item, ensure_ascii=False)}")
        print(f"\n--- validation ---")
        print(json.dumps(rec.get('validation', {}), ensure_ascii=False, indent=1)[:3000])


if __name__ == '__main__':
    main()
