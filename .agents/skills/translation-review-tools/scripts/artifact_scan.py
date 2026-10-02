#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审查工件泄漏扫描（只读）：在 canonical 译文 XML 的 Dest 里找备注/裁决说明/文件名/英文残留等非译文内容。

- 只读：不写任何文件，不改 canonical。
- 只扫已译行（Dest 非空且 Dest != Source）。
- 命中分两级：HIT（确定属工件，计入退出码）与 WEAK（疑似，默认只列出不计数；--strict 时计入）。
- 退出码：0 = 零命中；1 = 有命中；2 = 参数/文件错误。
用法：
  py -3 .agents/skills/translation-review-tools/scripts/artifact_scan.py --stem <stem> [--strict] [--json] [--limit N]
  py -3 .agents/skills/translation-review-tools/scripts/artifact_scan.py --xml <canonical.xml> [--strict] [--json] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# ---- HIT：确定属审查/流程工件 -------------------------------------------------
HIT_PATTERNS: list[tuple[str, str]] = [
    ("主会话", r"主会话|主管裁决|待主管|需主管"),
    ("统一为一形", r"统一为一形|统一成一形|收成一形|取多数形|多数形"),
    ("候选「", r"候选\s*[「“\"]|候选形|候选译"),
    ("文件名", r"DICTIONARY(?:\.md)?|CONTEXT\.md|GLOSSARY(?:\.md)?|PROGRESS\.md|SOP\.md|AGENTS\.md|terms\.json|[A-Za-z0-9_\-]+\.(?:md|json|py|txt|xml)\b"),
    ("零见证", r"零见证|零命中|底座实证|底座无对照|官方词典|词典形|官方形|词表形|登记为|已登记"),
    ("同型同改", r"同型同改|同型同形|同型同译|\d{3,6}\s*同(?:改|型|形|源)|同源副本|同句同改|同上改|[（(]\s*同上\s*[）)]"),
    ("idx 引用", r"(?:idx|xml_index|xml-index)\s*[:=：]?\s*\d+"),
    ("括注编号", r"[（(]\s*(?:见|同|参见|即|与)?\s*\d{3,6}(?:\s*[/、,，]\s*\d{3,6})*\s*(?:同型|同改|同形|同源|行|条|处|号)?[^）)]{0,20}[）)]"),
    ("箭头改写", r"→|⇒|=>|->|－>|—>"),
    ("流程术语", r"canonical|EDID|\bREC\b|TERM00\d|KEEP00\d|CHAR00\d|semgate|close_round|round_pipeline|REVIEW|TRANSLATED|PROVISIONAL|CONFIRMED"),
    # 「原形」是**正常汉语词**（显出原形／恢复原形／原形毕露），不是裁决行话；
    # 裸词匹配会误判正确译文（实测 33499「权力会在拒绝的四周显出原形。」被硬判 HIT 并阻断写回）。
    # 注记形态一定是「原形：X」这类带分隔符的，故收窄到带分隔符。
    # 真实注漏必然还带 `原译`／`改为[「“]`／`应译` 等其它标记，覆盖不因此降低。
    ("裁决说明", r"原译|原形[：:=＝]|原文作|改为[「“]|改作[「“]|改成[「“]|译为[「“]|译作[「“]|应译|应作[「“]|不取[「“]|取[「“][^」”]{1,20}[」”]|保留原|按词表|依词表|据底座|按底座|按官方"),
    ("译注括号", r"[（(]\s*(?:注|按|译注|译者注|审|待|疑|存疑|推测|暂定|待定|待裁|说明|理由|依据|备注)[^）)]{0,40}[）)]"),
    ("占位标记", r"TODO|FIXME|XXX|\?\?\?|？？？|【|】|\{\{|\}\}|<待|\[待|＜待|<<|>>"),
    ("直角引号", r"[「」『』]"),
]

# ---- WEAK：可能是正当译文，需人工看 ---------------------------------------------
WEAK_PATTERNS: list[tuple[str, str]] = [
    ("元语言词", r"审查|裁决|建议|词表|底座|译文|译名|定形|暂定|待定|同源"),
    ("斜杠并列", r"[^\s/／]{1,6}[/／][^\s/／]{1,6}"),
    ("整句引号包裹", r"^\s*“[^“”]+”\s*$"),
]

LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*[A-Za-z0-9]|[A-Za-z]{2,}")


def load_keep_tokens(keep_path: Path | None) -> set[str]:
    toks: set[str] = set()
    if not keep_path or not keep_path.exists():
        return toks
    try:
        data = json.loads(keep_path.read_text(encoding="utf-8"))
    except Exception:
        return toks

    def walk(o):
        if isinstance(o, str):
            for t in LATIN_TOKEN.findall(o):
                toks.add(t.lower())
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return toks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", help="MOD stem name")
    parser.add_argument("--xml", type=Path, help="Path to canonical XML")
    parser.add_argument("--keep", type=Path, help="Path to keep JSON")
    parser.add_argument("--strict", action="store_true", help="Treat WEAK matches as failures")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--show-weak", action="store_true", help="Show details for WEAK matches")
    parser.add_argument("--limit", type=int, default=50, help="Max hits to display")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path.cwd()

    if args.stem:
        stem = args.stem
        xml_p = args.xml or (root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml")
        if not xml_p.exists():
            xml_p = root / "mods" / stem / f"{stem}_english_chinese_translated.xml"
        keep_p = args.keep or (root / ".work" / stem / "contracts" / f"{stem}.keep.json")
    elif args.xml:
        xml_p = args.xml
        keep_p = args.keep
    else:
        # Default auto-discovery from current dir if TheKalpicAnomaly_GLENMORIL exists
        stem = "TheKalpicAnomaly_GLENMORIL"
        xml_p = root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml"
        if not xml_p.exists():
            xml_p = root / "mods" / stem / f"{stem}_english_chinese_translated.xml"
        keep_p = root / ".work" / stem / "contracts" / f"{stem}.keep.json"

    if not xml_p.exists():
        print(f"error: XML file not found: {xml_p}", file=sys.stderr)
        return 2

    tree = ET.parse(xml_p)
    strings = tree.getroot().findall(".//String")

    keep_tokens = load_keep_tokens(keep_p)

    hit_regexes = [(name, re.compile(pat)) for name, pat in HIT_PATTERNS]
    weak_regexes = [(name, re.compile(pat)) for name, pat in WEAK_PATTERNS]

    hits = []
    weaks = []
    translated_count = 0

    for idx, s in enumerate(strings):
        src = s.findtext("Source") or ""
        dst = s.findtext("Dest") or ""
        if not dst or dst == src:
            continue
        translated_count += 1

        matched_hit = False
        for name, rx in hit_regexes:
            m = rx.search(dst)
            if m:
                hits.append({"idx": idx, "type": "HIT", "rule": name, "match": m.group(0), "dest": dst, "source": src})
                matched_hit = True
                break

        if not matched_hit:
            for name, rx in weak_regexes:
                m = rx.search(dst)
                if m:
                    weaks.append({"idx": idx, "type": "WEAK", "rule": name, "match": m.group(0), "dest": dst, "source": src})
                    break

    weak_counts: dict[str, int] = {}
    for w in weaks:
        r = w["rule"]
        weak_counts[r] = weak_counts.get(r, 0) + 1

    weak_summary = "，".join(f"{k} {v}" for k, v in weak_counts.items())
    print(f"WEAK 分类计数（--show-weak 看明细）: {weak_summary}")
    print(f"artifact_scan: 已译 {translated_count} 行，命中 {len(hits)} 行，疑似(未计) {len(weaks)} 行")

    if hits:
        print(f"\n[FAIL] 发现 {len(hits)} 处确定工件泄漏:")
        for h in hits[:args.limit]:
            print(f"  idx {h['idx']} [{h['rule']}: {h['match']}]: {h['dest']}")
        if len(hits) > args.limit:
            print(f"  ... 另有 {len(hits) - args.limit} 处已截断")

    if args.show_weak and weaks:
        print(f"\n[WEAK] 疑似工件清单 ({len(weaks)} 处):")
        for w in weaks[:args.limit]:
            print(f"  idx {w['idx']} [{w['rule']}: {w['match']}]: {w['dest']}")

    if args.strict and weaks:
        return 1
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
