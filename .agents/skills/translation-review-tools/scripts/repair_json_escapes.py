# -*- coding: utf-8 -*-
r"""repair_json_escapes.py — 修 review-record 里「转义只写了一半」造成的非法 JSON。

LLM 写 JSON 的高频坏点：正则片段里的反斜杠只转义了一半，例如
    "checks_run": ["正则 ^\\[\\d+\\] 命中 46"]
前两处 `\\` 合法，末尾 `\]` 只有**一个**反斜杠——JSON 里 `\]` 是非法转义，
`json.loads` 直接抛 `Invalid \escape`，整份审查记录作废（9 条 findings 白写）。

本脚本逐字符扫描（不是正则替换——正则会把 `\\` 里的第二个反斜杠误判成非法），
只把「后面不跟合法转义字符」的反斜杠补成 `\\`，其余字节一律不动。
修完必须能 `json.loads`，并回读断言。

用法：
  py -3 repair_json_escapes.py --path <record.json> [--dry-run]

只读输入 + 显式 --path 写回；不碰其他文件。原地覆盖同名文件。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# JSON 字符串内合法的转义起始字符（\" \\ \/ \b \f \n \r \t \uXXXX）
_VALID = set('"\\/bfnrtu')


def repair(text: str) -> tuple[str, int]:
    out = []
    i = 0
    fixed = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\\':
            nxt = text[i + 1] if i + 1 < n else ''
            if nxt in _VALID or nxt == 'u':
                out.append(text[i:i + 2])
                i += 2
                continue
            # 非法转义：把这个反斜杠自己转义掉
            out.append('\\\\')
            fixed += 1
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out), fixed


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    p = Path(args.path)
    raw = p.read_text(encoding='utf-8-sig')
    try:
        json.loads(raw)
        print('本来就是合法 JSON，无需修复')
        return 0
    except json.JSONDecodeError as e:
        print(f'原文件解析失败: {e}')

    fixed, n = repair(raw)
    data = json.loads(fixed)  # 修完仍必须能解析，否则直接抛、不落盘
    print(f'修复非法转义 {n} 处；修复后解析通过，batch={data.get("batch")!r} '
          f'findings={len(data.get("findings") or [])}')
    if args.dry_run:
        print('(dry-run，未写盘)')
        return 0
    p.write_text(fixed, encoding='utf-8')
    back = json.loads(p.read_text(encoding='utf-8'))
    assert back == data, '回读内容不一致'
    print('已写盘并回读校验通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
