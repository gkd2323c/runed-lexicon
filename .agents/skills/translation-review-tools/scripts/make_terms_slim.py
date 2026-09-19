# -*- coding: utf-8 -*-
"""生成词表精简视图，供审查子代理替代 39KB 的 DICTIONARY.md。

背景：DICTIONARY.md 有 39KB，读取工具必在中段截断，子代理反复补读会耗尽
时限（ming 连续 3 轮因此失败）。而审查真正需要的只是「定名 + 禁形」，
即 terms.json 的 english / zh / forbidden 三字段——精简后仅约 8KB，一次可读完。

用法:
    python make_terms_slim.py [--terms <terms.json>] [--out <path>]
"""
import argparse
import json
import sys
from pathlib import Path


def build(terms_path: Path, out_path: Path) -> tuple:
    d = json.loads(terms_path.read_text(encoding="utf-8"))
    slim = []
    for x in d.get("terms", []):
        e = {"term": x.get("english", ""), "zh": x.get("zh", "")}
        if x.get("forbidden"):
            e["forbidden"] = x["forbidden"]
        slim.append(e)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(slim, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    return len(slim), out_path.stat().st_size


def main() -> int:
    here = Path(__file__).resolve()
    # .agents/skills/<name>/scripts/make_terms_slim.py -> 上 4 层才是 repo root
    root = here.parents[4]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--terms", default=str(root / "mods/TheKalpicAnomaly.esp/terms.json"))
    ap.add_argument("--out", default=str(root / ".work/TheKalpicAnomaly/notes/terms-slim.json"))
    args = ap.parse_args()

    n, size = build(Path(args.terms), Path(args.out))
    print(f"{n} 条定名 -> {args.out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
