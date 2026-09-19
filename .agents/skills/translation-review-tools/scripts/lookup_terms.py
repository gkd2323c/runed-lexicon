# -*- coding: utf-8 -*-
"""lookup_terms.py — 在官方词典中查证一批英文术语的既有中文译名。

用途：子代理把原版既有名词标 MEDIUM / REVIEW 悬置时，由编排者批量查证后入词表
（见 skyrim-translation-craft §8「跨批一致性」）。

用法:
  py -3 .agents/skills/translation-review-tools/scripts/lookup_terms.py <英文词> [<英文词> ...]
  py -3 .agents/skills/translation-review-tools/scripts/lookup_terms.py Vyrthur "Ice Wraith" Dremora

可选参数:
  --max-len N     源文长度上限，超过的条目跳过（默认 170；长叙事句命中噪声大）
  --per-term N    每个词最多列出几条（默认 4）
  --context REGEX 只保留源文同时匹配该正则的命中（用于区分同形词，
                  例：查 Seeker 时限定 --context "Apocrypha|Mora|Black Book"）

输出：每个词的「源文 -> 译文」对照；无命中时显式标注（无官方见证），
便于区分「官方没这个词」与「脚本没查到」。

只读工具：不修改任何文件。
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DICTIONARY_DIR = PROJECT_ROOT / "dictionary"


def iter_dictionary_rows(dictionary_dir: Path):
    """遍历词典目录下所有 XML 的 (source, dest, 文件名)。"""
    for path in sorted(glob.glob(str(dictionary_dir / "**" / "*.xml"), recursive=True)):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        name = Path(path).name
        for node in root.findall(".//String"):
            src_node = node.find("Source")
            dst_node = node.find("Dest")
            src = (src_node.text or "") if src_node is not None else ""
            dst = (dst_node.text or "") if dst_node is not None else ""
            yield src.strip(), dst.strip(), name


def lookup(term: str, rows, max_len: int, per_term: int, context: str | None) -> list[tuple[str, str, str]]:
    term_re = re.compile(re.escape(term), re.I)
    ctx_re = re.compile(context, re.I) if context else None
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for src, dst, name in rows:
        if not dst or dst == src or len(src) > max_len:
            continue
        if not term_re.search(src):
            continue
        if ctx_re is not None and not ctx_re.search(src):
            continue
        key = src[:110]
        if key in seen:
            continue
        seen.add(key)
        out.append((src[:110].replace("\n", " "), dst[:110].replace("\n", " "), name))
        if len(out) >= per_term:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在官方词典中查证术语译名（只读）")
    ap.add_argument("terms", nargs="+", help="要查证的英文词，可多个")
    ap.add_argument("--dictionary-dir", default=str(DEFAULT_DICTIONARY_DIR), help="官方词典目录")
    ap.add_argument("--max-len", type=int, default=170, help="源文长度上限（默认 170）")
    ap.add_argument("--per-term", type=int, default=4, help="每个词列出几条（默认 4）")
    ap.add_argument("--context", default=None, help="源文须同时匹配的正则，用于区分同形词")
    args = ap.parse_args(argv)

    dictionary_dir = Path(args.dictionary_dir)
    if not dictionary_dir.is_dir():
        print(f"词典目录不存在: {dictionary_dir}", file=sys.stderr)
        return 2

    rows = list(iter_dictionary_rows(dictionary_dir))
    for term in args.terms:
        print("==", term)
        hits = lookup(term, rows, args.max_len, args.per_term, args.context)
        if not hits:
            print("    (无官方见证)")
            continue
        for src, dst, name in hits:
            print("   ", src, "->", dst)
            print("      [" + name + "]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
