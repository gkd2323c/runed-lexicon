#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次派单前的同源继承预填（prefill）与折叠展开（expand）。

为什么需要这个脚本：MOD 文本里重复源句占比往往很高（应答句、公式收束句、
矩阵批反复出现的基句）。旧流程把「照抄 canonical 既有形」与「同源重复句
复制」这两件纯机械劳动交给译者实例输出：既占实例预算，也把复制错位的风险
留在 LLM 侧。本脚本把这两件事前移给确定性程序：
  prefill  —— 批次内凡 canonical 已有同源译文的键直接继承；其余键按同源
              折叠成「唯一句清单」，只把代表键交给译者。
  expand   —— 译者只回代表键的译文，由本脚本展开成完整键集 map.json。

收益随批次重复度变化：重复密集的批次可把译者输出量压到原键数的两三成，
全新内容的批次收益接近零；`prefill` 的统计行会直接给出该批的继承数与折叠
数，据此判断是否值得走本通道。

安全边界：
  - 只读 canonical 与源 XML，写批目录内的 prefill/uniq/map 文件，绝不触碰
    canonical；写回仍走 round_pipeline / write_translations。
  - 继承方向与流程口径一致（同源必同形，close_round 同源对账 split 必须为
    0）。`contracts/<stem>-same-source-exemptions.json` 登记的「prompt 驱动
    合法差异」源句一律不继承，留给译者按语境处理。
  - 继承项在 map 的 notes 标 `inherited`，供审查线优先核对。

