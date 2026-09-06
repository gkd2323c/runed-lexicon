#!/usr/bin/env python3
"""Initialize and validate structured Skyrim translation draft results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
RESULT_SCHEMA_VERSION = 1
ALLOWED_STATUSES = {"PENDING", "TRANSLATED", "REVIEW", "KEEP"}
ALLOWED_CONFIDENCE = {"", "HIGH", "MEDIUM", "LOW"}

ANGLE_TOKEN_RE = re.compile(r"<[^<>\r\n]+>")
PRINTF_TOKEN_RE = re.compile(
    r"%(?:\d+\$)?[-+#0']*\d*(?:\.\d+)?[diuoxXfFeEgGaAcspn%]"
)
BRACE_TOKEN_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.:=|+\- ]*\}")
BRACKET_TOKEN_RE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_.:=|+\- ]*\]")
ESCAPE_TOKEN_RE = re.compile(r"\\[nrt\\]")


class TranslationResultError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise TranslationResultError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise TranslationResultError(f"Invalid {label} JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TranslationResultError(f"{label} root must be a JSON object: {path}")
    return value


def get_batch(context: dict[str, Any], batch_index: int) -> dict[str, Any]:
    if context.get("schema_version") != 1:
        raise TranslationResultError(
            f"Unsupported translation context schema_version: {context.get('schema_version')!r}"
        )
    batches = context.get("batches")
    if not isinstance(batches, list):
        raise TranslationResultError("Translation context is missing batches array")
    for batch in batches:
        if isinstance(batch, dict) and batch.get("batch_index") == batch_index:
            entries = batch.get("entries")
            if not isinstance(entries, list):
                raise TranslationResultError(f"Batch {batch_index} is missing entries array")
            return batch
    raise TranslationResultError(f"Batch index not found in translation context: {batch_index}")


def protected_tokens(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for regex in (
        ANGLE_TOKEN_RE,
        PRINTF_TOKEN_RE,
        BRACE_TOKEN_RE,
        BRACKET_TOKEN_RE,
        ESCAPE_TOKEN_RE,
    ):
        for match in regex.finditer(text):
            matches.append((match.start(), match.group(0)))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [token for _, token in matches]


def review_reasons(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    dialogue = entry.get("dialogue_context")
    if isinstance(dialogue, dict) and dialogue.get("status") == "unmatched":
        reasons.append("dialogue_context_unmatched")

    terminology = entry.get("terminology")
    if not isinstance(terminology, dict):
        return reasons

    mod_hits = terminology.get("mod_dictionary_hits")
    if isinstance(mod_hits, list):
        for hit in mod_hits:
            if not isinstance(hit, dict):
                continue
            if str(hit.get("status", "")).upper() == "REVIEW":
                english = str(hit.get("english", "")).strip()
                reasons.append(f"mod_term_review:{english}" if english else "mod_term_review")

    official_hits = terminology.get("official_dictionary_hits")
    if isinstance(official_hits, list):
        destinations: dict[str, set[str]] = {}
        for hit in official_hits:
            if not isinstance(hit, dict):
                continue
            source = str(hit.get("source", "")).strip()
            dest = str(hit.get("dest", "")).strip()
            if source and dest:
                destinations.setdefault(source.casefold(), set()).add(dest)
        for source_key, values in destinations.items():
            if len(values) > 1:
                reasons.append(f"official_term_conflict:{source_key}")
    return reasons


def context_provenance(context_path: Path, context: dict[str, Any], batch_index: int) -> dict[str, Any]:
    inputs = context.get("inputs")
    if not isinstance(inputs, dict):
        raise TranslationResultError("Translation context is missing inputs object")

    xml = inputs.get("xtranslator_xml")
    mod_context = inputs.get("mod_context")
    mod_dictionary = inputs.get("mod_dictionary")
    if not all(isinstance(item, dict) for item in (xml, mod_context, mod_dictionary)):
        raise TranslationResultError(
            "Translation context must contain xtranslator_xml, mod_context, and mod_dictionary metadata"
        )

    return {
        "path": relative(context_path),
        "sha256": sha256_file(context_path),
        "batch_index": batch_index,
        "xtranslator_xml": {
            "path": xml.get("path", ""),
            "sha256": xml.get("sha256", ""),
        },
        "mod_context": {
            "path": mod_context.get("path", ""),
            "sha256": mod_context.get("sha256", ""),
        },
        "mod_dictionary": {
            "path": mod_dictionary.get("path", ""),
            "sha256": mod_dictionary.get("sha256", ""),
        },
    }


def build_result_template(context_path: Path, context: dict[str, Any], batch_index: int) -> dict[str, Any]:
    batch = get_batch(context, batch_index)
    translations: list[dict[str, Any]] = []

    for entry in batch["entries"]:
        if not isinstance(entry, dict):
            raise TranslationResultError(f"Batch {batch_index} contains a non-object entry")
        unit_id = str(entry.get("translation_unit_id", ""))
        xml = entry.get("xml")
        if not unit_id or not isinstance(xml, dict):
            raise TranslationResultError(
                f"Batch {batch_index} contains an entry without translation_unit_id or xml metadata"
            )
        source = str(xml.get("source", ""))
        translations.append(
            {
                "translation_unit_id": unit_id,
                "xml_index": xml.get("index"),
                "edid": xml.get("edid", ""),
                "rec": xml.get("rec", ""),
                "source": source,
                "original_dest": xml.get("dest", ""),
                "protected_tokens": protected_tokens(source),
                "review_reasons": review_reasons(entry),
                "translation": "",
                "status": "PENDING",
                "confidence": "",
                "notes": "",
                "terminology_decisions": [],
            }
        )

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "purpose": "agent_translation_draft_no_xml_writeback",
        "context": context_provenance(context_path, context, batch_index),
        "translations": translations,
    }


def write_json(payload: dict[str, Any], output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise TranslationResultError(f"Output already exists: {output}. Use --force to replace it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expected_items(context: dict[str, Any], batch_index: int) -> dict[str, dict[str, Any]]:
    batch = get_batch(context, batch_index)
    result: dict[str, dict[str, Any]] = {}
    for entry in batch["entries"]:
        if not isinstance(entry, dict):
            raise TranslationResultError(f"Batch {batch_index} contains a non-object entry")
        unit_id = str(entry.get("translation_unit_id", ""))
        if not unit_id:
            raise TranslationResultError(f"Batch {batch_index} contains an empty translation_unit_id")
        if unit_id in result:
            raise TranslationResultError(f"Context batch contains duplicate translation_unit_id: {unit_id}")
        result[unit_id] = entry
    return result


def validate_result(
    result_path: Path,
    result: dict[str, Any],
    context_path: Path,
    context: dict[str, Any],
    allow_pending: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append(
            f"unsupported result schema_version: {result.get('schema_version')!r}"
        )
    if result.get("purpose") != "agent_translation_draft_no_xml_writeback":
        errors.append("unexpected result purpose")

    provenance = result.get("context")
    if not isinstance(provenance, dict):
        errors.append("result is missing context provenance")
        return errors, warnings

    batch_index = provenance.get("batch_index")
    if not isinstance(batch_index, int):
        errors.append("context.batch_index must be an integer")
        return errors, warnings

    try:
        expected_provenance = context_provenance(context_path, context, batch_index)
        expected = expected_items(context, batch_index)
    except TranslationResultError as exc:
        errors.append(str(exc))
        return errors, warnings

    for key in ("sha256", "batch_index", "xtranslator_xml", "mod_context", "mod_dictionary"):
        if provenance.get(key) != expected_provenance.get(key):
            errors.append(f"context provenance mismatch for {key}")

    translations = result.get("translations")
    if not isinstance(translations, list):
        errors.append("result is missing translations array")
        return errors, warnings

    seen: set[str] = set()
    for index, item in enumerate(translations):
        label = f"translations[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        unit_id = str(item.get("translation_unit_id", ""))
        if not unit_id:
            errors.append(f"{label} has empty translation_unit_id")
            continue
        if unit_id in seen:
            errors.append(f"duplicate translation_unit_id in result: {unit_id}")
            continue
        seen.add(unit_id)

        entry = expected.get(unit_id)
        if entry is None:
            errors.append(f"unknown translation_unit_id in result: {unit_id}")
            continue
        xml = entry.get("xml")
        if not isinstance(xml, dict):
            errors.append(f"context entry {unit_id} is missing xml metadata")
            continue

        immutable = {
            "xml_index": xml.get("index"),
            "edid": xml.get("edid", ""),
            "rec": xml.get("rec", ""),
            "source": str(xml.get("source", "")),
            "original_dest": xml.get("dest", ""),
            "protected_tokens": protected_tokens(str(xml.get("source", ""))),
            "review_reasons": review_reasons(entry),
        }
        for key, expected_value in immutable.items():
            if item.get(key) != expected_value:
                errors.append(f"{unit_id}: immutable field changed: {key}")

        status = str(item.get("status", ""))
        if status not in ALLOWED_STATUSES:
            errors.append(f"{unit_id}: invalid status {status!r}")

        confidence = str(item.get("confidence", ""))
        if confidence not in ALLOWED_CONFIDENCE:
            errors.append(f"{unit_id}: invalid confidence {confidence!r}")

        translation = item.get("translation")
        if not isinstance(translation, str):
            errors.append(f"{unit_id}: translation must be a string")
            translation = ""

        if status == "PENDING":
            if not allow_pending:
                errors.append(f"{unit_id}: translation is still PENDING")
            if translation:
                warnings.append(f"{unit_id}: PENDING item already contains translation text")
        elif status == "KEEP":
            source_text = str(xml.get("source", ""))
            if translation != source_text:
                errors.append(f"{unit_id}: KEEP item must preserve the source text exactly")
        elif not translation.strip():
            errors.append(f"{unit_id}: completed item has an empty translation")
        else:
            source_tokens = Counter(protected_tokens(str(xml.get("source", ""))))
            translated_tokens = Counter(protected_tokens(translation))
            if source_tokens != translated_tokens:
                errors.append(
                    f"{unit_id}: protected token mismatch; source={dict(source_tokens)}, translation={dict(translated_tokens)}"
                )
            if translation == str(xml.get("source", "")):
                warnings.append(f"{unit_id}: translation is identical to source")
            if (
                status == "TRANSLATED"
                and item.get("review_reasons")
                and not item.get("terminology_decisions")
            ):
                warnings.append(
                    f"{unit_id}: marked TRANSLATED despite review_reasons={item.get('review_reasons')}"
                )

        if not isinstance(item.get("notes"), str):
            errors.append(f"{unit_id}: notes must be a string")
        decisions = item.get("terminology_decisions")
        if not isinstance(decisions, list) or any(not isinstance(value, dict) for value in decisions):
            errors.append(f"{unit_id}: terminology_decisions must be an array of objects")

    missing = [unit_id for unit_id in expected if unit_id not in seen]
    for unit_id in missing:
        errors.append(f"missing translation_unit_id from result: {unit_id}")

    if len(translations) != len(expected):
        errors.append(
            f"translation count mismatch: result={len(translations)}, expected={len(expected)}"
        )

    return errors, warnings


def command_init(args: argparse.Namespace) -> int:
    context_path = resolve_path(args.context)
    output_path = resolve_path(args.output)
    context = load_json(context_path, "translation context")
    payload = build_result_template(context_path, context, args.batch)
    write_json(payload, output_path, args.force)
    print(
        f"Initialized {len(payload['translations'])} translation item(s) from batch {args.batch} to {output_path}"
    )
    return 0


def command_validate(args: argparse.Namespace) -> int:
    result_path = resolve_path(args.result)
    context_path = resolve_path(args.context)
    result = load_json(result_path, "translation result")
    context = load_json(context_path, "translation context")
    errors, warnings = validate_result(
        result_path=result_path,
        result=result,
        context_path=context_path,
        context=context,
        allow_pending=args.allow_pending,
    )
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(
            f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr,
        )
        return 2
    print(f"Translation result is valid: {len(result.get('translations', []))} item(s), {len(warnings)} warning(s)")
    return 0


def command_summary(args: argparse.Namespace) -> int:
    result_path = resolve_path(args.result)
    result = load_json(result_path, "translation result")
    translations = result.get("translations")
    if not isinstance(translations, list):
        raise TranslationResultError("translation result is missing translations array")
    statuses = Counter(
        str(item.get("status", "<invalid>")) if isinstance(item, dict) else "<invalid>"
        for item in translations
    )
    confidences = Counter(
        str(item.get("confidence", "<invalid>")) if isinstance(item, dict) else "<invalid>"
        for item in translations
    )
    print(f"Result: {result_path}")
    print(f"Items: {len(translations)}")
    print("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    print("Confidence: " + ", ".join(f"{key or '<unset>'}={value}" for key, value in sorted(confidences.items())))
    return 0


def query_results(paths, *, uid=None, rec=None, status=None, text=None,
                  offset=0, limit=20, report=None, contract=None):
    """Read explicit result sets; never silently overwrite duplicate identities."""
    if offset < 0 or limit < 0:
        raise TranslationResultError("offset and limit must be non-negative")
    rows = []
    seen_paths = set()
    for path in paths:
        path = resolve_path(path)
        if path in seen_paths:
            raise TranslationResultError(f"duplicate input file: {path}")
        seen_paths.add(path)
        data = load_json(path, "translation result")
        items = data.get("translations")
        if not isinstance(items, list):
            raise TranslationResultError(f"{path}: missing translations array")
        for item in items:
            if not isinstance(item, dict) or not item.get("translation_unit_id"):
                raise TranslationResultError(f"{path}: invalid item identity")
            rows.append({"result_file": str(path), "item": item})
    if report is not None:
        if not isinstance(report, dict) or not all(isinstance(report.get(k), list) for k in ("fails", "warnings")):
            raise TranslationResultError("gate report requires fails and warnings arrays")
        if contract is not None and (not isinstance(contract, dict) or not isinstance(contract.get("terms"), dict)):
            raise TranslationResultError("contract requires terms object")
        index = {}
        for row in rows:
            index.setdefault(row["item"]["translation_unit_id"], []).append(row)
        expanded = []
        for severity, key in (("FAIL", "fails"), ("WARNING", "warnings")):
            for issue in report[key]:
                if not isinstance(issue, dict) or not issue.get("translation_unit_id"):
                    raise TranslationResultError("gate issue missing translation_unit_id")
                matches = index.get(issue["translation_unit_id"], [])
                if len(matches) != 1:
                    raise TranslationResultError(f"issue identity must match exactly one result: {issue['translation_unit_id']} ({len(matches)} matches)")
                row = dict(matches[0], issue=issue, severity=severity)
                if contract is not None and issue.get("term_id"):
                    term = contract["terms"].get(issue["term_id"])
                    if not isinstance(term, dict) or not isinstance(term.get("source"), str):
                        raise TranslationResultError(f"term missing or invalid: {issue['term_id']}")
                    row["term_definition"] = term
                expanded.append(row)
        rows = expanded
    def matches(row):
        item = row["item"]
        return ((uid is None or item.get("translation_unit_id") == uid)
                and (rec is None or item.get("rec") == rec)
                and (status is None or item.get("status") == status)
                and (text is None or any(text.casefold() in str(item.get(k, "")).casefold()
                                         for k in ("source", "translation"))))
    selected = [row for row in rows if matches(row)]
    page = selected[offset:] if limit == 0 else selected[offset:offset + limit]
    return {"schema_version": 1, "purpose": "read_only_review_not_validation",
            "total_matches": len(selected), "offset": offset, "returned": len(page),
            "has_more": offset + len(page) < len(selected), "rows": page}


def command_query(args):
    report = load_json(resolve_path(args.gate_report), "gate report") if args.gate_report else None
    contract = load_json(resolve_path(args.contract), "contract") if args.contract else None
    if contract is not None and report is None:
        raise TranslationResultError("--contract requires --gate-report")
    result = query_results(args.result, uid=args.uid, rec=args.rec, status=args.status,
                           text=args.text, offset=args.offset, limit=args.limit,
                           report=report, contract=contract)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a translation result for one context batch")
    init_parser.add_argument("context", help="translation-context-builder JSON")
    init_parser.add_argument("--batch", type=int, default=0, help="Batch index to initialize")
    init_parser.add_argument("--output", required=True, help="Result JSON path")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing output file")
    init_parser.set_defaults(func=command_init)

    validate_parser = subparsers.add_parser("validate", help="Validate a translation result against its context")
    validate_parser.add_argument("result", help="Translation result JSON")
    validate_parser.add_argument("--context", required=True, help="Original translation context JSON")
    validate_parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="Allow PENDING items for incomplete work-in-progress validation",
    )
    validate_parser.set_defaults(func=command_validate)

    summary_parser = subparsers.add_parser("summary", help="Print translation-result progress counts")
    summary_parser.add_argument("result", help="Translation result JSON")
    summary_parser.set_defaults(func=command_summary)
    query_parser = subparsers.add_parser("query", help="Read complete result items across explicit files; optionally expand gate issues")
    query_parser.add_argument("--result", action="append", required=True)
    query_parser.add_argument("--uid")
    query_parser.add_argument("--rec")
    query_parser.add_argument("--status")
    query_parser.add_argument("--text", help="Case-insensitive substring in source or translation")
    query_parser.add_argument("--offset", type=int, default=0)
    query_parser.add_argument("--limit", type=int, default=20, help="Rows per page; 0 means all")
    query_parser.add_argument("--gate-report")
    query_parser.add_argument("--contract")
    query_parser.set_defaults(func=command_query)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return int(args.func(args))
    except (OSError, TranslationResultError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

