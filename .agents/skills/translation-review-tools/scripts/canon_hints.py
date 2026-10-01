#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Emit already-translated canonical forms for one batch's sources.

Reads a batch's index.txt, compares the source strings against the current
canonical translated XML, and writes a Markdown digest of sentences and
clauses that have established canonical translations.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def split_en(s: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", s.strip())
    return [p for p in parts if p]


def split_zh(s: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])", s.strip())
    return [p for p in parts if p.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", help="MOD stem name (e.g. TheKalpicAnomaly_GLENMORIL)")
    parser.add_argument("--batch", help="Batch ID (e.g. INFO-288)")
    parser.add_argument("--index", type=Path, help="Path to batch index.txt")
    parser.add_argument("--source-xml", type=Path, help="Path to source XML")
    parser.add_argument("--canonical", type=Path, help="Path to translated canonical XML")
    parser.add_argument("--out", type=Path, help="Output markdown path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path.cwd()

    if args.stem and args.batch:
        stem = args.stem
        bid = args.batch
        idxp = args.index or (root / ".work" / stem / "batches" / bid / "index.txt")
        src_xml = args.source_xml or (root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese.xml")
        if not src_xml.exists():
            src_xml = root / "mods" / stem / f"{stem}_english_chinese.xml"
        canon_xml = args.canonical or (root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml")
        if not canon_xml.exists():
            canon_xml = root / "mods" / stem / f"{stem}_english_chinese_translated.xml"
        outp = args.out or (root / ".work" / stem / "batches" / bid / "canon-hints.md")
    elif args.index and args.source_xml and args.canonical and args.out:
        bid = args.batch or args.index.parent.name
        idxp = args.index
        src_xml = args.source_xml
        canon_xml = args.canonical
        outp = args.out
    else:
        print("error: must provide either (--stem and --batch) or (--index, --source-xml, --canonical, --out)", file=sys.stderr)
        return 2

    if not idxp.exists():
        print(f"error: index file not found: {idxp}", file=sys.stderr)
        return 1
    if not src_xml.exists():
        print(f"error: source xml not found: {src_xml}", file=sys.stderr)
        return 1
    if not canon_xml.exists():
        print(f"error: canonical xml not found: {canon_xml}", file=sys.stderr)
        return 1

    idxs = [int(x.strip()) for x in idxp.read_text(encoding="utf-8").splitlines() if x.strip()]
    batch_set = set(idxs)

    srcs = [s.findtext("Source") or "" for s in ET.parse(src_xml).getroot().iter("String")]
    dests = [s.findtext("Dest") or "" for s in ET.parse(canon_xml).getroot().iter("String")]

    by_src: dict[str, str] = {}
    for i, (s, d) in enumerate(zip(srcs, dests)):
        if d and d != s and i not in batch_set:
            by_src.setdefault(s, d)

    by_en_sent: dict[str, str] = {}
    for i, (s, d) in enumerate(zip(srcs, dests)):
        if not d or d == s or i in batch_set:
            continue
        en_sents, zh_sents = split_en(s), split_zh(d)
        if len(en_sents) == len(zh_sents):
            for e, z in zip(en_sents, zh_sents):
                by_en_sent.setdefault(e.strip(), z.strip())

    whole: list[tuple[int, str, str]] = []
    sent: list[tuple[int, str, str]] = []
    for idx in idxs:
        if idx >= len(srcs):
            continue
        s = srcs[idx]
        if s in by_src:
            whole.append((idx, s, by_src[s]))
            continue
        for e in split_en(s):
            e = e.strip()
            if e in by_en_sent:
                sent.append((idx, e, by_en_sent[e]))

    lines = [f"# {bid} canonical 既有定形（照抄，逐字一致）\n\n"]
    if not whole and not sent:
        lines.append("（本批无 canonical 既有定形可继承）\n")
    else:
        lines.append(f"整句 {len(whole)}，句级 {len(sent)}\n\n")
        if whole:
            lines.append("## 整句\n")
            for idx, s, z in whole:
                lines.append(f"[{idx}] {s}\n  -> {z}\n")
            lines.append("\n")
        if sent:
            lines.append("## 句级\n")
            for idx, s, z in sent:
                lines.append(f"[{idx}] {s}\n  -> {z}\n")

    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("".join(lines), encoding="utf-8")
    print(f"{bid}: {len(idxs)} idx, 整句 {len(whole)}, 句级 {len(sent)} -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