子命令：
  prefill --stem S --batch B [--part a] [--out-prefill prefill.json] [--out-uniq uniq.txt]
  expand  --stem S --batch B [--part a] --prefill prefill.json --uniq-map uniq-map.json [--out map.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from close_round import load_exemptions  # noqa: E402  同 skill 内复用，避免重写豁免解析


def mod_dir(stem: str) -> Path:
    for cand in (Path.cwd() / "mods" / f"{stem}.esp", Path.cwd() / "mods" / stem):
        if cand.is_dir():
            return cand
    raise SystemExit(f"MOD directory not found for {stem}")


def batch_dir(stem: str, batch: str) -> Path:
    d = Path.cwd() / ".work" / stem / "batches" / batch
    if not d.is_dir():
        raise SystemExit(f"batch directory not found: {d}")
    return d


def read_index(d: Path, part: str | None) -> list[int]:
    f = d / (f"index-part-{part}.txt" if part else "index.txt")
    if not f.is_file():
        raise SystemExit(f"index not found: {f}")
    return [int(x.strip()) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_sources(stem: str) -> list[str]:
    xml = mod_dir(stem) / f"{stem}_english_chinese.xml"
    if not xml.is_file():
        raise SystemExit(f"source XML not found: {xml}")
    return [s.findtext("Source") or "" for s in ET.parse(xml).getroot().iter("String")]


def load_canon_forms(stem: str, exclude: set[int]) -> dict[str, str]:
    """canonical 已译行的 Source -> Dest（首见），排除本批 idx 防自继承。"""
    xml = mod_dir(stem) / f"{stem}_english_chinese_translated.xml"
    if not xml.is_file():
        raise SystemExit(f"canonical XML not found: {xml}")
    out: dict[str, str] = {}
    for i, s in enumerate(ET.parse(xml).getroot().iter("String")):
        if i in exclude:
            continue
        src = s.findtext("Source") or ""
        dst = s.findtext("Dest") or ""
        if src and dst and dst != src:
            out.setdefault(src, dst)
    return out


def load_exempt_sources(stem: str) -> set[str]:
    path = Path.cwd() / ".work" / stem / "contracts" / f"{stem}-same-source-exemptions.json"
    return set(load_exemptions(path))


def cmd_prefill(args: argparse.Namespace) -> int:
    d = batch_dir(args.stem, args.batch)
    idxs = read_index(d, args.part)
    batch_set = set(idxs)
    srcs = load_sources(args.stem)
    forms = load_canon_forms(args.stem, batch_set)
    exempt = load_exempt_sources(args.stem)

    inherited: dict[str, str] = {}
    pending: list[tuple[int, str]] = []
    for i in idxs:
        s = srcs[i] if i < len(srcs) else ""
        if s and s in forms and s not in exempt:
            inherited[str(i)] = forms[s]
        else:
            pending.append((i, s))

    rep_of: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    reps: list[str] = []
    for i, s in pending:
        if s in rep_of:
            groups[rep_of[s]].append(str(i))
        else:
            rep_of[s] = str(i)
            groups[str(i)] = [str(i)]
            reps.append(str(i))

    suffix = f"-part-{args.part}" if args.part else ""
    out_prefill = Path(args.out_prefill) if args.out_prefill else d / f"prefill{suffix}.json"
    out_uniq = Path(args.out_uniq) if args.out_uniq else d / f"uniq{suffix}.txt"

    payload = {
        "stem": args.stem,
        "batch": args.batch,
        "part": args.part,
        "index": f"index{suffix}.txt",
        "inherited": inherited,
        "groups": groups,
        "reps": reps,
    }
    out_prefill.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    lines = []
    for rep in reps:
        lines.append(f"{rep}\t{srcs[int(rep)]}")
    out_uniq.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")

    total = len(idxs)
    fold_saved = len(pending) - len(groups)
    print(f"{args.batch}{suffix}: 键 {total} | 继承 {len(inherited)} | 待译 {len(pending)} 行"
          f" → 折叠为 {len(groups)} 个唯一句（省 {fold_saved} 行）")
    print(f"  -> {out_prefill}")
    print(f"  -> {out_uniq}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    d = batch_dir(args.stem, args.batch)
    idxs = read_index(d, args.part)
    pre = json.loads(Path(args.prefill).read_text(encoding="utf-8"))
    uni = json.loads(Path(args.uniq_map).read_text(encoding="utf-8-sig"))

    out: dict[str, dict] = {}
    for k, v in (pre.get("inherited") or {}).items():
        out[k] = {"translation": v, "status": "TRANSLATED", "confidence": "HIGH", "notes": "inherited"}

    missing: list[str] = []
    for rep, members in (pre.get("groups") or {}).items():
        item = uni.get(rep)
        if item is None:
            missing.append(rep)
            continue
        if isinstance(item, dict):
            txt = str(item.get("translation") or "").strip()
            conf = str(item.get("confidence") or "HIGH")
        else:
            txt = str(item).strip()
            conf = "HIGH"
        if not txt:
            missing.append(rep)
            continue
        for m in members:
            out[m] = {"translation": txt, "status": "TRANSLATED", "confidence": conf, "notes": "folded"}

    if missing:
        raise SystemExit(f"uniq-map 缺 {len(missing)} 个代表键译文: {missing[:10]}")

    need = {str(i) for i in idxs}
    if set(out) != need:
        miss = sorted(need - set(out), key=int)
        extra = sorted(set(out) - need, key=int)
        raise SystemExit(f"展开后键集不符: 缺 {len(miss)} {miss[:8]} / 多 {len(extra)} {extra[:8]}")

    out_path = Path(args.out) if args.out else d / (f"map-part-{args.part}.json" if args.part else "map.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    inherited_n = len(pre.get("inherited") or {})
    print(f"{args.batch}: expand {len(out)} 键（继承 {inherited_n} + 折叠展开 {len(out) - inherited_n}）-> {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("prefill", help="继承 + 折叠，产出 prefill.json 与 uniq.txt")
    a.add_argument("--stem", required=True)
    a.add_argument("--batch", required=True)
    a.add_argument("--part")
    a.add_argument("--out-prefill", type=Path)
    a.add_argument("--out-uniq", type=Path)
    a.set_defaults(func=cmd_prefill)

    b = sub.add_parser("expand", help="把译者的代表键译文展开成完整 map.json")
    b.add_argument("--stem", required=True)
    b.add_argument("--batch", required=True)
    b.add_argument("--part")
    b.add_argument("--prefill", required=True)
    b.add_argument("--uniq-map", required=True)
    b.add_argument("--out", type=Path)
    b.set_defaults(func=cmd_expand)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
