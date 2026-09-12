#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 canonical 译文 XML 导出全库 translation-result，供全量 quality_gate 复核。

为什么需要它
------------
`quality_gate.py` 只吃 translation-result JSON，而流水线产出的 result 是**批次级**的：
每个 result 只覆盖该批次的 idx。因此既有 `*-gate-report.json` 报告的 `units_checked`
通常远小于全库行数（Artaeum 实测 74 / 14968），**不能据此声明全库通过门禁**。

真实事故：批次级 gate 全 PASS 的 canonical，在全库复核时抓出 `圣母`（现实宗教禁令）
违规——该行属于早已写回的批次，此后从未再经过任何 gate。

本工具把 canonical 的每一条导出成 gate 可消费的 result，使全量复核成为一条命令。

口径
----
- `Source == Dest` 的行导出为 `KEEP`（保留原文，gate 不计入翻译单元）；
- 其余导出为 `TRANSLATED`；
- result 的 `original_dest` 取 Source（本 result 用于校验 canonical 现状，
  不参与增量写回）。

用法
----
    py -3 export_full_result.py \\
        --xml mods/<plugin>/<plugin>_english_chinese.xml \\
        --canonical mods/<plugin>/<plugin>_english_chinese_translated.xml \\
        --out _tmp/data/full-result.json

    py -3 .agents/skills/translation-quality-gate/scripts/quality_gate.py \\
        --result _tmp/data/full-result.json \\
        --contract .work/<plugin>/contracts/<plugin>.compiled.json \\
        --keep-list .work/<plugin>/contracts/<plugin>.keep.json \\
        --xml mods/<plugin>/<plugin>_english_chinese.xml \\
        --report .work/<plugin>/reports/<plugin>-gate-report.json

只读：不修改任何 XML、terms 或契约。输出路径由调用方给定。
"""
import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_result(source_xml, canonical_xml):
    src_root = ET.parse(source_xml).getroot()
    canon_root = ET.parse(canonical_xml).getroot()
    src_strings = list(src_root.iter("String"))
    canon_strings = list(canon_root.iter("String"))
    if len(src_strings) != len(canon_strings):
        raise ValueError(
            "String 数量不一致：source=%d canonical=%d"
            % (len(src_strings), len(canon_strings))
        )

    items = []
    for i, (s, c) in enumerate(zip(src_strings, canon_strings)):
        source = s.findtext("Source") or ""
        dest = c.findtext("Dest") or ""
        items.append({
            "translation_unit_id": "xml-index:%d" % i,
            "xml_index": i,
            "edid": c.findtext("EDID") or "",
            "rec": c.findtext("REC") or "",
            "source": source,
            "original_dest": source,
            "translation": dest,
            "status": "KEEP" if source == dest else "TRANSLATED",
        })

    return {
        "schema_version": 1,
        "purpose": "full-canonical audit",
        "context": {
            "xtranslator_xml": {
                "path": source_xml,
                "sha256": sha256_file(source_xml),
            },
            "canonical_xml": {
                "path": canonical_xml,
                "sha256": sha256_file(canonical_xml),
            },
        },
        "translations": items,
    }


def main():
    ap = argparse.ArgumentParser(description="从 canonical 导出全库 translation-result")
    ap.add_argument("--xml", required=True, help="源 xTranslator XML（身份校验基准）")
    ap.add_argument("--canonical", required=True, help="canonical 译文 XML")
    ap.add_argument("--out", required=True, help="输出 result JSON 路径")
    a = ap.parse_args()

    doc = build_result(a.xml, a.canonical)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    total = len(doc["translations"])
    kept = sum(1 for t in doc["translations"] if t["status"] == "KEEP")
    print("wrote %d units (%d TRANSLATED / %d KEEP) -> %s" % (total, total - kept, kept, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
