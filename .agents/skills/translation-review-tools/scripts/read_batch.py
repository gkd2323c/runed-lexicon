#!/usr/bin/env python3
"""读取单个翻译批次的源文/译文对照（只读）。

数据源优先级：
  1. <batch>/translation.json 的 translations[]（含 source/translation/status）
  2. <batch>/map.json + --xml（map 仅有译文，源文从 XML 取）

Usage:
  py -3 read_batch.py --stem Artaeum --batch NI-CELL-002
  py -3 read_batch.py --stem Artaeum --batch INFO-023 --status WAITING,KEEP
  py -3 read_batch.py --stem Artaeum --batch NI-CELL-002 --out _tmp/data/readout.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_xml_sources(xml_path: Path) -> dict[int, str]:
    root = ET.parse(str(xml_path)).getroot()
    return {i: (node.findtext("Source") or "") for i, node in enumerate(root.iter("String"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", required=True, help="Work stem under the work root (e.g. Artaeum)")
    parser.add_argument("--batch", required=True, help="Batch id (e.g. NI-CELL-002)")
    parser.add_argument("--work-root", default=".work", help="Work root directory (default: .work)")
    parser.add_argument("--xml", help="Source XML; required only when the batch has map.json but no translation.json")
    parser.add_argument("--status", help="Only show these statuses, comma-separated (e.g. WAITING,KEEP)")
    parser.add_argument("--out", help="Write the readout to a file instead of stdout")
    args = parser.parse_args()

    batch_dir = resolve(args.work_root) / args.stem / "batches" / args.batch
    if not batch_dir.is_dir():
        print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    result_path = batch_dir / "translation.json"
    map_path = batch_dir / "map.json"

    if result_path.is_file():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        for unit in data.get("translations", []):
            rows.append({
                "idx": unit.get("xml_index"),
                "src": unit.get("source", ""),
                "dst": unit.get("translation", ""),
                "status": unit.get("status", ""),
            })
        source_desc = "translation.json"
    elif map_path.is_file():
        if not args.xml:
            print("error: 批次只有 map.json，需要 --xml 取源文", file=sys.stderr)
            return 2
        sources = load_xml_sources(resolve(args.xml))
        data = json.loads(map_path.read_text(encoding="utf-8"))
        for key, value in data.items():
            idx = int(key)
            rows.append({
                "idx": idx,
                "src": sources.get(idx, ""),
                "dst": value.get("translation", ""),
                "status": value.get("status", ""),
            })
        source_desc = "map.json"
    else:
        print(f"error: 批次目录里既没有 translation.json 也没有 map.json: {batch_dir}", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: r["idx"])
    if args.status:
        wanted = {s.strip().upper() for s in args.status.split(",") if s.strip()}
        rows = [r for r in rows if str(r["status"]).upper() in wanted]

    lines: list[str] = []
    for row in rows:
        flag = f" [{row['status']}]" if row["status"] and row["status"] != "TRANSLATED" else ""
        lines.append(f"[{row['idx']}]{flag} {row['src']}")
        lines.append(f"  -> {row['dst']}")
    output = f"== {args.batch} ({source_desc}, {len(rows)} 条) ==\n" + "\n".join(lines) + "\n"

    if args.out:
        out_path = resolve(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {len(rows)} rows -> {out_path}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
