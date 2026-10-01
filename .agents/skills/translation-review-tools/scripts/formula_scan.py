#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式句多译法扫描 + 修正集生成（只读 canonical，只写修正 map，不写回）。

口径：
- 部分 MOD 文本大量「固定收束句 + 变动的评注」拼装，整句源文各不相同，canon-wide 同源分裂检查看不见。
- 只看已译行（Dest 非空且 != Source）。英文按 (?<=[.!?])\\s+ 切句；中文按标点本身切（[。！？] 之后，
  保留标点，无损切分）——不能用 \\s+ 切中文。英中句数不等的行不参与（无法对位）。
- 分组：首句组 = 多句行的英文首句；尾句组 = 多句行的英文尾句；单句行（整句即该句）并入同英文句的首/尾句组。
  另有整句组 = 完全相同 Source 的多译（同源分裂，close_round 也会查，这里一并列出）。
- 组内中文形 >1 种即「分裂」。定形取多数形；平票取最早 idx 的形并在 notes 标「平票」。
- 修正只替换该句位置（首句/尾句），保留各 idx 的评注其余部分；单句行与整句组为整句替换（notes 标明）。
- 输出扁平 map：{idx: {"new": 整句新译, "notes": 说明}}，与既有 fix-map-*.json 同格式（改译文字段恒为 new）。
  传 --batches <BID...> --batches-dir <dir> 直接输出 close_round 需要的 {批次: {idx: fix}} 分组格式
  （归属由各批 index.txt 机械判定，判不出即失败），不必再手转分组。本脚本不写回。
- 整句组不认 same-source-exemptions.json 会把 prompt 驱动的必要差异重新「统一定形」成错值
  （本 MOD idx 26216 实测被改回「确实令人赞叹。」）；--stem 时默认读
  .work/<stem>/contracts/<stem>-same-source-exemptions.json，登记 idx 不参与整句定形。
- 输出路径（固定角色名，重跑覆盖）：.work/<stem>/maps/<stem>-fix-map-formula.json
退出码：0 = 零分裂（map 写成 {}）；1 = 有分裂（map 已写出修正）；2 = 错误。
用法：
  py -3 .agents/skills/translation-review-tools/scripts/formula_scan.py --stem <stem> [--out P] [--dry-run]
  py -3 .agents/skills/translation-review-tools/scripts/formula_scan.py --xml <canon.xml> [--out P] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixes_from_report import group_fixes, load_batch_owner  # noqa: E402

POS_LABEL = {"head": "首句", "tail": "尾句", "whole": "整句"}


def load_exemptions(path: Path | None) -> dict[str, set[int]]:
    """读 same-source-exemptions.json → {source: {idx...}}。

    事故锚定：省略式应答句（They are. / He is. / So do I.）的差异由玩家 prompt
    决定，已登记为 prompt 驱动的必要差异。整句分组不认这份登记时，会把上一轮
    按 prompt 回正的译文重新「统一定形」成错值——本 MOD idx 26216 实测被改回
    「确实令人赞叹。」（应承 They seem organized. 译「他们确实有条理。」）。
    """
    out: dict[str, set[int]] = {}
    if not path or not Path(path).is_file():
        return out
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("exemptions", data) if isinstance(data, dict) else {}
    for src, val in (entries or {}).items():
        if isinstance(val, dict):
            idxs = val.get("idxs") or []
        elif isinstance(val, list):
            idxs = [v.get("idxs") or [] for v in val if isinstance(v, dict)]
            idxs = [i for sub in idxs for i in sub]
        else:
            idxs = []
        out[src.strip()] = {int(i) for i in idxs}
    return out


