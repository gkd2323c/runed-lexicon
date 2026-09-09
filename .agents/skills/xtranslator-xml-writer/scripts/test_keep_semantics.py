#!/usr/bin/env python3
"""Regression tests for KEEP semantics in xtranslator-xml-writer.

Contract: a KEEP unit's final <Dest> must equal its <Source>. Therefore a KEEP
unit whose current <Dest> holds a stale/wrong translation must be rewritten back
to Source; a KEEP unit already equal to Source must stay a no-op.

Synthetic mini-XML only; never touches real project files.
Run: py -3 .agents/skills/xtranslator-xml-writer/scripts/test_keep_semantics.py
"""

from __future__ import annotations

import hashlib
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
    '<String List="0"><EDID>[B]</EDID><REC>INFO:NAM1</REC><Source>Bye</Source><Dest>错误旧译文</Dest></String>\n'
    '<String List="0"><EDID>[C]</EDID><REC>INFO:NAM1</REC><Source>Tag</Source><Dest>Tag</Dest></String>\n'
    "</Content>\n"
    "</SSTXMLRessources>"
)


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True
    )


def make_result(xml: Path, items: list[dict]) -> dict:
    digest = hashlib.sha256(xml.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "purpose": "test",
        "context": {
            "path": "x",
            "sha256": "x",
            "batch_index": 0,
            "xtranslator_xml": {"path": str(xml), "sha256": digest},
            "mod_context": {"path": "x", "sha256": "x"},
            "mod_dictionary": {"path": "x", "sha256": "x"},
        },
        "translations": items,
    }


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="xmlwriter-keep-test-"))
    try:
        xml = tmp / "t.xml"
        xml.write_text(MINI_XML, encoding="utf-8")

        # 1. RED case: KEEP whose Dest holds a stale wrong translation must be
        #    rewritten back to Source (index 1: Dest='错误旧译文', Source='Bye').
        res1 = tmp / "r1.json"
        res1.write_text(
            json.dumps(
                make_result(
                    xml,
                    [
                        {
                            "translation_unit_id": "B",
                            "xml_index": 1,
                            "edid": "[B]",
                            "rec": "INFO:NAM1",
                            "source": "Bye",
                            "original_dest": "错误旧译文",
                            "translation": "Bye",
                            "status": "KEEP",
                        }
                    ],
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out1 = tmp / "t_english_chinese_translated.xml"
        result = run("--xml", str(xml), "--result", str(res1), "--output", str(out1))
        if result.returncode != 0:
            failures.append(f"keep-fix case failed: {result.stderr.strip()}")
        else:
            got = out1.read_text(encoding="utf-8")
            if "<Dest>Bye</Dest>" not in got:
                failures.append(
                    "keep-fix case: stale Dest not rewritten to Source; got="
                    + repr(got.split("<Content>")[1][:200])
                )
            if "<Dest>错误旧译文</Dest>" in got:
                failures.append("keep-fix case: stale translation still present")

        # 2. no-op case: KEEP already equal to Source stays untouched (index 2)
        res2 = tmp / "r2.json"
        res2.write_text(
            json.dumps(
                make_result(
                    xml,
                    [
                        {
                            "translation_unit_id": "C",
                            "xml_index": 2,
                            "edid": "[C]",
                            "rec": "INFO:NAM1",
                            "source": "Tag",
                            "original_dest": "Tag",
                            "translation": "Tag",
                            "status": "KEEP",
                        }
                    ],
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out2 = tmp / "t2_english_chinese_translated.xml"
        out1.unlink(missing_ok=True)  # multi-version guard: one canonical per dir
        result = run("--xml", str(xml), "--result", str(res2), "--output", str(out2))
        if result.returncode != 0:
            failures.append(f"keep-noop case failed: {result.stderr.strip()}")
        elif "<Dest>Tag</Dest>" not in out2.read_text(encoding="utf-8"):
            failures.append("keep-noop case: Dest changed unexpectedly")

        # 3. KEEP with translation != source must still be rejected
        res3 = tmp / "r3.json"
        res3.write_text(
            json.dumps(
                make_result(
                    xml,
                    [
                        {
                            "translation_unit_id": "C",
                            "xml_index": 2,
                            "edid": "[C]",
                            "rec": "INFO:NAM1",
                            "source": "Tag",
                            "original_dest": "Tag",
                            "translation": "标签",
                            "status": "KEEP",
                        }
                    ],
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = run("--xml", str(xml), "--result", str(res3), "--output", str(tmp / "t3_english_chinese_translated.xml"))
        if result.returncode != 2 or "KEEP translation must equal source" not in result.stderr:
            failures.append(
                f"keep-mismatch not rejected: rc={result.returncode} err={result.stderr.strip()}"
            )

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("keep semantics tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
