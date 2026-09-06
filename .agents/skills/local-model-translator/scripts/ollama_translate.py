#!/usr/bin/env python3
"""Constrained Ollama translation-worker adapter for Skyrim localization."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_MODEL = "hy-mt2-32k:latest"
DEFAULT_NUM_CTX = 32768
DEFAULT_TEMPERATURE = 0.0
DEFAULT_HOST = "http://127.0.0.1:11434"

NEWLINE_SENTINELS = {
    "\r\n": "<LM_CRLF>",
    "\n": "<LM_LF>",
    "\r": "<LM_CR>",
}

PROTECTED_PATTERNS = [
    re.compile(r"<[^<>\r\n]+>"),
    re.compile(r"%(?:\d+\$)?[-+#0 ']*\d*(?:\.\d+)?[hlLzjt]*[diuoxXfFeEgGaAcspn%]"),
    re.compile(r"\{[^{}\r\n]+\}"),
    re.compile(r"\[[^\[\]\r\n]+\]"),
    re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"),
    re.compile(r"\\[nrt0\\]"),
]

OUTPUT_LINE_RE = re.compile(r"^\[([^\]\r\n]+)\]\s?(.*)$")


class RequestError(ValueError):
    pass


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RequestError(f"JSON root must be an object: {path}")
    return data


def dump_json(data: dict[str, Any], path: str | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if not path or path == "-":
        sys.stdout.write(text)
        return
    Path(path).write_text(text, encoding="utf-8")


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    raise RequestError("background/style must be a string or a list of strings")


def validate_request(request: dict[str, Any]) -> None:
    schema_version = request.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise RequestError(
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )

    task_id = request.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise RequestError("task_id must be a non-empty string")

    items = request.get("items")
    if not isinstance(items, list) or not items:
        raise RequestError("items must be a non-empty list")

    def validate_string_list(value: Any, label: str) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            raise RequestError(f"{label} must be a list of strings")
        for entry_index, entry in enumerate(value):
            if not isinstance(entry, str) or not entry:
                raise RequestError(
                    f"{label}[{entry_index}] must be a non-empty string"
                )

    validate_string_list(request.get("keep_literals"), "keep_literals")

    ids: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RequestError(f"items[{index}] must be an object")
        item_id = item.get("id")
        source = item.get("source")
        if not isinstance(item_id, str) or not item_id.strip():
            raise RequestError(f"items[{index}].id must be a non-empty string")
        if "]" in item_id or "\n" in item_id or "\r" in item_id:
            raise RequestError(
                f"items[{index}].id may not contain ']' or line breaks: {item_id!r}"
            )
        if not isinstance(source, str) or not source:
            raise RequestError(f"items[{index}].source must be a non-empty string")
        validate_string_list(item.get("keep_literals"), f"items[{index}].keep_literals")
        validate_string_list(
            item.get("required_phrases"), f"items[{index}].required_phrases"
        )
        ids.append(item_id)

        for sentinel in NEWLINE_SENTINELS.values():
            if sentinel in source:
                raise RequestError(
                    f"items[{index}].source contains reserved internal token {sentinel}"
                )

    duplicates = [item_id for item_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise RequestError(f"duplicate item ids: {duplicates}")

    terminology = request.get("terminology", [])
    if not isinstance(terminology, list):
        raise RequestError("terminology must be a list")
    for index, term in enumerate(terminology):
        if not isinstance(term, dict):
            raise RequestError(f"terminology[{index}] must be an object")
        source = term.get("source")
        target = term.get("target")
        if not isinstance(source, str) or not source:
            raise RequestError(f"terminology[{index}].source must be a non-empty string")
        if not isinstance(target, str) or not target:
            raise RequestError(f"terminology[{index}].target must be a non-empty string")


def encode_newlines(text: str) -> str:
    return (
        text.replace("\r\n", NEWLINE_SENTINELS["\r\n"])
        .replace("\n", NEWLINE_SENTINELS["\n"])
        .replace("\r", NEWLINE_SENTINELS["\r"])
    )


def decode_newlines(text: str) -> str:
    return (
        text.replace(NEWLINE_SENTINELS["\r\n"], "\r\n")
        .replace(NEWLINE_SENTINELS["\n"], "\n")
        .replace(NEWLINE_SENTINELS["\r"], "\r")
    )


def newline_signature(text: str) -> Counter[str]:
    signature: Counter[str] = Counter()
    remaining = text
    crlf = remaining.count("\r\n")
    signature["CRLF"] = crlf
    remaining = remaining.replace("\r\n", "")
    signature["LF"] = remaining.count("\n")
    signature["CR"] = remaining.count("\r")
    return signature


def encoded_newline_signature(text: str) -> Counter[str]:
    return Counter(
        {
            "CRLF": text.count(NEWLINE_SENTINELS["\r\n"]),
            "LF": text.count(NEWLINE_SENTINELS["\n"]),
            "CR": text.count(NEWLINE_SENTINELS["\r"]),
        }
    )


def extract_protected_tokens(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), match.group(0)))

    # Patterns are chosen to avoid normal overlap. De-duplicate exact span/token
    # pairs defensively so one token is not counted twice by future extensions.
    seen: set[tuple[int, int, str]] = set()
    ordered: list[tuple[int, int, str]] = []
    for match in sorted(matches, key=lambda item: (item[0], item[1], item[2])):
        if match not in seen:
            seen.add(match)
            ordered.append(match)
    return [token for _, _, token in ordered]


def protected_summary(source: str) -> str:
    counts = Counter(extract_protected_tokens(source))
    counts.update(
        {
            NEWLINE_SENTINELS["\r\n"]: source.count("\r\n"),
        }
    )
    without_crlf = source.replace("\r\n", "")
    counts[NEWLINE_SENTINELS["\n"]] += without_crlf.count("\n")
    counts[NEWLINE_SENTINELS["\r"]] += without_crlf.count("\r")
    parts = [f"{token} x{count}" for token, count in counts.items() if count]
    return ", ".join(parts) if parts else "none"


def render_prompt(request: dict[str, Any]) -> str:
    validate_request(request)
    background = as_text(request.get("background"))
    style = as_text(request.get("style"))
    terminology = request.get("terminology", [])
    keep_literals = request.get("keep_literals", [])
    items = request["items"]

    sections: list[str] = [
        "You are a local translation worker for The Elder Scrolls V: Skyrim MOD localization.",
        "The high-level Agent has already resolved story semantics, terminology decisions, and spoiler boundaries.",
        "Your job is only to translate the SOURCE ITEMS from English into natural Simplified Chinese.",
    ]

    if background:
        sections.extend(
            [
                "\nBACKGROUND (context only; do not translate or continue this text):",
                background,
            ]
        )

    metadata_lines: list[str] = []
    for item in items:
        parts: list[str] = []
        record_type = item.get("record_type")
        if isinstance(record_type, str) and record_type.strip():
            parts.append(f"record_type={record_type.strip()}")
        guidance = item.get("guidance")
        if isinstance(guidance, str) and guidance.strip():
            parts.append(f"guidance={guidance.strip()}")
        item_keep = item.get("keep_literals", [])
        if item_keep:
            parts.append("keep_exact=" + ", ".join(repr(value) for value in item_keep))
        required = item.get("required_phrases", [])
        if required:
            parts.append(
                "HARD_REQUIRED_TARGET="
                + ", ".join(repr(value) for value in required)
                + " (the Chinese output must contain these exact phrases even if English omits the referent)"
            )
        if parts:
            metadata_lines.append(f"- [{item['id']}] " + "; ".join(parts))
    if metadata_lines:
        sections.extend(
            [
                "\nITEM METADATA (context only; never copy this metadata into the translation):",
                *metadata_lines,
            ]
        )

    if style:
        sections.extend(["\nSTYLE:", style])

    if terminology:
        sections.append("\nTERMINOLOGY:")
        for term in terminology:
            label = "MANDATORY" if term.get("enforce", True) else "PREFERRED"
            sections.append(f"- [{label}] `{term['source']}` => `{term['target']}`")

    if keep_literals:
        sections.extend(
            [
                "\nNARRATIVE LITERALS TO KEEP EXACTLY:",
                "These are player-visible story literals, not runtime placeholders. Keep their spelling exactly when they occur in a source item; do not translate or transliterate them.",
            ]
        )
        for literal in keep_literals:
            sections.append(f"- `{literal}`")

    sections.extend(
        [
            "\nTRANSLATION CONTRACT:",
            "1. Translate only the SOURCE ITEMS. Do not translate or continue BACKGROUND, STYLE, ITEM METADATA, or this contract.",
            "2. Output exactly one line per source item, in exactly the same order.",
            "3. Each output line must be: [ID] Chinese translation",
            "4. Copy every ID exactly. Do not add, remove, merge, split, or reorder IDs.",
            "5. Runtime tokens are not natural language. Preserve every protected token exactly and preserve its count.",
            "6. Internal tokens <LM_CRLF>, <LM_LF>, and <LM_CR> represent source line breaks. Preserve them exactly where present.",
            "7. Follow every MANDATORY terminology mapping when its source term occurs.",
            "8. Preserve every NARRATIVE LITERAL assigned to the request or item exactly when it occurs in that source item.",
            "9. Include every REQUIRED TARGET PHRASE assigned to an item. The high-level Agent has already decided that Chinese needs to make that context-carried meaning explicit.",
            "10. Return translation lines only. No explanation, Markdown, commentary, headings, or code fences.",
            "\nITEM REQUIREMENTS:",
        ]
    )

    for item in items:
        item_keep = item.get("keep_literals", [])
        required = item.get("required_phrases", [])
        parts: list[str] = []
        if item_keep:
            parts.append("keep_exact=" + ", ".join(repr(value) for value in item_keep))
        if required:
            parts.append(
                "required_target=" + ", ".join(repr(value) for value in required)
            )
        sections.append(
            f"- [{item['id']}] " + ("; ".join(parts) if parts else "none")
        )

    sections.extend(
        [
            "\nPROTECTED TOKENS BY ITEM:",
        ]
    )

    for item in items:
        sections.append(f"- [{item['id']}] {protected_summary(item['source'])}")

    sections.append("\nSOURCE ITEMS:")
    for item in items:
        sections.append(f"[{item['id']}] {encode_newlines(item['source'])}")

    sections.extend(
        [
            "\nFINAL REMINDER: translate SOURCE ITEMS only; keep IDs/order/protected tokens exact; use mandatory terminology; output one [ID] translation line per item.",
            "OUTPUT:",
        ]
    )
    return "\n".join(sections)


def normalize_host(host: str | None) -> str:
    value = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip()
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.rstrip("/")


def estimate_num_predict(request: dict[str, Any]) -> int:
    source_chars = sum(len(item["source"]) for item in request["items"])
    item_overhead = len(request["items"]) * 16
    return max(512, min(8192, source_chars * 2 + item_overhead))


def call_ollama(
    *,
    host: str,
    model: str,
    prompt: str,
    num_ctx: int,
    temperature: float,
    num_predict: int,
    keep_alive: str,
    timeout: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    request = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach Ollama at {host}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama request timed out after {timeout:g}s") from exc

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Ollama response root is not a JSON object")
    if "error" in data:
        raise RuntimeError(f"Ollama error: {data['error']}")
    return data


def parse_model_output(raw_output: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    parsed: list[tuple[str, str, str]] = []
    unexpected: list[str] = []
    for line in raw_output.splitlines():
        if not line.strip():
            continue
        match = OUTPUT_LINE_RE.match(line)
        if not match:
            unexpected.append(line)
            continue
        item_id = match.group(1)
        encoded_translation = match.group(2).strip()
        parsed.append((item_id, encoded_translation, decode_newlines(encoded_translation)))
    return parsed, unexpected


def count_occurrences(text: str, needle: str, case_sensitive: bool) -> int:
    if case_sensitive:
        return text.count(needle)
    return text.casefold().count(needle.casefold())


def validate_translations(
    request: dict[str, Any],
    parsed: list[tuple[str, str, str]],
    *,
    unexpected_lines: list[str] | None = None,
    done_reason: str | None = None,
) -> dict[str, Any]:
    validate_request(request)
    errors: list[str] = []
    warnings: list[str] = []
    unexpected_lines = unexpected_lines or []

    expected_ids = [item["id"] for item in request["items"]]
    actual_ids = [item_id for item_id, _, _ in parsed]

    if unexpected_lines:
        errors.append(f"unexpected non-ID output lines: {unexpected_lines[:5]}")
    if actual_ids != expected_ids:
        errors.append(f"ID/order mismatch: expected {expected_ids}, got {actual_ids}")

    duplicates = [item_id for item_id, count in Counter(actual_ids).items() if count > 1]
    if duplicates:
        errors.append(f"duplicate output IDs: {duplicates}")

    parsed_by_id = {item_id: (encoded, translation) for item_id, encoded, translation in parsed}
    terminology = request.get("terminology", [])
    request_keep_literals = request.get("keep_literals", [])

    for item in request["items"]:
        item_id = item["id"]
        source = item["source"]
        if item_id not in parsed_by_id:
            continue
        encoded_translation, translation = parsed_by_id[item_id]
        if not translation:
            errors.append(f"[{item_id}] empty translation")
            continue

        source_tokens = Counter(extract_protected_tokens(source))
        translation_tokens = Counter(extract_protected_tokens(translation))
        if translation_tokens != source_tokens:
            errors.append(
                f"[{item_id}] protected-token mismatch: "
                f"source={dict(source_tokens)} translation={dict(translation_tokens)}"
            )

        source_newlines = newline_signature(source)
        output_newlines = encoded_newline_signature(encoded_translation)
        if source_newlines != output_newlines:
            errors.append(
                f"[{item_id}] line-break mismatch: "
                f"source={dict(source_newlines)} translation={dict(output_newlines)}"
            )

        for term in terminology:
            if not term.get("enforce", True):
                continue
            term_source = term["source"]
            term_target = term["target"]
            case_sensitive = bool(term.get("case_sensitive", False))
            source_count = count_occurrences(source, term_source, case_sensitive)
            if source_count == 0:
                continue
            target_count = count_occurrences(translation, term_target, True)
            if target_count < source_count:
                errors.append(
                    f"[{item_id}] mandatory terminology missing: "
                    f"{term_source!r} => {term_target!r}; "
                    f"source_count={source_count}, target_count={target_count}"
                )

        keep_literals = [*request_keep_literals, *item.get("keep_literals", [])]
        for literal in keep_literals:
            source_count = source.count(literal)
            if source_count == 0:
                continue
            target_count = translation.count(literal)
            if target_count < source_count:
                errors.append(
                    f"[{item_id}] narrative literal changed or missing: "
                    f"{literal!r}; source_count={source_count}, target_count={target_count}"
                )

        for phrase in item.get("required_phrases", []):
            if phrase not in translation:
                errors.append(f"[{item_id}] required target phrase missing: {phrase!r}")

    if done_reason and done_reason != "stop":
        warnings.append(f"Ollama done_reason={done_reason!r}, expected 'stop'")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def duration_seconds(value: Any) -> float | None:
    if not isinstance(value, int | float):
        return None
    return value / 1_000_000_000


def rate(count: Any, duration_ns: Any) -> float | None:
    if not isinstance(count, int | float) or not isinstance(duration_ns, int | float):
        return None
    if duration_ns <= 0:
        return None
    return count / (duration_ns / 1_000_000_000)


def metrics_from_ollama(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "done_reason": data.get("done_reason"),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "load_seconds": duration_seconds(data.get("load_duration")),
        "prompt_seconds": duration_seconds(data.get("prompt_eval_duration")),
        "generation_seconds": duration_seconds(data.get("eval_duration")),
        "total_seconds": duration_seconds(data.get("total_duration")),
        "prompt_tokens_per_second": rate(
            data.get("prompt_eval_count"), data.get("prompt_eval_duration")
        ),
        "generation_tokens_per_second": rate(data.get("eval_count"), data.get("eval_duration")),
    }


def build_response(
    request: dict[str, Any],
    *,
    model: str,
    host: str,
    num_ctx: int,
    temperature: float,
    num_predict: int,
    raw_ollama: dict[str, Any],
) -> dict[str, Any]:
    raw_output = raw_ollama.get("response", "")
    if not isinstance(raw_output, str):
        raw_output = str(raw_output)
    parsed, unexpected = parse_model_output(raw_output)
    validation = validate_translations(
        request,
        parsed,
        unexpected_lines=unexpected,
        done_reason=raw_ollama.get("done_reason"),
    )
    by_id = {item_id: translation for item_id, _, translation in parsed}

    translations = []
    for item in request["items"]:
        effective_keep = [
            literal
            for literal in [*request.get("keep_literals", []), *item.get("keep_literals", [])]
            if literal in item["source"]
        ]
        translations.append(
            {
                "id": item["id"],
                "record_type": item.get("record_type"),
                "source": item["source"],
                "translation": by_id.get(item["id"]),
                "protected_tokens": extract_protected_tokens(item["source"]),
                "keep_literals": effective_keep,
                "required_phrases": item.get("required_phrases", []),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": request["task_id"],
        "model": model,
        "config": {
            "host": host,
            "num_ctx": num_ctx,
            "temperature": temperature,
            "num_predict": num_predict,
        },
        "translations": translations,
        "validation": validation,
        "metrics": metrics_from_ollama(raw_ollama),
        "raw_output": raw_output,
    }


def response_to_parsed(response: dict[str, Any]) -> list[tuple[str, str, str]]:
    translations = response.get("translations")
    if not isinstance(translations, list):
        raise RequestError("response.translations must be a list")
    parsed: list[tuple[str, str, str]] = []
    for index, item in enumerate(translations):
        if not isinstance(item, dict):
            raise RequestError(f"response.translations[{index}] must be an object")
        item_id = item.get("id")
        translation = item.get("translation")
        if not isinstance(item_id, str):
            raise RequestError(f"response.translations[{index}].id must be a string")
        if translation is None:
            translation = ""
        if not isinstance(translation, str):
            raise RequestError(f"response.translations[{index}].translation must be a string")
        # Re-encode actual line breaks so validate_translations can check the same
        # reversible transport invariant used during a live run.
        encoded = encode_newlines(translation)
        parsed.append((item_id, encoded, translation))
    return parsed


def cmd_prompt(args: argparse.Namespace) -> int:
    request = load_json(args.request)
    sys.stdout.write(render_prompt(request) + "\n")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    request = load_json(args.request)
    validate_request(request)
    host = normalize_host(args.host)
    model = args.model or request.get("model") or DEFAULT_MODEL
    num_ctx = args.num_ctx or int(request.get("num_ctx", DEFAULT_NUM_CTX))
    temperature = (
        args.temperature
        if args.temperature is not None
        else float(request.get("temperature", DEFAULT_TEMPERATURE))
    )
    num_predict = args.num_predict or int(
        request.get("num_predict", estimate_num_predict(request))
    )
    prompt = render_prompt(request)
    raw_ollama = call_ollama(
        host=host,
        model=model,
        prompt=prompt,
        num_ctx=num_ctx,
        temperature=temperature,
        num_predict=num_predict,
        keep_alive=args.keep_alive,
        timeout=args.timeout,
    )
    response = build_response(
        request,
        model=model,
        host=host,
        num_ctx=num_ctx,
        temperature=temperature,
        num_predict=num_predict,
        raw_ollama=raw_ollama,
    )
    dump_json(response, args.output)
    return 0 if response["validation"]["ok"] else 2


def cmd_validate(args: argparse.Namespace) -> int:
    request = load_json(args.request)
    validate_request(request)
    response = load_json(args.response)
    raw_output = response.get("raw_output")
    if isinstance(raw_output, str):
        parsed, unexpected = parse_model_output(raw_output)
    else:
        parsed = response_to_parsed(response)
        unexpected = []

    metrics = response.get("metrics")
    done_reason = metrics.get("done_reason") if isinstance(metrics, dict) else None
    validation = validate_translations(
        request,
        parsed,
        unexpected_lines=unexpected,
        done_reason=done_reason if isinstance(done_reason, str) else None,
    )
    if response.get("task_id") != request["task_id"]:
        validation["errors"].insert(
            0,
            f"task_id mismatch: request={request['task_id']!r}, response={response.get('task_id')!r}",
        )
        validation["ok"] = False
    sys.stdout.write(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
    return 0 if validation["ok"] else 2


def prepare_import_map(request, response, result, bindings):
    """Revalidate both representations, then map explicit worker IDs to result IDs."""
    validate_request(request)
    if response.get("task_id") != request["task_id"]:
        raise RequestError("task_id mismatch")
    if not isinstance(response.get("raw_output"), str):
        raise RequestError("import requires original raw_output")
    metrics = response.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("done_reason") != "stop":
        raise RequestError("import requires done_reason=stop")
    raw, unexpected = parse_model_output(response["raw_output"])
    parsed = response_to_parsed(response)
    for items, noise in ((raw, unexpected), (parsed, [])):
        validation = validate_translations(request, items, unexpected_lines=noise, done_reason="stop")
        if not validation["ok"]:
            raise RequestError("worker revalidation failed: " + "; ".join(validation["errors"]))
    if [(i, t) for i, _, t in raw] != [(i, t) for i, _, t in parsed]:
        raise RequestError("raw_output and translations disagree; do not silently promote repaired output")
    expected = {item["id"] for item in request["items"]}
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise RequestError("bindings must map every request ID exactly once")
    if not all(isinstance(value, str) for value in bindings.values()) or len(set(bindings.values())) != len(bindings):
        raise RequestError("binding targets must be unique result unit IDs")
    units = result.get("translations")
    if not isinstance(units, list):
        raise RequestError("result requires translations array")
    index = {}
    xml_keys = set()
    for unit in units:
        if not isinstance(unit, dict):
            raise RequestError("invalid result item")
        uid, xi = unit.get("translation_unit_id"), unit.get("xml_index")
        if not isinstance(uid, str) or uid in index or type(xi) is not int or xi < 0 or xi in xml_keys:
            raise RequestError("result requires unique unit IDs and nonnegative xml_index values")
        index[uid] = unit
        xml_keys.add(xi)
    translations = {i: t for i, _, t in parsed}
    out = {}
    for item in request["items"]:
        unit = index.get(bindings[item["id"]])
        if unit is None or unit.get("source") != item["source"]:
            raise RequestError("bound result missing or source mismatch")
        if unit.get("status") != "PENDING":
            raise RequestError("import only accepts PENDING result items")
        out[str(unit["xml_index"])] = {
            "translation": translations[item["id"]], "status": "REVIEW",
            "confidence": "LOW", "expected_translation": unit.get("translation"),
            "notes": "Worker structurally revalidated; semantic review pending.",
        }
    return out


def cmd_import_map(args):
    output = Path(args.output)
    mapping = prepare_import_map(load_json(args.request), load_json(args.response),
                                 load_json(args.result), load_json(args.bindings))
    # Exclusive creation: never overwrite a response, result or existing map.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(mapping, stream, ensure_ascii=False, indent=2)
    print(f"Prepared {len(mapping)} REVIEW items; result unchanged")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use Ollama as a constrained local translation worker for Skyrim localization."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt_parser = subparsers.add_parser("prompt", help="Render the worker prompt only")
    prompt_parser.add_argument("request", help="Worker request JSON")
    prompt_parser.set_defaults(func=cmd_prompt)

    run_parser = subparsers.add_parser("run", help="Call Ollama and validate the response")
    run_parser.add_argument("request", help="Worker request JSON")
    run_parser.add_argument("--output", default="-", help="Response JSON path; '-' prints stdout")
    run_parser.add_argument("--host", help="Ollama base URL; defaults to OLLAMA_HOST or localhost")
    run_parser.add_argument("--model", help=f"Ollama model name; default {DEFAULT_MODEL}")
    run_parser.add_argument("--num-ctx", type=int, help=f"Context window; default {DEFAULT_NUM_CTX}")
    run_parser.add_argument("--temperature", type=float, help="Sampling temperature; default 0")
    run_parser.add_argument("--num-predict", type=int, help="Maximum generated tokens")
    run_parser.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout seconds")
    run_parser.add_argument("--keep-alive", default="5m", help="Ollama keep_alive value")
    run_parser.set_defaults(func=cmd_run)

    validate_parser = subparsers.add_parser(
        "validate", help="Revalidate an existing worker response without calling Ollama"
    )
    validate_parser.add_argument("response", help="Worker response JSON")
    validate_parser.add_argument("--request", required=True, help="Original worker request JSON")
    validate_parser.set_defaults(func=cmd_validate)

    import_parser = subparsers.add_parser("import-map", help="Revalidate a worker response and create a REVIEW map; never modify results")
    import_parser.add_argument("response")
    import_parser.add_argument("--request", required=True)
    import_parser.add_argument("--result", required=True)
    import_parser.add_argument("--bindings", required=True, help="JSON object: worker ID to result translation_unit_id")
    import_parser.add_argument("--output", required=True, help="New map path; existing files are rejected")
    import_parser.set_defaults(func=cmd_import_map)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (RequestError, RuntimeError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

