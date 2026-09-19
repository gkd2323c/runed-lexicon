# -*- coding: utf-8 -*-
"""为审查子代理生成精简视图，从根本消除「文件截断」。

背景：translation.json 是 indent=2 的美化 JSON，大批量（80+ 条）可达
40KB / 1200+ 行，读取工具按行截断，子代理只能靠反复补读拼全，既慢又易漏。

审查只需要 xml_index / source / translation 三字段。精简 + 紧凑后体积约为
原来的 1/5（40KB -> 8KB），一次可读完。

用法:
    python make_review_view.py INFO-147 [INFO-148 ...] [--out DIR]
    python make_review_view.py --batch-file batches.txt

输出: .work/<MOD>/notes/review-view-<ID>.json （单批）
      或 --out 指定目录下 review-view-<ID>.json
"""
import argparse
import json
import sys
from pathlib import Path

FIELDS = ("xml_index", "source", "translation")


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
