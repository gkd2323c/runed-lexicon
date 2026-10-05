#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""过度泛化扫描：译文出现专名，源文却没有对应英文锚。

    中文出现「傲特莫」，原文没有 Altmer → 报。

与 translation-quality-gate 的 TERM004 方向相反。gate 只管「源文含锚点时
译文必须用定形」，源文不含锚点的行根本不在任何词条的检查范围内；而中文没有
词边界，专名很容易被顺手写上去。失效形态是把源文没说的事补进译文：

    he's an Elf.               →  他是傲特莫吗？        （源文未指明何族）
    I believe Elven supremacy  →  精灵至上             （形容词 Elven，项目规则留精灵）

**只报，不定罪。** 大量良性命中会进来，人工回源逐条裁决：

- 源文分写变体：词表 `Hollyfalls`，源文写 `Holly Falls`（译主形是对的）
- 源文作者拼错：`Celamer` 词条，源文写 `Celamir`；`Artaeum` 源文写 `Arteum`
- 源文用同义词：`Labyrinth` 词条，对上的是 `A Minor Maze`
- 承前省译：上文已点明，源文行靠上下文省略

所以本工具的产物是**候选清单**，裁决结论回填 `terms.json` 的 note，不在工具内定罪。

用法：
    py -3 overgeneralization_scan.py --xml <translated.xml> --contract <compiled.json> --stem <plugin>
    py -3 overgeneralization_scan.py --xml <...> --contract <...> --term Elf
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

# 锚点匹配复用 quality-gate 的 `_anchor_present`，不自写第二套（它已处理
# 整词优先、专名形容词派生、连字符变体）。复用规则见 skyrim-tool-dev-rules §1。
_GATE_SCRIPTS = Path(__file__).resolve().parents[2] / "translation-quality-gate" / "scripts"
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))
from term_match import _anchor_present  # noqa: E402


def load_rows(xml_path: Path) -> list[dict]:
    """读 canonical，只取已译行（Source == Dest 的行没有「译名」可言）。"""
    root = ET.parse(xml_path).getroot()
    rows = []
    for i, st in enumerate(root.iter("String")):
        def txt(tag: str) -> str:
            el = st.find(tag)
            return (el.text or "") if el is not None else ""
        src, dst = txt("Source"), txt("Dest")
        if src == dst:
            continue
        rows.append({"idx": i, "source": src, "dest": dst,
                     "rec": txt("REC"), "edid": txt("EDID")})
    return rows


def load_terms(contract_path: Path) -> list[dict]:
    """取词条；无译形或无英文锚的条目无从判断，直接跳过。"""
    raw = json.loads(contract_path.read_text(encoding="utf-8")).get("terms") or {}
    out = []
    for term_id, t in raw.items():
        if not isinstance(t, dict):
            continue
        zh = t.get("zh") or t.get("target") or ""
        en = t.get("english") or t.get("source") or ""
        if zh and en:
            out.append({"term_id": term_id, "english": en, "zh": zh})
    return out


def scan(rows: list[dict], terms: list[dict], only_term: str | None = None) -> list[dict]:
    """译文含 zh 且源文无 en 锚点 → 候选。"""
    findings = []
    for t in terms:
        if only_term and only_term.lower() not in t["english"].lower():
            continue
        for r in rows:
            if t["zh"] in r["dest"] and not _anchor_present(r["source"], t["english"]):
                findings.append({
                    "code": "OVERGEN001",
                    "term_id": t["term_id"], "english": t["english"], "zh": t["zh"],
                    "xml_index": r["idx"], "rec": r["rec"], "edid": r["edid"],
                    "source": r["source"], "dest": r["dest"],
                })
    findings.sort(key=lambda f: (f["term_id"], f["xml_index"]))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="过度泛化扫描：译文有专名、源文无对应英文锚")
    ap.add_argument("--xml", required=True, help="已译 canonical XML")
    ap.add_argument("--contract", required=True, help="编译契约 .compiled.json")
    ap.add_argument("--stem", help="MOD 名；给了就把报告写到 .work/<stem>/reports/")
    ap.add_argument("--term", help="只扫锚点含此串的词条")
    ap.add_argument("--report", help="报告输出路径（不给则只打印）")
    args = ap.parse_args(argv)

    xml_path, contract_path = Path(args.xml), Path(args.contract)
    for p in (xml_path, contract_path):
        if not p.is_file():
            print(f"error: 找不到 {p}", file=sys.stderr)
            return 2

    rows, terms = load_rows(xml_path), load_terms(contract_path)
    findings = scan(rows, terms, args.term)

    if args.stem and not args.report:
        rep = Path(".work") / args.stem / "reports" / f"{args.stem}-overgeneralization-report.json"
    else:
        rep = Path(args.report) if args.report else None
    if rep is not None:
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(json.dumps({
            "xml": str(xml_path), "contract": str(contract_path),
            "rows_scanned": len(rows), "terms_scanned": len(terms),
            "candidate_count": len(findings), "candidates": findings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report -> {rep}")

    if not findings:
        print("零候选。")
        return 0
    print(f"候选 {len(findings)} 条；已译行 {len(rows)}，词条 {len(terms)}")
    for f in findings:
        print(f"  [{f['xml_index']}] {f['english']} -> {f['zh']}")
        print(f"      SRC: {f['source'][:90]}")
        print(f"      DST: {f['dest'][:90]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
