# -*- coding: utf-8 -*-
"""sanitize_translation_map.py — validate & repair a translation map JSON before fill.

!! DEPRECATED / 废弃 (2026-09-04) !!
该工具曾被证实会把 JSON 结构文本泄漏进 translation 值（SB1 项目 1489 条污染）。
不要在新工作中使用；保留仅作历史参考与事故对照。替代做法：写 map 时直接使用
中文引号“”，写完 json.load 验证，错误用 fill_translations.py 定位后手改。

Companion to `fill_translations.py`. A translation map is the Agent's deliverable:
a flat JSON object keyed by xml_index (string) whose values are partial result
items `{translation, status?, confidence?, notes?}`. This tool makes the map safe
for `fill_translations` by catching the authoring mistakes that recur when writing
Chinese wording by hand:

- bare ASCII double-quotes inside a translation value (breaks JSON parsing) are
  converted pairwise to Chinese quotes “ ”;
- missing `status` defaults to TRANSLATED (a warning is printed: the tool cannot
  know whether the Agent meant KEEP / REVIEW from the value alone);
- missing `confidence` defaults to HIGH;
- invalid status / confidence values, missing translation, non-object entries and
  unrepairable JSON are hard errors (exit 2), never silently repaired.

Algorithm: first try a plain JSON parse. If it succeeds, the file is structurally
sound and only semantic defaults / validation apply. If it fails, the file is
re-flowed line-by-line (entries are expected one-per-line, the shape the Agent
writes by hand) and ASCII quotes inside translation values are converted pairwise
to Chinese quotes; the result is re-parsed and then validated.

Usage:
    py -3 .agents/skills/translation-executor/scripts/sanitize_translation_map.py <map.json>
    py -3 .../sanitize_translation_map.py <map.json> --check-only
    py -3 .../sanitize_translation_map.py <map.json> --in-place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VALID_STATUS = {"TRANSLATED", "KEEP", "REVIEW"}
VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

ENTRY_RE = re.compile(
    r'^(?P<indent>\s*)(?P<key>"[^"]+"):\s*(?P<body>\{.*\})(?P<tail>,?\s*)$'
)


class SanitizeError(Exception):
    pass


def fix_value_quotes(val: str) -> str:
    if '"' not in val:
        return val
    out = []
    open_q = True
    for ch in val:
        if ch == '"':
            out.append("\u201c" if open_q else "\u201d")
            open_q = not open_q
        else:
            out.append(ch)
    return "".join(out)


def repair_text(raw: str) -> tuple[str, int]:
    """Line-by-line ASCII-quote repair inside translation values. Returns
    (repaired_text, lines_fixed). Only safe when entries are one-per-line."""
    out = []
    fixed = 0
    for line in raw.splitlines(keepends=True):
        stripped = line.strip()
        m = ENTRY_RE.match(stripped)
        if not m or '"translation":' not in stripped:
            out.append(line)
            continue
        body = m.group("body")
        # translation value: "translation": "<value>", then next field or }.
        tm = re.search(r'"translation":\s*"(.*)"(?=\s*[,}])', body, re.S)
        if tm and '"' in tm.group(1):
            val = tm.group(1)
            newval = fix_value_quotes(val)
            if newval != val:
                body = body[: tm.start(1)] + newval + body[tm.end(1):]
                fixed += 1
        out.append(f'{m.group("indent")}{m.group("key")}: {body}{m.group("tail")}\n')
    return "".join(out), fixed


def validate_and_fill(data, notes) -> list[str]:
    problems = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            problems.append(f"entry {key!r}: value is not an object")
            continue
        tr = entry.get("translation")
        if not isinstance(tr, str) or not tr.strip():
            problems.append(f"entry {key!r}: missing or empty translation")
            continue
        status = entry.get("status")
        if status is None:
            entry["status"] = "TRANSLATED"
            notes.append(f"entry {key!r}: status missing -> TRANSLATED")
        elif status not in VALID_STATUS:
            problems.append(f"entry {key!r}: invalid status {status!r}")
        conf = entry.get("confidence")
        if conf is None:
            entry["confidence"] = "HIGH"
            notes.append(f"entry {key!r}: confidence missing -> HIGH")
        elif conf not in VALID_CONFIDENCE:
            problems.append(f"entry {key!r}: invalid confidence {conf!r}")
        for f in ("notes", "terminology_decisions"):
            if f in entry and entry[f] is None:
                entry[f] = [] if f == "terminology_decisions" else ""
    return problems


def sanitize_text(raw: str, check_only: bool):
    notes = []

    # Path 1: already-valid JSON.
    try:
        data = json.loads(raw)
        repaired_raw = None
        fixed = 0
    except json.JSONDecodeError:
        # Path 2: attempt line-by-line quote repair, then re-parse.
        repaired_raw, fixed = repair_text(raw)
        try:
            data = json.loads(repaired_raw)
        except json.JSONDecodeError as exc:
            raise SanitizeError(
                f"JSON invalid and not repairable by quote fix: {exc}. "
                "Check for unbalanced braces / stray commas / multi-line entries."
            ) from exc

    problems = validate_and_fill(data, notes)
    if problems:
        print("sanitize errors:")
        for p in problems:
            print(f"  - {p}")
        return 2

    if check_only:
        print(f"check OK: {len(data)} entries, {fixed} quote-fixed line(s)")
        for n in notes:
            print(f"  note: {n}")
        return 0

    if fixed == 0 and not notes:
        print(f"map already clean: {len(data)} entries, no changes")
        return 0

    # Serialize canonically so the repaired map is machine-shaped regardless of
    # the input layout (single- or multi-line), then report what changed.
    canonical = json.dumps(data, ensure_ascii=False, indent=1)
    if repaired_raw is not None and fixed:
        print(f"repaired {fixed} line(s) with ASCII quotes -> Chinese quotes")
    for n in notes:
        print(f"  note: {n}")
    return canonical


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate and repair a translation map JSON")
    ap.add_argument("map", help="translation map JSON path")
    ap.add_argument("--check-only", action="store_true", help="report only, do not write")
    ap.add_argument("--in-place", action="store_true", help="rewrite the input file in place")
    ap.add_argument("--output", default=None, help="write repaired map to this path")
    args = ap.parse_args()

    path = Path(args.map)
    raw = path.read_text(encoding="utf-8")

    try:
        result = sanitize_text(raw, args.check_only)
    except SanitizeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if isinstance(result, int):
        return result
    if result is None:
        return 0
    if result == 0:
        return 0

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"wrote {args.output}")
    elif args.in_place:
        path.write_text(result, encoding="utf-8")
        print(f"rewrote {path} in place")
    else:
        print(result)
        print("(dry run — pass --in-place or --output to write)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SanitizeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
