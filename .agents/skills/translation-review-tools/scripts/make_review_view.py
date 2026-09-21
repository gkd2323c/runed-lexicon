# -*- coding: utf-8 -*-
"""为审查子代理生成精简视图，从根本消除「文件截断」。

背景：translation.json 是 indent=2 的美化 JSON，大批量（80+ 条）可达
40KB / 1200+ 行，读取工具按行截断，子代理只能靠反复补读拼全，既慢又易漏。

审查只需要 xml_index / source / translation 三字段。精简 + 紧凑后体积约为
原来的 1/5（40KB -> 8KB），一次可读完。

一致性守卫：视图值来自批次 translation.json；若该批与 canonical 不一致
（修正未回写批次，历史事故模式：旧视图重报已修问题），默认拒绝生成并提示
先用 batch_sync.py 同步；--allow-drift 跳过检查。

用法:
    python make_review_view.py INFO-147 [INFO-148 ...] [--out DIR]
    python make_review_view.py --batch-file batches.txt

输出: .work/<MOD>/notes/review-view-<ID>.json （单批）
      或 --out 指定目录下 review-view-<ID>.json
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FIELDS = ("xml_index", "source", "translation")
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve_canonical(mod: str, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p if p.is_file() else None
    matches = sorted((PROJECT_ROOT / "mods").glob(f"*/{mod}_english_chinese_translated.xml"))
    return matches[0] if matches else None


def load_canonical(path: Path) -> tuple[list[str], list[str]]:
    root = ET.parse(str(path)).getroot()
    strings = root.findall(".//String")
    dests = [s.findtext("Dest") or "" for s in strings]
    srcs = [s.findtext("Source") or "" for s in strings]
    return dests, srcs


def collect_drift(root: Path, batches: list[str], dests: list[str], srcs: list[str]) -> dict:
    """报每批 translation.json 与 canonical 不一致的条目（批次值已译时）。"""
    drift: dict[str, list] = {}
    for b in batches:
        src = root / "batches" / b / "translation.json"
        if not src.exists():
            continue
        try:
            units = json.loads(src.read_text(encoding="utf-8")).get("translations", [])
        except (OSError, json.JSONDecodeError):
            continue
        for u in units:
            idx = u.get("xml_index")
            cur = u.get("translation", "")
            if not isinstance(idx, int) or idx >= len(dests):
                continue
            cval = dests[idx]
            if not cval or cval == srcs[idx] or not cur:
                continue
            if cur != cval:
                drift.setdefault(b, []).append((idx, cur, cval))
    return drift


def build_view(src: Path, dst: Path) -> tuple:
    d = json.loads(src.read_text(encoding="utf-8"))
    units = d.get("translations", d if isinstance(d, list) else [])
    view = {
        "batch": src.parent.name,
        "unit_count": len(units),
        "translations": [
            {k: u.get(k, "") for k in FIELDS} for u in units
        ],
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        json.dumps(view, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return len(units), dst.stat().st_size, src.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batches", nargs="*", help="批次 ID，如 INFO-147")
    ap.add_argument("--batch-file", help="每行一个批次 ID 的文件")
    ap.add_argument("--work", default=".work", help=".work 根目录")
    ap.add_argument("--mod", default="TheKalpicAnomaly", help="MOD 名（.work 下的子目录）")
    ap.add_argument("--out", default=None, help="输出目录，默认 .work/<mod>/notes")
    ap.add_argument("--canonical", default=None,
                    help="canonical XML；缺省按 mods/*/<mod>_english_chinese_translated.xml 推断；找不到则跳过一致性校验")
    ap.add_argument("--allow-drift", action="store_true",
                    help="批次与 canonical 不一致时仍生成（默认拒绝，防旧视图重报已修问题）")
    args = ap.parse_args()

    batches = list(args.batches)
    if args.batch_file:
        batches += [
            ln.strip()
            for ln in Path(args.batch_file).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    if not batches:
        print("error: 至少给一个批次 ID", file=sys.stderr)
        return 2

    root = Path(args.work) / args.mod
    outdir = Path(args.out) if args.out else root / "notes"

    # ---- 一致性守卫 ----
    if not args.allow_drift:
        canonical = resolve_canonical(args.mod, args.canonical)
        if canonical is None:
            print("WARN: 未找到 canonical，跳过一致性校验", file=sys.stderr)
        else:
            dests, srcs = load_canonical(canonical)
            drift = collect_drift(root, batches, dests, srcs)
            if drift:
                total = sum(len(v) for v in drift.values())
                print(f"拒绝生成：{len(drift)} 批 / {total} 条与 canonical 不一致（旧视图会重报已修问题）",
                      file=sys.stderr)
                for b in sorted(drift)[:10]:
                    for idx, cur, cval in drift[b][:4]:
                        print(f"  [{b}] {idx}", file=sys.stderr)
                        print(f"    批次: {cur[:70]}", file=sys.stderr)
                        print(f"    canonical: {cval[:70]}", file=sys.stderr)
                print(f"先同步: py -3 .agents/skills/translation-batch-ops/scripts/batch_sync.py apply --stem {args.mod}",
                      file=sys.stderr)
                return 1
            print(f"一致性校验通过（{len(batches)} 批 vs canonical）")

    tot_before = tot_after = 0
    for b in batches:
        src = root / "batches" / b / "translation.json"
        if not src.exists():
            print(f"skip {b}: 无 {src}")
            continue
        dst = outdir / f"review-view-{b}.json"
        n, after, before = build_view(src, dst)
        tot_before += before
        tot_after += after
        print(f"{b}: {n} 条  {before//1024}KB -> {after//1024}KB  -> {dst.name}")

    if tot_before:
        print(f"合计 {tot_before//1024}KB -> {tot_after//1024}KB "
              f"({tot_after*100//tot_before}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
