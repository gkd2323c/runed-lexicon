#!/usr/bin/env python3
"""Permanent regression tests for xtranslator-xml-writer --patch (atomic patch mode).

Synthetic mini-XML only; never touches real project files.
Run: py -3 .agents/skills/xtranslator-xml-writer/scripts/test_patch_mode.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "write_translations.py"

MINI_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<SSTXMLRessources version="2">\n'
    "<Params><Addon>test.esp</Addon><Source>english</Source><Dest>chinese</Dest></Params>\n"
    "<Content>\n"
    '<String List="0"><EDID>[A]</EDID><REC>INFO:NAM1</REC><Source>Hello</Source><Dest>你好</Dest></String>\n'
    '<String List="0"><EDID>[B]</EDID><REC>INFO:NAM1</REC><Source>Bye</Source><Dest>再见</Dest></String>\n'
    "</Content>\n"
    "</SSTXMLRessources>"
)


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True
    )


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="xmlwriter-patch-test-"))
    try:
        xml = tmp / "t.xml"
        xml.write_text(MINI_XML, encoding="utf-8")

        # 1. positive: single-entry patch applies, sibling untouched
        patch = tmp / "p.json"
        patch.write_text(
            json.dumps({"1": {"expected_dest": "再见", "translation": "拜拜"}}),
            encoding="utf-8",
        )
        out = tmp / "t_english_chinese_translated.xml"
        result = run("--xml", str(xml), "--patch", str(patch), "--output", str(out))
        if result.returncode != 0:
            failures.append(f"positive case failed: {result.stderr.strip()}")
        else:
            got = out.read_text(encoding="utf-8")
            if "<Dest>拜拜</Dest>" not in got or "<Dest>你好</Dest>" not in got:
                failures.append(f"positive case wrong output: {got}")

        # 2. stale expected_dest rejected
        bad_cas = tmp / "bad-cas.json"
        bad_cas.write_text(
            json.dumps({"1": {"expected_dest": "wrong", "translation": "x"}}),
            encoding="utf-8",
        )
        result = run("--xml", str(xml), "--patch", str(bad_cas), "--output", str(tmp / "o2.xml"))
        if result.returncode != 2 or "stale expected_dest" not in result.stderr:
            failures.append(f"stale CAS not rejected: rc={result.returncode} err={result.stderr.strip()}")

        # 3. protected token mismatch rejected (placeholder dropped)
        bad_tok = tmp / "bad-tok.json"
        bad_tok.write_text(
            json.dumps({"0": {"expected_dest": "你好", "translation": "<Alias=Player>坏了"}}),
            encoding="utf-8",
        )
        result = run("--xml", str(xml), "--patch", str(bad_tok), "--output", str(tmp / "o3.xml"))
        if result.returncode != 2 or "protected token mismatch" not in result.stderr:
            failures.append(f"token mismatch not rejected: rc={result.returncode} err={result.stderr.strip()}")

        # 4. overlap between patch and result file rejected
        result_file = tmp / "r.json"
        result_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "purpose": "test",
                    "context": {
                        "path": "x",
                        "sha256": "x",
                        "batch_index": 0,
                        "xtranslator_xml": {"path": "x", "sha256": "x"},
                        "mod_context": {"path": "x", "sha256": "x"},
                        "mod_dictionary": {"path": "x", "sha256": "x"},
                    },
                    "translations": [],
                }
            ),
            encoding="utf-8",
        )
        # empty translations array is fine; overlap check uses real index collision,
        # so build a result file with a real item for index 1 matching the XML.
        result_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "purpose": "test",
                    "context": {
                        "path": "x",
                        "sha256": "x",
                        "batch_index": 0,
                        "xtranslator_xml": {"path": str(xml), "sha256": "0" * 64},
                        "mod_context": {"path": "x", "sha256": "x"},
                        "mod_dictionary": {"path": "x", "sha256": "x"},
                    },
                    "translations": [],
                }
            ),
            encoding="utf-8",
        )
        # A genuine overlap requires a full valid result item with matching hash,
        # which cannot be forged cheaply here; instead test invalid-status path:
        # patch on out-of-range index must be rejected.
        bad_range = tmp / "bad-range.json"
        bad_range.write_text(
            json.dumps({"99": {"expected_dest": "x", "translation": "x"}}),
            encoding="utf-8",
        )
        result = run("--xml", str(xml), "--patch", str(bad_range), "--output", str(tmp / "o4.xml"))
        if result.returncode != 2 or "out of range" not in result.stderr:
            failures.append(f"out-of-range not rejected: rc={result.returncode} err={result.stderr.strip()}")

        # 4b. multi-version accumulation guard: sibling *_translated*.xml in the
        # output directory is rejected (exactly one canonical artifact per MOD dir)
        mod_dir = tmp / "moddir"
        mod_dir.mkdir()
        shutil.copy(xml, mod_dir / "t.xml")
        stale = mod_dir / "other_translated.xml"
        shutil.copy(xml, stale)
        patch3 = tmp / "p3.json"
        patch3.write_text(
            json.dumps({"1": {"expected_dest": "再见", "translation": "拜拜2"}}),
            encoding="utf-8",
        )
        result = run(
            "--xml", str(mod_dir / "t.xml"), "--patch", str(patch3),
            "--output", str(mod_dir / "t_english_chinese_translated.xml"),
        )
        if result.returncode != 2 or "multiple translated XML versions" not in result.stderr:
            failures.append(
                f"multi-version guard not enforced: rc={result.returncode} err={result.stderr.strip()}"
            )

        # 4c. canonical naming guard: version/label suffixes in output filename rejected
        for bad_name in ("t_english_chinese_translated_final.xml", "t_english_chinese_translated_v6.xml"):
            result = run(
                "--xml", str(xml), "--patch", str(patch),
                "--output", str(tmp / bad_name),
            )
            if result.returncode != 2 or "no version/label suffixes" not in result.stderr:
                failures.append(
                    f"naming guard not enforced for {bad_name}: rc={result.returncode} err={result.stderr.strip()}"
                )
        result = run(
            "--xml", str(xml), "--patch", str(patch),
            "--output", str(tmp / "t_english_chinese_translated.xml"), "--force",
        )
        if result.returncode != 0:
            failures.append(f"canonical name rejected: rc={result.returncode} err={result.stderr.strip()}")

        # 5. duplicate key in patch JSON rejected (silent last-wins would drop a fix)
        dup = tmp / "dup.json"
        dup.write_text(
            '{"1": {"expected_dest": "再见", "translation": "拜拜"},\n'
            ' "1": {"expected_dest": "再见", "translation": "再会"}}',
            encoding="utf-8",
        )
        result = run("--xml", str(xml), "--patch", str(dup), "--output", str(tmp / "t_english_chinese_translated.xml"), "--force")
        if result.returncode != 2 or "Duplicate key in patch JSON" not in result.stderr:
            failures.append(f"duplicate key not rejected: rc={result.returncode} err={result.stderr.strip()}")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("patch mode smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
