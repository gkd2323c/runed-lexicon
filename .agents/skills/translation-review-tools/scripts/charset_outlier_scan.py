# -*- coding: utf-8 -*-
"""charset_outlier_scan.py — 扫描译文中超出合法字集合的汉字（生僻字/生造字/形近错字候选）。

背景：CHAR001（normalize_charset.py）只处理「zh-cn 转换表能转换的字符」（繁体、
标点、台港用词），对表里没有的生僻字与形近错字零命中——形近错字（如「讱讽」
「蚖龙」）恰好都是常用字集外的生僻字，是重译事故的主要形态之一。

判据是数据驱动的闭集，不硬编码词表：
  合法字集 = GB2312 一级+二级（6763 现代通用汉字）
           ∪ 官方词典语料（dictionary/*.xml 的全部汉字，权威译文用字）
任何超出该并集的汉字即候选，逐个报出上下文供人工判定。

验证（ESX1 库）：合法词（瞭/祇/啰/窸/窣/蚺/嗫/嚅）全部在官方语料内；
真错字（讱/蚖/刭/捿/蝑/睖/霤/敉/眙/瞱/喍/呹）全部在集外，零误杀。

模式：
  --xml <canonical.xml>        只读全库扫描（0-based String 序号）
  --result <translation.json>  批次结果 JSON（可重复）
  --dictionary <dir>           官方词典目录（默认 dictionary/，可重复）
  --json <path>                候选写入 JSON
  --limit N                    最多列出 N 条（0=全部）

退出码：0 = 无候选；1 = 有候选（需人工判定）；2 = 用法/读入错误。

安全边界：只读，不修改任何输入文件。
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def is_gb2312(ch: str) -> bool:
    try:
        ch.encode('gb2312')
        return True
    except UnicodeEncodeError:
        return False


def is_cjk(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff'


def build_legal_set(dict_dirs: list[Path]) -> set[str]:
    """合法字集 = GB2312 ∪ 官方语料用字。"""
    legal: set[str] = set()
    # GB2312 全集（一级+二级）
    for hi in range(0xA1, 0xFA):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode('gb2312')
            except UnicodeDecodeError:
                continue
            legal.add(ch)
    # 官方语料
    for d in dict_dirs:
        if not d.is_dir():
            continue
        for f in d.glob('*.xml'):
            try:
                text = f.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            for ch in text:
                if is_cjk(ch):
                    legal.add(ch)
    return legal


def scan_text(idx, dest: str, legal: set[str]) -> list[tuple]:
    hits = []
    for pos, ch in enumerate(dest):
        if not is_cjk(ch) or ch in legal:
            continue
        ctx = dest[max(0, pos - 12):pos + 12]
        hits.append((idx, ch, ctx))
    return hits


def scan_xml(path: Path, legal: set[str]) -> list[tuple]:
    tree = ET.parse(str(path))
    hits = []
    for i, node in enumerate(tree.getroot().findall('.//String')):
        dest_node = node.find('Dest')
        dst = dest_node.text if dest_node is not None and dest_node.text else ''
        src_node = node.find('Source')
        src = src_node.text if src_node is not None and src_node.text else ''
        if not dst.strip() or dst == src:
            continue
        hits += scan_text(i, dst, legal)
    return hits


def scan_result(path: Path, legal: set[str]) -> list[tuple]:
    data = json.loads(path.read_text(encoding='utf-8'))
    hits = []
    for u in data.get('translations') or []:
        idx = u.get('xml_index')
        dst = u.get('translation') or ''
        hits += scan_text(idx, dst, legal)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--xml', default=None, help='canonical xTranslator XML（只读全库扫描）')
    ap.add_argument('--result', action='append', default=[], help='translation-result JSON（可重复）')
    ap.add_argument('--dictionary', action='append', default=[], help='官方词典目录（默认 dictionary/）')
    ap.add_argument('--json', default=None, help='候选写入 JSON')
    ap.add_argument('--limit', type=int, default=0, help='最多列出 N 条（0=全部）')
    args = ap.parse_args()
    if not args.xml and not args.result:
        ap.error('provide --xml and/or --result')

    dict_dirs = [Path(d) for d in args.dictionary] if args.dictionary else [Path('dictionary')]
    legal = build_legal_set(dict_dirs)
    print(f'合法字集: {len(legal)} 字（GB2312 ∪ {len(dict_dirs)} 个语料目录）', file=sys.stderr)

    all_hits = []
    if args.xml:
        p = Path(args.xml)
        if not p.is_file():
            print(f'error: not found: {p}', file=sys.stderr)
            return 2
        hits = scan_xml(p, legal)
        print(f'== {p}')
        for idx, ch, ctx in hits[: (args.limit or None)]:
            print(f'   [xml-index:{idx}] {ch!r} … {ctx}')
        print(f'   候选 {len(hits)} 处')
        all_hits += hits
    for r in args.result:
        p = Path(r)
        if not p.is_file():
            print(f'error: not found: {p}', file=sys.stderr)
            return 2
        hits = scan_result(p, legal)
        print(f'== {p}')
        for idx, ch, ctx in hits[: (args.limit or None)]:
            print(f'   [{idx}] {ch!r} … {ctx}')
        print(f'   候选 {len(hits)} 处')
        all_hits += hits

    if args.json:
        payload = [
            {'idx': idx, 'char': ch, 'context': ctx}
            for idx, ch, ctx in all_hits
        ]
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'json: {len(payload)} 条 -> {args.json}')

    print(f'总计：候选 {len(all_hits)} 处')
    return 1 if all_hits else 0


if __name__ == '__main__':
    raise SystemExit(main())
