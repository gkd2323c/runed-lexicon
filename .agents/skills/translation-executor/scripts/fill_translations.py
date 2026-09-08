# -*- coding: utf-8 -*-
"""fill_translations.py — apply an Agent's translation map to a translation-result JSON.

Deterministic helper for `translation-executor`: takes a result JSON produced by
`translation_result.py init` plus a flat translation map, and fills every PENDING
item whose xml_index appears in the map.

The translation map is a plain JSON object keyed by xml_index (integer as string):

    {
      "813": {"translation": "<grunt>", "status": "KEEP", "confidence": "HIGH",
              "notes": "拟声标签，保留原文"},
      "1313": {"translation": "祈祷吧！你就要去见你的神了。", "status": "TRANSLATED",
               "confidence": "HIGH", "notes": ""}
    }

Rules:
- Only PENDING items are touched. A map key whose item is already TRANSLATED /
  KEEP / REVIEW is reported and skipped (use --overwrite to replace it).
- A map key not present in the result is an error (default) — it means the map
  and the result disagree on batch scope.
- Optional fields in a map entry (confidence / notes / terminology_decisions /
  waived_tokens) fall back to empty / [] when omitted; translation and status
  are required. waived_tokens (R16: Agent-endorsed protected-token waivers)
  is passed through to the result item for validator/gate consumption.
- status must be one of TRANSLATED / KEEP / REVIEW; PENDING is not accepted from
  the map. KEEP items must have translation == source (validator enforces this
  later, but the filler warns now to catch it early).
- The output is written to a new path (never in place) unless --output equals
  the input, which requires --force.

Two keying modes:
- Default: map keyed by xml_index (integer as string). Exact, index-stable;
  use when the result scope is fixed and you can reference indexes reliably.
- --by-source: map keyed by the exact Source text. The map becomes readable
  (the English line is the key) and immune to the 1-based/0-based index
  confusion between skyrim-xml-tools (1-based) and context/executor/writer
  (0-based). A source that matches zero items, or more than one PENDING item
  (duplicate line), is an error — duplicates must use the index-keyed mode.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_STATUS = {"TRANSLATED", "KEEP", "REVIEW"}
VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


class FillError(Exception):
    pass


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FillError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FillError(f"{path}: expected a JSON object")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a translation map to a result JSON")
    ap.add_argument("--result", required=True, help="result JSON from translation_result.py init")
    ap.add_argument("--map", required=True, help="translation map JSON (keyed by xml_index, or by source with --by-source)")
    ap.add_argument("--output", required=True, help="output result JSON path")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace existing non-PENDING translations for mapped keys")
    ap.add_argument("--by-source", action="store_true",
                    help="interpret map keys as exact Source text instead of xml_index")
    ap.add_argument("--force", action="store_true",
                    help="allow overwriting an existing --output file")
    args = ap.parse_args()

    result_path = Path(args.result)
    map_path = Path(args.map)
    output_path = Path(args.output)

    result = load_json(result_path)
    mapping = load_json(map_path)

    translations = result.get("translations")
    if not isinstance(translations, list):
        raise FillError(f"{result_path}: no 'translations' array")

    by_index = {}
    for item in translations:
        idx = item.get("xml_index")
        if idx is not None:
            by_index[str(idx)] = item
    # translation_unit_id fallback for items without xml_index
    for item in translations:
        if item.get("xml_index") is None and item.get("translation_unit_id"):
            by_index.setdefault(item["translation_unit_id"], item)

    # --by-source: build source -> [items] index; reject only when a *mapped*
    # source is ambiguous (the batch may legitimately contain other duplicates)
    by_source: dict[str, list] = {}
    if args.by_source:
        for item in translations:
            by_source.setdefault(item.get("source", ""), []).append(item)

    problems = []
    filled = 0
    skipped = 0
    for key, entry in mapping.items():
        if not isinstance(entry, dict):
            problems.append(f"map key {key!r}: value is not an object")
            continue
        if args.by_source:
            matches = by_source.get(key)
            if not matches:
                problems.append(f"map key {key!r}: no matching source in result")
                continue
            if len(matches) > 1:
                problems.append(
                    f"map key {key!r}: source 在 result 中重复（{len(matches)} 条），"
                    f"无法确定填哪条，请改用默认 xml_index 模式"
                )
                continue
            item = matches[0]
        else:
            item = by_index.get(str(key))
            if item is None:
                problems.append(f"map key {key!r}: no matching translation item in result")
                continue
        status = entry.get("status")
        translation = entry.get("translation")
        if status not in VALID_STATUS:
            problems.append(f"map key {key!r}: invalid status {status!r}")
            continue
        if translation is None:
            problems.append(f"map key {key!r}: missing translation")
            continue
        if item.get("status") != "PENDING" and not args.overwrite:
            skipped += 1
            continue
        if "expected_translation" in entry and item.get("translation") != entry["expected_translation"]:
            problems.append(f"map key {key!r}: expected_translation mismatch; result has changed")
            continue
        if not isinstance(translation, str):
            problems.append(f"map key {key!r}: translation must be a string")
            continue
        item["translation"] = translation
        item["status"] = status
        item["confidence"] = entry.get("confidence", "HIGH")
        conf = item["confidence"]
        if conf not in VALID_CONFIDENCE:
            problems.append(f"map key {key!r}: invalid confidence {conf!r}")
        item["notes"] = entry.get("notes", "")
        td = entry.get("terminology_decisions")
        item["terminology_decisions"] = td if isinstance(td, list) else []
        wt = entry.get("waived_tokens")
        if wt is not None:
            if not isinstance(wt, list) or any(not isinstance(w, str) for w in wt):
                problems.append(f"map key {key!r}: waived_tokens must be an array of strings")
                continue
            item["waived_tokens"] = wt
        if status == "KEEP" and translation != item.get("source"):
            problems.append(
                f"map key {key!r}: KEEP translation must equal source "
                f"({item.get('source')!r} != {translation!r})"
            )
        filled += 1

    if problems:
        print("fill errors:")
        for p in problems:
            print("  -", p)
        return 2

    if output_path.exists() and not args.force:
        raise FillError(f"output exists: {output_path}; use --force to replace")

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    remaining = sum(1 for it in translations if it.get("status") == "PENDING")
    print(f"filled {filled}, skipped (already done) {skipped}, remaining PENDING {remaining}")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
