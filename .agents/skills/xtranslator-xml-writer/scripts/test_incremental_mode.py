#!/usr/bin/env python3
"""Permanent regression tests for xtranslator-xml-writer incremental writeback mode.

Covers:
  1. incremental writeback on a canonical baseline: only the target batch rows
     change; previously translated rows survive byte-for-byte.
  2. full-rebuild interception: a source-XML baseline writing over an existing
     canonical is rejected unless --allow-full-rebuild is explicit.
  3. full-rebuild shrink detection: even with --allow-full-rebuild, dropping
     previously translated rows is rejected.
  4. --source-xml validation: result source hash must match it; the baseline
     must derive from that source (EDID/REC/Source columns identical).
  5. idempotent replay exemption on original_dest; manual-correction protection.
  6. --in-place updates the canonical baseline atomically; without it the same
     path is still refused.
  7. --archive-to snapshots the baseline content-addressably before writing.

Synthetic mini-XML only; never touches real project files.
Run: py -3 .agents/skills/xtranslator-xml-writer/scripts/test_incremental_mode.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "write_translations.py"


def build_xml(rows) -> str:
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<SSTXMLRessources version="2">',
        "<Params><Addon>test.esp</Addon><Source>english</Source><Dest>chinese</Dest></Params>",
        "<Content>",
    ]
    for row in rows:
        parts.append(
            '<String List="0">'
            f'<EDID>{row["edid"]}</EDID><REC>{row["rec"]}</REC>'
            f'<Source>{row["source"]}</Source><Dest>{row["dest"]}</Dest>'
            "</String>"
        )
    parts.append("</Content>")
    parts.append("</SSTXMLRessources>")
    return "\n".join(parts) + "\n"


def read_row(xml_path: Path, idx: int) -> dict:
    root = ET.parse(str(xml_path)).getroot()
    node = list(root.iter("String"))[idx]
    return {
        "xml_index": idx,
        "edid": node.findtext("EDID") or "",
        "rec": node.findtext("REC") or "",
        "source": node.findtext("Source") or "",
        "dest": node.findtext("Dest") or "",
    }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_result(path: Path, source_xml: Path, items, recorded_sha=None) -> None:
    sha = recorded_sha if recorded_sha is not None else sha256_of(source_xml)
    translations = []
    for item in items:
        translations.append(
            {
                "translation_unit_id": item.get("unit", f"unit:{item['xml_index']}"),
                "xml_index": item["xml_index"],
                "edid": item["edid"],
                "rec": item["rec"],
                "source": item["source"],
                "original_dest": item["original_dest"],
                "translation": item["translation"],
                "status": item.get("status", "TRANSLATED"),
            }
        )
    doc = {
        "schema_version": 1,
        "purpose": "test",
        "context": {
            "xtranslator_xml": {"path": str(source_xml), "sha256": sha},
        },
        "translations": translations,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8"
    )


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="xmlwriter-incr-test-"))
    try:
        # --- fixture: source (untranslated) + canonical (partially translated)
        src_rows = [
            {"edid": "[A]", "rec": "INFO:NAM1", "source": "Hello there", "dest": "Hello there"},
            {"edid": "[B]", "rec": "INFO:NAM1", "source": "Goodbye", "dest": "Goodbye"},
            {"edid": "[C]", "rec": "INFO:NAM1", "source": "Thanks friend", "dest": "Thanks friend"},
        ]
        source = tmp / "t_english_chinese.xml"
        source.write_text(build_xml(src_rows), encoding="utf-8")

        canon_rows = [
            {**src_rows[0], "dest": "你好啊"},
            dict(src_rows[1]),
            {**src_rows[2], "dest": "谢谢，朋友"},
        ]
        canonical = tmp / "t_english_chinese_translated.xml"
        canonical.write_text(build_xml(canon_rows), encoding="utf-8")

        # --- case 1: incremental writeback on canonical baseline; history kept
        arch = tmp / "archive-snapshot"
        arch.mkdir()
        baseline = arch / "t_english_chinese_translated.xml"
        shutil.copyfile(canonical, baseline)

        row1 = read_row(baseline, 1)
        r1 = tmp / "r1.json"
        write_result(
            r1, source,
            [{**row1, "original_dest": row1["dest"], "translation": "再见"}],
        )
        out1 = tmp / "case1" / "t_english_chinese_translated.xml"
        res = run(
            "--xml", str(baseline), "--source-xml", str(source),
            "--result", str(r1), "--output", str(out1),
        )
        if res.returncode != 0:
            failures.append(f"case1 incremental failed: rc={res.returncode} err={res.stderr.strip()}")
        else:
            text = out1.read_text(encoding="utf-8")
            if "<Dest>再见</Dest>" not in text:
                failures.append(f"case1 new translation missing: {text}")
            if "<Dest>你好啊</Dest>" not in text or "<Dest>谢谢，朋友</Dest>" not in text:
                failures.append(f"case1 history rows lost: {text}")

        # --- case 2a: source baseline over existing canonical is blocked
        res = run(
            "--xml", str(source), "--result", str(r1), "--output", str(canonical), "--force",
        )
        if res.returncode != 2 or "blocked" not in res.stderr.lower():
            failures.append(
                f"case2a interception not triggered: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 2b: --allow-full-rebuild bypasses interception but shrink is rejected
        canonical_before = canonical.read_text(encoding="utf-8")
        res = run(
            "--xml", str(source), "--result", str(r1), "--output", str(canonical),
            "--force", "--allow-full-rebuild",
        )
        if res.returncode != 2 or "shrink" not in res.stderr.lower():
            failures.append(
                f"case2b shrink not detected: rc={res.returncode} err={res.stderr.strip()}"
            )
        if canonical.read_text(encoding="utf-8") != canonical_before:
            failures.append("case2b canonical was modified despite rejection")

        # --- case 2d: interception also fires in --check-only (pre-flight)
        res = run(
            "--xml", str(source), "--result", str(r1), "--output", str(canonical), "--check-only",
        )
        if res.returncode != 2 or "blocked" not in res.stderr.lower():
            failures.append(
                f"case2d check-only interception missing: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 2c: full rebuild with complete batch set succeeds, history preserved
        full_items = []
        for idx in (0, 1, 2):
            row = read_row(canonical, idx)
            row = dict(row)
            translation = {0: "你好啊", 1: "再见", 2: "谢谢，朋友"}[idx]
            full_items.append({**row, "original_dest": row["dest"], "translation": translation})
        # idx0/idx2 original_dest must equal the *source* snapshot the results were made from
        full_items[0]["original_dest"] = src_rows[0]["dest"]
        full_items[2]["original_dest"] = src_rows[2]["dest"]
        rfull = tmp / "rfull.json"
        write_result(rfull, source, full_items)
        res = run(
            "--xml", str(source), "--result", str(rfull), "--output", str(canonical),
            "--force", "--allow-full-rebuild",
        )
        if res.returncode != 0:
            failures.append(f"case2c full rebuild failed: rc={res.returncode} err={res.stderr.strip()}")
        else:
            text = canonical.read_text(encoding="utf-8")
            if "<Dest>再见</Dest>" not in text or "<Dest>你好啊</Dest>" not in text:
                failures.append(f"case2c rebuilt output wrong: {text}")

        # --- case 3a: result source hash must match --source-xml
        tampered = tmp / "tampered.xml"
        tampered.write_text(build_xml([{**r, "dest": "x"} for r in src_rows]), encoding="utf-8")
        res = run(
            "--xml", str(baseline), "--source-xml", str(tampered),
            "--result", str(r1), "--output", str(tmp / "case3a" / "t_english_chinese_translated.xml"),
        )
        if res.returncode != 2 or "mismatch" not in res.stderr.lower():
            failures.append(
                f"case3a tampered source accepted: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 3b: baseline must derive from the declared source
        tampered2_rows = [dict(r) for r in src_rows]
        tampered2_rows[2] = {**tampered2_rows[2], "source": "Thanks stranger"}
        tampered2 = tmp / "tampered2.xml"
        tampered2.write_text(build_xml(tampered2_rows), encoding="utf-8")
        r1b = tmp / "r1b.json"
        write_result(r1b, tampered2, [{**row1, "original_dest": row1["dest"], "translation": "再见"}],
                     recorded_sha=sha256_of(tampered2))
        res = run(
            "--xml", str(baseline), "--source-xml", str(tampered2),
            "--result", str(r1b), "--output", str(tmp / "case3b" / "t_english_chinese_translated.xml"),
        )
        if res.returncode != 2 or "not derived" not in res.stderr.lower():
            failures.append(
                f"case3b baseline/source drift accepted: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 4a: idempotent replay exemption (re-send an already-written batch)
        canon_full_rows = [
            {**src_rows[0], "dest": "你好啊"},
            {**src_rows[1], "dest": "再见"},
            {**src_rows[2], "dest": "谢谢，朋友"},
        ]
        canon_full = tmp / "canon_full.xml"
        canon_full.write_text(build_xml(canon_full_rows), encoding="utf-8")
        row0 = read_row(canon_full, 0)
        r4a = tmp / "r4a.json"
        write_result(
            r4a, source,
            [{**row0, "original_dest": src_rows[0]["dest"], "translation": "你好啊"}],
        )
        res = run(
            "--xml", str(canon_full), "--source-xml", str(source),
            "--result", str(r4a), "--output", str(tmp / "case4a" / "t_english_chinese_translated.xml"),
        )
        if res.returncode != 0:
            failures.append(f"case4a idempotent replay rejected: rc={res.returncode} err={res.stderr.strip()}")

        # --- case 4b: stale intermediate must not clobber a manual correction
        corrected_rows = [dict(r) for r in canon_full_rows]
        corrected_rows[0] = {**corrected_rows[0], "dest": "您好啊"}
        corrected = tmp / "canon_corrected.xml"
        corrected.write_text(build_xml(corrected_rows), encoding="utf-8")
        row0c = read_row(corrected, 0)
        r4b = tmp / "r4b.json"
        write_result(
            r4b, source,
            [{**row0c, "original_dest": src_rows[0]["dest"], "translation": "你好啊"}],
        )
        res = run(
            "--xml", str(corrected), "--source-xml", str(source),
            "--result", str(r4b), "--output", str(tmp / "case4b" / "t_english_chinese_translated.xml"),
        )
        if res.returncode != 2 or "original_dest" not in res.stderr.lower():
            failures.append(
                f"case4b manual correction clobbered: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 5: --in-place updates the canonical baseline
        (tmp / "case5").mkdir()
        inplace = tmp / "case5" / "t_english_chinese_translated.xml"
        shutil.copyfile(canonical, inplace)
        rowi = read_row(inplace, 1)
        res = run(
            "--xml", str(inplace), "--source-xml", str(source),
            "--result", str(r1), "--output", str(inplace), "--in-place",
        )
        if res.returncode != 0:
            failures.append(f"case5 in-place failed: rc={res.returncode} err={res.stderr.strip()}")
        else:
            text = inplace.read_text(encoding="utf-8")
            if "<Dest>再见</Dest>" not in text or "<Dest>你好啊</Dest>" not in text:
                failures.append(f"case5 in-place content wrong: {text}")
        # same path without --in-place is still refused
        res = run(
            "--xml", str(inplace), "--source-xml", str(source),
            "--result", str(r1), "--output", str(inplace),
        )
        if res.returncode != 2 or "in-place" not in res.stderr.lower():
            failures.append(
                f"case5b in-place guard missing: rc={res.returncode} err={res.stderr.strip()}"
            )

        # --- case 6: --archive-to snapshots the baseline before writing
        arch6 = tmp / "arch6"
        before = canonical.read_text(encoding="utf-8")
        res = run(
            "--xml", str(canonical), "--source-xml", str(source),
            "--result", str(r1), "--output", str(tmp / "case6" / "t_english_chinese_translated.xml"),
            "--archive-to", str(arch6),
        )
        if res.returncode != 0:
            failures.append(f"case6 archive-to failed: rc={res.returncode} err={res.stderr.strip()}")
        else:
            snap = arch6 / sha256_of(canonical) / canonical.name
            if not snap.exists():
                failures.append(f"case6 snapshot missing: {snap}")
            elif snap.read_text(encoding="utf-8") != before:
                failures.append("case6 snapshot content mismatch")
        # idempotent second run must not fail on the existing snapshot
        res = run(
            "--xml", str(canonical), "--source-xml", str(source),
            "--result", str(r1), "--output", str(tmp / "case6" / "t_english_chinese_translated.xml"),
            "--archive-to", str(arch6), "--force",
        )
        if res.returncode != 0:
            failures.append(f"case6b rerun failed: rc={res.returncode} err={res.stderr.strip()}")

        # --- case 7: stale-generation baseline that would drop newer translations
        stale_baseline = tmp / "stale_baseline.xml"
        stale_baseline.write_text(
            build_xml([dict(src_rows[0]), dict(src_rows[1]), {**src_rows[2], "dest": "谢谢，朋友"}]),
            encoding="utf-8",
        )
        res = run(
            "--xml", str(stale_baseline), "--source-xml", str(source),
            "--result", str(r1), "--output", str(canonical), "--force",
        )
        if res.returncode != 2 or "shrink" not in res.stderr.lower():
            failures.append(
                f"case7 stale baseline not rejected: rc={res.returncode} err={res.stderr.strip()}"
            )

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("incremental mode smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
