#!/usr/bin/env python3
"""已写回批修正的一条龙收口入口。

事故锚定（为什么需要这个脚本而不是手串命令）：
- 手串 apply_fixes -> round(write) 曾撞 original_dest 软保护整批拒写（result 模式
  只适用于首写），已写回行修订必须 patch 模式；本脚本固化该选择。
- 手写 verify 断言 token 曾手误（同一时刻 vs 同一个时刻）造成假红；本脚本的
  断言从 fixes 的 new 值自动生成，逐 idx 验证 `new in dest`。
- readout 忘记在修正后重新生成会让审查读旧稿（r18 事故形态）；本脚本默认 regen。
- 同源副本行（DIAL/RNAM 等同 Source 多行）在别的批次，逐批修正只改自己批次的键；
  本脚本收口时内置同源组对账，split>0 直接失败。

流程：fixes 分组 -> apply_fixes(每批, 生成 patch) -> patch write(每批) ->
round_pipeline verify,snapshot -> readout regen(每批) -> 同源组对账 -> new 断言。

用法：
  py -3 close_round.py --stem S --xml <source.xml> --contract <compiled.json> \
      --fixes <fix1.json> [--fixes <fix2.json>] --batches B1 B2 [--note "..."]

fixes 文件格式（多文件合并）：
  {"<batch>": {"<idx>": {"new": "...", "status": "...", "notes": "..."}}}
  也接受单批裸格式 {"<idx>": {...}}，此时必须且只能配一个 --batches 值。

安全边界：只处理已写回（canonical 已有该行现值）的批修正；首次写回（consume/write
result 链）仍走 round_pipeline 原生入口，本脚本不接管。只读源 XML，写回仅经
xtranslator-xml-writer 的 --patch 通道。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # repo root
APPLY = ROOT / ".agents/skills/translation-review-tools/scripts/apply_fixes.py"
WRITER = ROOT / ".agents/skills/xtranslator-xml-writer/scripts/write_translations.py"
ROUND = ROOT / ".agents/skills/translation-batch-ops/scripts/round_pipeline.py"
READOUT = ROOT / ".agents/skills/translation-review-tools/scripts/read_batch.py"


def run(cmd: list[str], label: str) -> None:
    r = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:]
    print(f"[{label}] rc={r.returncode}")
    for line in tail:
        print("   ", line)
    if r.returncode != 0:
        raise SystemExit(f"CLOSE ROUND FAIL at: {label}")


def load_fixes(paths: list[str], batches: list[str]) -> dict[str, dict[str, dict]]:
    merged: dict[str, dict[str, dict]] = {}
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        if any(k.isdigit() for k in data):  # 裸格式 {idx: fix}
            if len(batches) != 1:
                raise SystemExit("裸格式 fixes 只能配一个 --batch")
            merged.setdefault(batches[0], {}).update(data)
        else:  # 标准格式 {batch: {idx: fix}}
            for bid, fx in data.items():
                merged.setdefault(bid, {}).update(fx)
    unknown = set(merged) - set(batches)
    if unknown:
        raise SystemExit(f"fixes 含未声明的批次: {sorted(unknown)}；把它们加进 --batches")
    return merged


def same_source_split(xml: Path) -> list[tuple[str, dict[str, list[int]]]]:
    rows = ET.parse(xml).getroot().findall(".//String")
    by_src: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, s in enumerate(rows):
        src = s.findtext("Source") or ""
        if src.strip():
            by_src[src][s.findtext("Dest") or ""].append(i)
    return [(src, d) for src, d in by_src.items() if len(d) > 1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stem", required=True)
    ap.add_argument("--batches", nargs="+", required=True)
    ap.add_argument("--fixes", action="append", required=True)
    ap.add_argument("--xml", required=True, help="源 XML（_english_chinese.xml）")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--translated-xml", default=None)
    ap.add_argument("--note", default="close_round")
    ap.add_argument("--no-readout", action="store_true")
    args = ap.parse_args()

    stem = args.stem
    translated = Path(args.translated_xml) if args.translated_xml else (
        ROOT / "mods" / f"{stem}.esp" / f"{stem}_english_chinese_translated.xml"
    )
    source = ROOT / args.xml
    contract = ROOT / args.contract
    workb = ROOT / ".work" / stem / "batches"
    archive = ROOT / ".work" / stem / "archive"
    report = ROOT / ".work" / stem / "reports" / f"{stem}-writeback-report.json"

    by_batch = load_fixes(args.fixes, args.batches)
    print(f"fixes: { {b: sorted(f) for b, f in by_batch.items()} }")

    # 1) apply_fixes 每批（生成 patch；校验先行，失败即炸不写）
    patches: list[tuple[str, Path]] = []
    for bid in args.batches:
        if bid not in by_batch:
            continue
        fx_path = workb / bid / "close-round-fixes.json"
        fx_path.write_text(json.dumps(by_batch[bid], ensure_ascii=False), encoding="utf-8")
        patch = workb / bid / "close-round-patch.json"
        run([sys.executable, APPLY, "--stem", stem, "--batch", bid,
             "--fixes", fx_path, "--translated-xml", translated, "--patch-out", patch],
            f"apply:{bid}")
        fx_path.unlink(missing_ok=True)
        # 同值修正时 apply_fixes 产出空 patch（全部已就位），跳过 write
        try:
            pobj = json.loads(patch.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pobj = {}
        if pobj:
            patches.append((bid, patch))

    # 2) patch write 每批（已写回行修订唯一合法通道）
    for bid, patch in patches:
        run([sys.executable, WRITER, "--xml", translated, "--source-xml", source,
             "--report", report, "--force", "--in-place", "--archive-to", archive,
             "--patch", patch], f"patch:{bid}")

    # 3) round verify,snapshot（不带 write：result 模式对已写回键会拒）
    run([sys.executable, ROUND, "--stem", stem, "--batches", *args.batches,
         "--xml", source, "--contract", contract,
         "--phases", "verify,snapshot", "--note", args.note], "round")

    # 4) readout regen（防审查读旧稿）
    if not args.no_readout:
        for bid in args.batches:
            out = workb / bid / "readout.txt"
            run([sys.executable, READOUT, "--stem", stem, "--batch", bid, "--out", out],
                f"readout:{bid}")

    # 5) 同源组对账（跨批副本行漂移的常驻防线）
    splits = same_source_split(source if False else translated)  # 对 canonical
    if splits:
        for src, forms in splits[:10]:
            print("SPLIT:", src[:80])
            for dest, idxs in forms.items():
                print("   ", idxs, "->", dest[:70])
        raise SystemExit(f"CLOSE ROUND FAIL: 同源多 Dest 分裂 {len(splits)} 组")

    # 6) new 断言（从 fixes 自动生成，消灭手写 token 手误）
    rows = ET.parse(translated).getroot().findall(".//String")
    checked = 0
    for bid, fx in by_batch.items():
        for idx_s, fix in fx.items():
            if "new" not in fix:
                continue
            i = int(idx_s)
            dest = rows[i].findtext("Dest") or ""
            if fix["new"] not in dest:
                raise SystemExit(f"CLOSE ROUND FAIL: idx {i} 断言失败\n  expect: {fix['new'][:80]}\n  actual: {dest[:80]}")
            checked += 1

    import hashlib
    sha = hashlib.sha256(translated.read_bytes()).hexdigest()
    print(f"CLOSE ROUND PASS  batches={','.join(args.batches)}  "
          f"applied_new={checked}  same_source_split=0  canonical={sha[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
