#!/usr/bin/env python3
"""已写回批修正的一条龙收口入口。

事故锚定（为什么需要这个脚本而不是手串命令）：
- 手串 apply_fixes -> round(write) 曾撞 original_dest 软保护整批拒写（result 模式
  只适用于首写），已写回行修订必须 patch 模式；本脚本固化该选择。
- 手写 verify 断言 token 曾手误（同一时刻 vs 同一个时刻）造成假红；本脚本的
  断言从 fixes 的 new 值自动生成，逐 idx 验证 `new in dest`。
- readout 忘记在修正后重新生成会让审查读旧稿（r18 事故形态）；本脚本默认 regen。
- 同源副本行（DIAL/RNAM 等同 Source 多行）在别的批次，逐批修正只改自己批次的键；
  本脚本收口时内置同源组对账，split>0 直接失败。对账默认严格（同源必同形）；仅当
  译文差异确由对话 prompt 决定时，经人工回源核实后登记到
  `contracts/<stem>-same-source-exemptions.json` 才放行（I am. 分别回 You sound
  disappointed. 与 You sound happy about this. 即此类）。
- 同源对账早期无豁免通道，prompt 驱动的合法差异永久判成分裂，close_round 无限失败。

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
        if not isinstance(data, dict):
            raise SystemExit(
                f"fixes 顶层格式错误: {p} 是 {type(data).__name__}，"
                f"应为 {{batch: {{idx: fix}}}} 或裸格式 {{idx: fix}}"
            )
        if any(str(k).isdigit() for k in data):  # 裸格式 {idx: fix}
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


def _exempt_forms(src: str, forms: dict[str, list[int]],
                  exemptions: dict[str, set[int]]) -> dict[str, list[int]]:
    """剔除显式登记的合法差异形；剔除后不足两形即视为无分裂。"""
    allow = exemptions.get(src)
    if not allow:
        return forms
    return {d: idxs for d, idxs in forms.items() if not set(idxs) <= allow}


def load_exemptions(path: Path) -> dict[str, set[int]]:
    """同源多形豁免：{"<source>": {"idxs": [...], "reason": "..."}}。

    对账默认严格（同源必同形）。只有经人工逐条回源核实、确认译文差异由对话 prompt
    决定且属必要差异的行，才登记在这里；未登记的分裂一律让 close_round 失败。
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, set[int]] = {}
    for src, items in (data.get("exemptions") or data).items():
        if isinstance(items, dict):
            items = [items]
        idxs: set[int] = set()
        for it in items or []:
            idxs.update(int(i) for i in (it.get("idxs") or []))
        if idxs:
            out[src] = idxs
    return out


def same_source_split(xml: Path, exemptions: dict[str, set[int]] | None = None) -> list[tuple[str, dict[str, list[int]]]]:
    """同源多 Dest 分裂组。exemptions 为显式登记的合法差异形，剔除后仍 >=2 形才判分裂。"""
    rows = ET.parse(xml).getroot().findall(".//String")
    by_src: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, s in enumerate(rows):
        src = s.findtext("Source") or ""
        dest = s.findtext("Dest") or ""
        if src.strip() and dest != src:
            by_src[src][dest].append(i)
    out: list[tuple[str, dict[str, list[int]]]] = []
    for src, forms in by_src.items():
        if len(forms) < 2:
            continue
        if exemptions:
            forms = _exempt_forms(src, forms, exemptions)
        if len(forms) > 1:
            out.append((src, forms))
    return out


def _index_owner(workb: Path) -> dict[int, str]:
    owner: dict[int, str] = {}
    for d in sorted(workb.iterdir()):
        f = d / "index.txt"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.isdigit():
                owner[int(s)] = d.name
    return owner


def prompts_for(workb: Path, idxs: set[int]) -> dict[int, str | None]:
    """idx -> DIAL prompt（懒加载，只解析涉及 idx 的批次 context.json）。

    供人工核实豁免登记时取铁证，不参与自动判定（见 load_exemptions）。
    """
    owner = _index_owner(workb)
    wanted: dict[str, set[int]] = defaultdict(set)
    for i in idxs:
        bid = owner.get(i)
        if bid:
            wanted[bid].add(i)
    prompts: dict[int, str | None] = {}
    for bid, need in wanted.items():
        cf = workb / bid / "context.json"
        if not cf.is_file():
            continue
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for b in data.get("batches", []):
            for e in b.get("entries", []):
                idx = (e.get("xml") or {}).get("index")
                if idx not in need:
                    continue
                info = ((e.get("dialogue_context") or {}).get("info") or {})
                pr = (info.get("prompt") or "").strip()
                prompts[idx] = pr or None
    for i in idxs:
        prompts.setdefault(i, None)
    return prompts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stem", required=True)
    ap.add_argument("--batches", nargs="+", required=True)
    ap.add_argument("--fixes", action="append", required=True)
    ap.add_argument("--xml", required=True, help="源 XML（_english_chinese.xml）")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--translated-xml", default=None)
    ap.add_argument("--note", default="close_round")
    ap.add_argument("--archive-keep", type=int, default=5,
                    help="写回归档保留的最近代数（默认 5，0=不限）；archive 只增不减会随轮次无限膨胀")
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

    # --xml 是**源** XML。传成 canonical（_translated.xml）是极易犯的错，而且**不炸**：
    # writer 拿到 --xml/--source-xml 都是 canonical 时仍能正确写回（post-write 校验照过、
    # 门禁照 PASS），受害的只有 round_pipeline 里的 progress_snapshot —— 它把 canonical
    # 当源文去算战役口径，于是 `campaign_from_canonical` 恒为 0、目标行从 42994 掉到 34100，
    # 每轮刷一条「口径不一致: pipeline=34100 canonical=0」告警。那条告警被当噪声忽略了很久，
    # 实际是调用错误。源文件一旦显式校验，这种错就不会再静默发生。
    if source.resolve() == translated.resolve():
        print(f"  !! --xml 传成了 canonical：{args.xml}\n"
              f"     --xml 要的是**源** XML（{stem}_english_chinese.xml）；"
              f"canonical 由 --translated-xml 或默认推导，不需要传。\n"
              f"     继续会把快照的战役口径算成 0，并刷一条假的「口径不一致」告警。",
              file=sys.stderr)
        return 1

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
             "--archive-keep", str(args.archive_keep),
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

    # 5) 同源组对账（跨批副本行漂移的常驻防线；显式登记的 prompt 必要差异不计）
    exemptions = load_exemptions(workb.parent / "contracts" / f"{stem}-same-source-exemptions.json")
    splits = same_source_split(translated, exemptions)  # 对 canonical
    if splits:
        for src, forms in splits[:10]:
            print("SPLIT:", src[:80])
            for dest, idxs in forms.items():
                print("   ", idxs, "->", dest[:70])
        hint = ""
        if not exemptions:
            hint = ("；若确属 prompt 决定的必要差异，先回源核实并登记到 "
                    f"contracts/{stem}-same-source-exemptions.json 再重跑")
        raise SystemExit(f"CLOSE ROUND FAIL: 同源多 Dest 分裂 {len(splits)} 组{hint}")

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
