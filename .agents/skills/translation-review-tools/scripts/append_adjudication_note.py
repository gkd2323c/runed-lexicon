# -*- coding: utf-8 -*-
"""append_adjudication_note.py — 定点追加 notes / 刷新 verification 到总档。

总档（review-adjudication.json）是跨轮次的裁决记忆，必须整份读改写、不能手工 Edit
（几十万字符）。本脚本只做三件事，不碰 decisions / close_rounds：
  1. 追加 notes（去重：同 topic 已存在则替换，不重复堆）
  2. 刷新 canonical_at_adjudication
  3. 刷新 totals.verification
写盘后回读校验。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--note-topic', required=True)
    ap.add_argument('--note-body', required=True, help='正文，可用 \\n 换行')
    ap.add_argument('--verification', default=None)
    args = ap.parse_args()

    root = Path('.work') / args.stem
    path = root / 'reports' / f'{args.stem}-review-adjudication.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))

    body = args.note_body.replace('\\n', '\n')
    notes = data.setdefault('notes', [])
    replaced = False
    for n in notes:
        if isinstance(n, dict) and n.get('topic') == args.note_topic:
            n['body'] = body
            replaced = True
            break
    if not replaced:
        notes.append({'topic': args.note_topic, 'body': body})

    xml = Path('mods') / f'{args.stem}.esp' / f'{args.stem}_english_chinese_translated.xml'
    sha = hashlib.sha256(xml.read_bytes()).hexdigest()[:8]
    data['canonical_at_adjudication'] = sha
    if args.verification:
        data.setdefault('totals', {})['verification'] = args.verification.replace('\\n', '\n')

    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    back = json.loads(path.read_text(encoding='utf-8'))
    assert back['canonical_at_adjudication'] == sha, '回读 canonical 不一致'
    assert any(isinstance(n, dict) and n.get('topic') == args.note_topic
               for n in back['notes']), '回读 note 缺失'
    print(f'note={"替换" if replaced else "追加"}  notes={len(back["notes"])}  '
          f'canonical={sha}  chars={len(path.read_text(encoding="utf-8"))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
