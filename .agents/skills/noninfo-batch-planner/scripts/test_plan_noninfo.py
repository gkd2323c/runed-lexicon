#!/usr/bin/env python3
"""Permanent regression tests for the non-INFO batch planner.

Synthetic mini-XML only; never touches real project files.
Run: py -3 .agents/skills/noninfo-batch-planner/scripts/test_plan_noninfo.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "plan_noninfo_batches.py"


def build_xml(rows) -> str:
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<SSTXMLRessources version="2">',
        "<Params><Addon>t.esp</Addon><Source>english</Source><Dest>chinese</Dest></Params>",
        "<Content>",
    ]
    for row in rows:
        parts.append(
            '<String List="0">'
            f'<EDID>{row.get("edid", "[X]")}</EDID><REC>{row["rec"]}</REC>'
            f'<Source>{row["source"]}</Source><Dest>{row.get("dest", row["source"])}</Dest>'
            "</String>"
        )
    parts.append("</Content>")
    parts.append("</SSTXMLRessources>")
    return "\n".join(parts) + "\n"


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8"
    )


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="noninfo-planner-test-"))
    try:
        work_root = tmp / "work"
        rows = [
            # INFO rows must be excluded
            {"rec": "INFO:NAM1", "source": "Hello there", "edid": "[I1]"},
            # CELL:FULL group A: repeated source with 3 rows (shared translation)
            {"rec": "CELL:FULL", "source": "Psijic Pint", "edid": "[C1]"},
            {"rec": "CELL:FULL", "source": "Psijic Pint", "edid": "[C1]"},
            {"rec": "CELL:FULL", "source": "North Tower", "edid": "[C2]"},
            # already translated row must be excluded
            {"rec": "CELL:FULL", "source": "Old Cell", "dest": "旧地", "edid": "[C3]"},
            # non-INFO family NPC_ with mixed subs
            {"rec": "NPC_:FULL", "source": "Enthyriia the Wandering Witch", "edid": "[N1]"},
            {"rec": "NPC_:SHRT", "source": "Enthyriia", "edid": "[N2]"},
            {"rec": "NPC_:FULL", "source": "Mark", "edid": "[N3]"},
        ]
        xml = tmp / "t_english_chinese.xml"
        xml.write_text(build_xml(rows), encoding="utf-8")

        # 1. dry-run writes nothing
        res = run("--xml", str(xml), "--work-root", str(work_root), "--dry-run")
        if res.returncode != 0:
            failures.append(f"dry-run rc={res.returncode}: {res.stderr.strip()}")
        if work_root.exists():
            failures.append("dry-run wrote files")

        # 2. full run: plan + index.txt
        res = run("--xml", str(xml), "--stem", "T", "--work-root", str(work_root))
        if res.returncode != 0:
            failures.append(f"run rc={res.returncode}: {res.stderr.strip()}")
        else:
            plan_path = work_root / "T" / "context" / "T-noninfo-batches.json"
            if not plan_path.exists():
                failures.append(f"plan missing: {plan_path}")
            else:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                batches = {b["id"]: b for b in plan["batches"]}
                # expect: NI-CELL-001 and NI-NPC-001 (families sorted)
                if set(batches) != {"NI-CELL-001", "NI-NPC-001"}:
                    failures.append(f"unexpected batch ids: {sorted(batches)}")
                cell = batches.get("NI-CELL-001", {})
                # 2 unique sources (Psijic Pint dup collapsed), 3 rows
                if cell.get("unique_src") != 2 or cell.get("row_count") != 3:
                    failures.append(
                        f"cell batch counts wrong: unique={cell.get('unique_src')} rows={cell.get('row_count')}")
                # repeated source stays inside one batch
                if "Psijic Pint" not in cell.get("srcs", []):
                    failures.append("Psijic Pint missing from cell batch")
                npc = batches.get("NI-NPC-001", {})
                if npc.get("unique_src") != 3 or npc.get("row_count") != 3:
                    failures.append(
                        f"npc batch counts wrong: unique={npc.get('unique_src')} rows={npc.get('row_count')}")
                # INFO / translated rows excluded
                all_srcs = [s for b in plan["batches"] for s in b["srcs"]]
                if "Hello there" in all_srcs or "Old Cell" in all_srcs:
                    failures.append(f"excluded rows leaked: {all_srcs}")

            # index.txt matches idx
            for bid, batch in batches.items():
                idx_path = work_root / "T" / "batches" / bid / "index.txt"
                if not idx_path.exists():
                    failures.append(f"index.txt missing for {bid}")
                    continue
                got = [int(x) for x in idx_path.read_text(encoding="utf-8").split()]
                if sorted(got) != sorted(batch["idx"]):
                    failures.append(f"{bid} index.txt mismatch: {got} vs {batch['idx']}")

        # 3. max-unique forces splitting (2 sources per batch -> ceil split)
        res = run("--xml", str(xml), "--stem", "T2", "--work-root", str(work_root),
                  "--max-unique", "2", "--max-rows", "100")
        if res.returncode != 0:
            failures.append(f"split run rc={res.returncode}: {res.stderr.strip()}")
        else:
            plan = json.loads((work_root / "T2" / "context" / "T2-noninfo-batches.json")
                              .read_text(encoding="utf-8"))
            cell_batches = [b for b in plan["batches"] if b["id"].startswith("NI-CELL-")]
            # Cell has 2 unique sources -> fits in exactly one batch at max-unique=2? 
            # No: 2 <= 2, so one batch. NPC has 3 unique -> 2 batches.
            npc_batches = [b for b in plan["batches"] if b["id"].startswith("NI-NPC-")]
            if len(cell_batches) != 1:
                failures.append(f"cell split unexpected: {len(cell_batches)}")
            if len(npc_batches) != 2:
                failures.append(f"npc split expected 2, got {len(npc_batches)}")
            # source never splits across batches
            seen: dict[str, str] = {}
            for b in plan["batches"]:
                for s in b["srcs"]:
                    if s in seen and seen[s] != b["id"]:
                        failures.append(f"source {s} split across batches")
                    seen[s] = b["id"]

        # 4. oversized single group is still emitted whole
        rows2 = [
            {"rec": "CELL:FULL", "source": "Duplicate", "edid": "[D1]"},
            {"rec": "CELL:FULL", "source": "Duplicate", "edid": "[D1]"},
            {"rec": "CELL:FULL", "source": "Duplicate", "edid": "[D1]"},
            {"rec": "CELL:FULL", "source": "Other", "edid": "[D2]"},
        ]
        xml2 = tmp / "t2_english_chinese.xml"
        xml2.write_text(build_xml(rows2), encoding="utf-8")
        res = run("--xml", str(xml2), "--stem", "T3", "--work-root", str(work_root),
                  "--max-unique", "1", "--max-rows", "2")
        if res.returncode != 0:
            failures.append(f"oversize run rc={res.returncode}: {res.stderr.strip()}")
        else:
            plan = json.loads((work_root / "T3" / "context" / "T3-noninfo-batches.json")
                              .read_text(encoding="utf-8"))
            first = plan["batches"][0]
            if first["unique_src"] != 1 or first["row_count"] != 3:
                failures.append(
                    f"oversized group handling wrong: {first['unique_src']}/{first['row_count']}")

        # 5. REC filter
        res = run("--xml", str(xml), "--stem", "T4", "--work-root", str(work_root),
                  "--recs", "CELL:FULL")
        if res.returncode != 0:
            failures.append(f"filter run rc={res.returncode}: {res.stderr.strip()}")
        else:
            plan = json.loads((work_root / "T4" / "context" / "T4-noninfo-batches.json")
                              .read_text(encoding="utf-8"))
            ids = [b["id"] for b in plan["batches"]]
            if any(not i.startswith("NI-CELL-") for i in ids):
                failures.append(f"filter leaked families: {ids}")

        # 6. determinism: rerun produces identical plan bytes
        plan_a = (work_root / "T" / "context" / "T-noninfo-batches.json").read_bytes()
        res = run("--xml", str(xml), "--stem", "T", "--work-root", str(work_root))
        plan_b = (work_root / "T" / "context" / "T-noninfo-batches.json").read_bytes()
        if res.returncode != 0 or plan_a != plan_b:
            failures.append("determinism check failed")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("non-INFO planner smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