def split_en(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", s.strip()) if p.strip()]


def split_zh(s: str) -> list[str]:
    """无损切分：切在 。！？ 之后（含连续标点如「？！」），''.join(parts) == s。"""
    parts = re.split(r"(?<=[。！？])(?![。！？”’」』）)])", s)
    return [p for p in parts if p != ""]


def load(xml_path: Path):
    rows = []
    for idx, s in enumerate(ET.parse(xml_path).getroot().iter("String")):
        src = s.findtext("Source") or ""
        dst = s.findtext("Dest") or ""
        rows.append((idx, s.findtext("REC") or "", src, dst))
    return rows


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stem", help="MOD stem name (e.g. TheKalpicAnomaly_GLENMORIL)")
    ap.add_argument("--xml", type=Path, help="Path to canonical XML")
    ap.add_argument("--out", type=Path, help="Path to output fix-map JSON")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写 map")
    ap.add_argument("--no-whole", action="store_true", help="不列整句（完全同源）分裂")
    ap.add_argument("--exemptions", type=Path, default=None,
                    help="same-source-exemptions.json（--stem 时默认 .work/<stem>/contracts/ 下同名文件）")
    ap.add_argument("--batches", nargs="+", default=None,
                    help="按批次分组输出（close_round 的 {batch: {idx: fix}} 形态）")
    ap.add_argument("--batches-dir", default=None,
                    help="批次目录（--batches 必与它同用）")
    ap.add_argument("--limit", type=int, default=0, help="最多打印 N 组明细（0 = 全部）")
    args = ap.parse_args()
    if bool(args.batches) != bool(args.batches_dir):
        raise SystemExit("--batches 与 --batches-dir 必须同用")
    return args


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    stem = None

    if args.stem:
        stem = args.stem
        xml_p = args.xml or (root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml")
        if not xml_p.exists():
            xml_p = root / "mods" / stem / f"{stem}_english_chinese_translated.xml"
        out_p = args.out or (root / ".work" / stem / "maps" / f"{stem}-fix-map-formula.json")
    elif args.xml:
        xml_p = args.xml
        out_p = args.out or (root / "_tmp" / "data" / "fix-map-formula.json")
    else:
        # Default fallback
        stem = "TheKalpicAnomaly_GLENMORIL"
        xml_p = root / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml"
        if not xml_p.exists():
            xml_p = root / "mods" / stem / f"{stem}_english_chinese_translated.xml"
        out_p = root / ".work" / stem / "maps" / f"{stem}-fix-map-formula.json"

    if not xml_p.exists():
        print(f"error: xml not found: {xml_p}", file=sys.stderr)
        return 2

    exempt_p = args.exemptions
    if exempt_p is None and stem:
        exempt_p = root / ".work" / stem / "contracts" / f"{stem}-same-source-exemptions.json"
    exempt = load_exemptions(exempt_p)
    if exempt:
        print(f"exemptions: {exempt_p}（{len(exempt)} 个源句，"
              f"{sum(len(v) for v in exempt.values())} 个 idx 不参与整句定形）")

    rows = load(xml_p)

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    translated_count = 0
    mismatch_count = 0
    exempt_skipped = 0

    for idx, rec, src, dst in rows:
        if not dst or dst == src:
            continue
        translated_count += 1
        en_s = split_en(src)
        zh_s = split_zh(dst)

        if not args.no_whole:
            # 豁免 idx 不进整句组：prompt 驱动的必要差异不是公式句分裂
            if idx in exempt.get(src.strip(), set()):
                exempt_skipped += 1
            else:
                groups[("whole", src.strip())].append({
                    "idx": idx, "pos": "whole", "en": src.strip(), "zh": dst.strip(),
                    "src_full": src, "dst_full": dst, "en_s": en_s, "zh_s": zh_s,
                })

        if len(en_s) != len(zh_s):
            mismatch_count += 1
            continue

        if len(en_s) == 1:
            continue

        groups[("head", en_s[0])].append({
            "idx": idx, "pos": "head", "en": en_s[0], "zh": zh_s[0],
            "src_full": src, "dst_full": dst, "en_s": en_s, "zh_s": zh_s,
        })
        groups[("tail", en_s[-1])].append({
            "idx": idx, "pos": "tail", "en": en_s[-1], "zh": zh_s[-1],
            "src_full": src, "dst_full": dst, "en_s": en_s, "zh_s": zh_s,
        })

    splits = []
    for (pos, en_key), items in groups.items():
        if len(items) < 2:
            continue
        form_counts = Counter(it["zh"] for it in items)
        if len(form_counts) < 2:
            continue

        top_forms = form_counts.most_common()
        is_tie = len(top_forms) > 1 and top_forms[0][1] == top_forms[1][1]
        canonical_zh = top_forms[0][0]

        splits.append({
            "pos": pos,
            "en": en_key,
            "canonical_zh": canonical_zh,
            "is_tie": is_tie,
            "form_counts": top_forms,
            "items": items,
        })

    pos_order = {"head": 0, "tail": 1, "whole": 2}
    splits.sort(key=lambda s: (pos_order.get(s["pos"], 9), -len(s["items"]), s["en"]))

    fixes: dict[str, dict] = {}
    printed = 0
    head_splits = 0
    tail_splits = 0
    whole_splits = 0

    for s in splits:
        if s["pos"] == "head":
            head_splits += 1
        elif s["pos"] == "tail":
            tail_splits += 1
        elif s["pos"] == "whole":
            whole_splits += 1

        if args.limit == 0 or printed < args.limit:
            tag = f"[{POS_LABEL.get(s['pos'], s['pos'])}]"
            tie_tag = "（平票）" if s["is_tie"] else ""
            print(f"{tag} 「{s['en']}」 {len(s['items'])} 处 {len(s['form_counts'])} 种{tie_tag} → 定形「{s['canonical_zh']}」")
            by_form: dict[str, list[int]] = defaultdict(list)
            for it in s["items"]:
                by_form[it["zh"]].append(it["idx"])
            for form, count in s["form_counts"]:
                idxs_str = ", ".join(str(i) for i in by_form[form][:8])
                more = f"... +{len(by_form[form]) - 8}" if len(by_form[form]) > 8 else ""
                print(f"      {count}× 「{form}」 idx {idxs_str} {more}")
            printed += 1

        for it in s["items"]:
            if it["zh"] == s["canonical_zh"]:
                continue
            idx = it["idx"]
            pos = it["pos"]
            new_text = ""
            if pos == "whole":
                new_text = s["canonical_zh"]
            elif pos == "head":
                zh_s = list(it["zh_s"])
                zh_s[0] = s["canonical_zh"]
                new_text = "".join(zh_s)
            elif pos == "tail":
                zh_s = list(it["zh_s"])
                zh_s[-1] = s["canonical_zh"]
                new_text = "".join(zh_s)

            if new_text and new_text != it["dst_full"]:
                fixes[str(idx)] = {
                    "new": new_text,
                    "notes": f"formula_{pos} 统一定形「{s['canonical_zh']}」" + (" (平票首形)" if s["is_tie"] else ""),
                }

    print(
        f"formula_scan: 已译 {translated_count} 行（句数不对位跳过 {mismatch_count} 行）；"
        f"公式句分裂 {head_splits + tail_splits} 组（首句 {head_splits} / 尾句 {tail_splits}，平票 {sum(1 for s in splits if s['is_tie'] and s['pos'] != 'whole')}），"
        f"待修 {len(fixes)} 处 → map {len(fixes)} 条；整句同源分裂 {whole_splits} 组"
        + (f"；豁免跳过 {exempt_skipped} 行" if exempt_skipped else "")
    )

    if not args.dry_run:
        payload = {k: v for k, v in sorted(fixes.items(), key=lambda kv: int(kv[0]))}
        if args.batches:
            owner = load_batch_owner(args.batches_dir, args.batches)
            grouped = group_fixes({int(k): v for k, v in fixes.items()}, owner, args.batches)
            payload = {bid: {k: v for k, v in fx.items()} for bid, fx in grouped.items()}
            print("分组: " + "，".join(f"{bid} {len(fx)} 条" for bid, fx in payload.items()))
            print("  → close_round --batches " + " ".join(payload))
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_p} ({len(fixes)} fixes)")

    return 1 if splits else 0


if __name__ == "__main__":
    raise SystemExit(main())
