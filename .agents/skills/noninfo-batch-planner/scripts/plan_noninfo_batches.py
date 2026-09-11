#!/usr/bin/env python3
"""Plan deterministic non-INFO translation batches for a Skyrim MOD XML.

Scans the source xTranslator XML for untranslated rows (Source == Dest,
non-empty, REC not starting with INFO), groups them by record family
(REC prefix, e.g. CELL / NPC_ / QUST), and packs each family into batches.

Packing is source-group greedy: all XML rows sharing one Source are an
indivisible unit (they share a translation), so a Source never splits
across batches. A batch closes when adding the next Source group would
exceed --max-unique distinct sources or --max-rows XML rows (a single
oversized group is still emitted whole).

Outputs:
  .work/<stem>/context/<stem>-noninfo-batches.json   (batch plan; batches[].id/idx
                                                      schema compatible with
                                                      verify_subagent_batch.py)
  .work/<stem>/batches/NI-<family>-<NNN>/index.txt   (one integer per line)

Read-only with respect to MOD XML; overwrite-style deterministic outputs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def text_of(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_untranslated(xml_path: Path, rec_filter: set[str]) -> list[dict]:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in {xml_path}: {exc}") from exc

    rows = []
    for index, node in enumerate(root.findall("./Content/String")):
        source = text_of(node.find("Source"))
        dest = text_of(node.find("Dest"))
        rec = text_of(node.find("REC"))
        if not source or source != dest:
            continue
        if rec.startswith("INFO"):
            continue
        if rec_filter and rec not in rec_filter:
            continue
        rows.append({"index": index, "rec": rec, "source": source})
    return rows


def group_by_source(rows: list[dict]) -> list[tuple[str, list[int], str]]:
    """Group rows into (source, [idx...], first_rec) units, ordered by first
    occurrence. Rows of one Source keep XML order inside the unit."""
    order: list[str] = []
    by_source: dict[str, list[int]] = {}
    first_rec: dict[str, str] = {}
    for row in rows:
        src = row["source"]
        if src not in by_source:
            order.append(src)
            by_source[src] = []
            first_rec[src] = row["rec"]
        by_source[src].append(row["index"])
    return [(src, by_source[src], first_rec[src]) for src in order]


def pack_groups(
    groups: list[tuple[str, list[int], str]],
    max_unique: int,
    max_rows: int,
) -> list[dict]:
    batches = []
    cur_srcs: list[str] = []
    cur_idx: list[int] = []
    cur_recs: set[str] = set()
    cur_rows = 0

    def flush():
        nonlocal cur_srcs, cur_idx, cur_recs, cur_rows
        if cur_srcs:
            batches.append({
                "srcs": cur_srcs,
                "idx": sorted(cur_idx),
                "recs": sorted(cur_recs),
            })
        cur_srcs, cur_idx, cur_recs, cur_rows = [], [], set(), 0

    for src, idxs, rec in groups:
        would_unique = len(cur_srcs) + 1
        would_rows = cur_rows + len(idxs)
        if cur_srcs and (would_unique > max_unique or would_rows > max_rows):
            flush()
        cur_srcs.append(src)
        cur_idx.extend(idxs)
        cur_recs.add(rec)
        cur_rows += len(idxs)
    flush()
    return batches


def family_slug(rec: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", rec.split(":", 1)[0]).upper() or "GEN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, help="Source xTranslator XML")
    parser.add_argument("--stem", default=None,
                        help="Work stem under .work/ (default: MOD dir name without extension)")
    parser.add_argument("--work-root", default=".work",
                        help="Work root directory (default: .work)")
    parser.add_argument("--recs", default=None,
                        help="Comma-separated REC filter (exact, e.g. 'CELL:FULL,NPC_:FULL'); "
                             "default = all non-INFO")
    parser.add_argument("--max-unique", type=int, default=60,
                        help="Max distinct sources per batch (default 60)")
    parser.add_argument("--max-rows", type=int, default=150,
                        help="Max XML rows per batch (default 150)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print statistics only; write nothing")
    args = parser.parse_args()

    try:
        xml_path = resolve_path(args.xml)
        if not xml_path.is_file():
            raise ValueError(f"XML does not exist: {xml_path}")
        stem = args.stem or (xml_path.parent.name.rsplit(".", 1)[0]
                             if xml_path.parent.name else xml_path.stem)
        work_root = Path(args.work_root)
        if not work_root.is_absolute():
            work_root = PROJECT_ROOT / work_root
        work_root = work_root.resolve()
        rec_filter = set()
        if args.recs:
            rec_filter = {r.strip() for r in args.recs.split(",") if r.strip()}

        rows = load_untranslated(xml_path, rec_filter)
        if not rows:
            raise ValueError("No untranslated non-INFO rows matched the filter")

        # family = REC prefix; families processed in sorted order for determinism
        families: dict[str, list[dict]] = {}
        for row in rows:
            families.setdefault(row["rec"].split(":", 1)[0], []).append(row)

        plan_batches = []
        lines = []
        for family in sorted(families):
            groups = group_by_source(families[family])
            packed = pack_groups(groups, args.max_unique, args.max_rows)
            slug = family_slug(family)
            for seq, batch in enumerate(packed, start=1):
                bid = f"NI-{slug}-{seq:03d}"
                plan_batches.append({
                    "id": bid,
                    "line": family,
                    "recs": batch["recs"],
                    "unique_src": len(batch["srcs"]),
                    "row_count": len(batch["idx"]),
                    "srcs": batch["srcs"],
                    "idx": batch["idx"],
                })
                lines.append(f"  {bid}: {len(batch['srcs'])} unique / {len(batch['idx'])} rows")

        plan = {
            "schema_version": 1,
            "source_xml": str(xml_path.relative_to(PROJECT_ROOT))
                          if xml_path.is_relative_to(PROJECT_ROOT) else str(xml_path),
            "selection": {
                "rule": "source_equals_dest_and_source_nonempty_and_rec_not_INFO",
                "recs": sorted(rec_filter) if rec_filter else "all-non-INFO",
                "max_unique": args.max_unique,
                "max_rows": args.max_rows,
            },
            "batches": plan_batches,
        }

        total_unique = sum(b["unique_src"] for b in plan_batches)
        total_rows = sum(b["row_count"] for b in plan_batches)
        print(f"families: {len(families)}, batches: {len(plan_batches)}, "
              f"unique: {total_unique}, rows: {total_rows}")
        print("\n".join(lines))

        if args.dry_run:
            print("(dry-run: nothing written)")
            return 0

        plan_path = work_root / stem / "context" / f"{stem}-noninfo-batches.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        try:
            shown = plan_path.relative_to(PROJECT_ROOT)
        except ValueError:
            shown = plan_path
        print(f"plan -> {shown}")

        for batch in plan_batches:
            outdir = work_root / stem / "batches" / batch["id"]
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "index.txt").write_text(
                "".join(f"{i}\n" for i in batch["idx"]), encoding="utf-8")
        print(f"index.txt written for {len(plan_batches)} batches")
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
