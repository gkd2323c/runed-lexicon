#!/usr/bin/env python3
"""Surgically apply structured translation results to xTranslator XML."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
UTF8_BOM = b"\xef\xbb\xbf"

STRING_BLOCK_RE = re.compile(r"<String\b[^>]*>.*?</String>", re.DOTALL)
DEST_RE = re.compile(r"<Dest>(.*?)</Dest>", re.DOTALL)

ANGLE_TOKEN_RE = re.compile(r"<[^<>\r\n]+>")
PRINTF_TOKEN_RE = re.compile(
    r"%(?:\d+\$)?[-+#0']*\d*(?:\.\d+)?[diuoxXfFeEgGaAcspn%]"
)
BRACE_TOKEN_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.:=|+\- ]*\}")
BRACKET_TOKEN_RE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_.:=|+\- ]*\]")
ESCAPE_TOKEN_RE = re.compile(r"\\[nrt\\]")


class WritebackError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise WritebackError(f"Result file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WritebackError(f"Invalid result JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WritebackError(f"Result root must be an object: {path}")
    return value


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


def xml_text(value: str, newline: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    escaped = (
        normalized.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
        .replace("'", "&apos;")
    )
    return escaped.replace("\n", newline)


def parsed_strings(xml_bytes: bytes, label: str) -> tuple[ET.Element, list[ET.Element]]:
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise WritebackError(f"{label} is not valid UTF-8 xTranslator XML: {exc}") from exc
    content = root.find("Content")
    if content is None:
        raise WritebackError(f"{label} has no <Content> element")
    return root, content.findall("String")


def params_snapshot(root: ET.Element) -> list[tuple[str, str]]:
    params = root.find("Params")
    if params is None:
        raise WritebackError("XML has no <Params> element")
    return [(child.tag, child.text or "") for child in list(params)]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook：拒绝重复键。JSON 默认保留最后一个同名键，会让同一条目的
    多次修改静默丢失（同 index 写两条 = 只剩最后一条）；这里直接报错。"""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise WritebackError(f"Duplicate key in patch JSON: {key!r}")
        seen[key] = value
    return seen


def load_patch(
    path: Path, source_strings: list[ET.Element]
) -> dict[int, dict[str, Any]]:
    """Load an atomic patch file: {"<xml_index>": {"expected_dest", "translation", ...}}.

    expected_dest is a compare-and-swap guard: it must match the XML's current
    Dest exactly, otherwise the patch is rejected (stale baseline or wrong index).
    Optional keys "source", "edid", "rec" get the same CAS treatment when present.
    Optional key "waived_tokens" (R16): Agent-endorsed token waivers, same
    semantics as translation-executor (verified against source substring,
    subtracted from both multisets).
    """
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except FileNotFoundError as exc:
        raise WritebackError(f"Patch file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WritebackError(f"Invalid patch JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WritebackError(f"Patch root must be an object: {path}")
    if not value:
        # 空 patch = 显式无变更（幂等重跑已完成的批）；返回空 items，main 短路处理
        return {}


    items: dict[int, dict[str, Any]] = {}
    for raw_index, entry in value.items():
        label = f"patch[{raw_index}] ({relative(path)})"
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise WritebackError(f"{label}: invalid xml_index key") from exc
        if index < 0 or index >= len(source_strings):
            raise WritebackError(f"{label}: xml_index out of range: {index}")
        if not isinstance(entry, dict):
            raise WritebackError(f"{label}: entry must be an object")
        translation = str(entry.get("translation", ""))
        if not translation:
            raise WritebackError(f"{label}: missing translation")

        node = source_strings[index]
        cas_checks = {
            "expected_dest": node.findtext("Dest") or "",
            "source": node.findtext("Source") or "",
            "edid": node.findtext("EDID") or "",
            "rec": node.findtext("REC") or "",
        }
        for key, actual in cas_checks.items():
            if key not in entry:
                continue
            supplied = str(entry[key])
            if supplied != actual:
                raise WritebackError(
                    f"{label}: stale {key} (compare-and-swap failed): "
                    f"patch={supplied!r}, xml={actual!r}"
                )
        if "expected_dest" not in entry:
            raise WritebackError(f"{label}: missing expected_dest compare-and-swap guard")

        source_tokens = Counter(protected_tokens(cas_checks["source"]))
        translation_tokens = Counter(protected_tokens(translation))
        waived = entry.get("waived_tokens") or []
        if waived:
            if not isinstance(waived, list) or any(not isinstance(w, str) for w in waived):
                raise WritebackError(f"{label}: waived_tokens must be an array of strings")
            for w in waived:
                if w not in cas_checks["source"]:
                    raise WritebackError(f"{label}: waived token not in source: {w!r}")
                source_tokens[w] -= 1
                translation_tokens[w] -= 1
                if source_tokens[w] <= 0:
                    del source_tokens[w]
                if translation_tokens[w] <= 0:
                    del translation_tokens[w]
        if source_tokens != translation_tokens:
            raise WritebackError(
                f"{label}: protected token mismatch; "
                f"source={dict(source_tokens)}, translation={dict(translation_tokens)}"
            )

        items[index] = {
            "translation_unit_id": f"patch:{index}",
            "xml_index": index,
            "edid": cas_checks["edid"],
            "rec": cas_checks["rec"],
            "source": cas_checks["source"],
            "original_dest": cas_checks["expected_dest"],
            "translation": translation,
            "status": "TRANSLATED",
        }
    return items


def collect_result_paths(explicit: list[str], patterns: list[str]) -> list[Path]:
    found: list[Path] = [resolve_path(value) for value in explicit]
    for pattern in patterns:
        raw = pattern
        if not Path(raw).is_absolute():
            raw = str(PROJECT_ROOT / raw)
        matches = sorted(Path(item).resolve() for item in glob.glob(raw))
        if not matches:
            raise WritebackError(f"Result glob matched no files: {pattern}")
        found.extend(matches)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    if not unique:
        raise WritebackError("No result files supplied")
    return unique


def collect_translations(
    paths: list[Path], source_hash: str, xml_strings: list[ET.Element],
    skip_nonfinal: bool = False,
) -> tuple[dict[int, dict[str, Any]], dict[str, int], list[dict[str, str]]]:
    by_index: dict[int, dict[str, Any]] = {}
    seen_units: set[str] = set()
    counts: Counter[str] = Counter()
    skipped: list[dict[str, str]] = []

    for path in paths:
        result = load_json(path)
        context = result.get("context")
        if not isinstance(context, dict):
            raise WritebackError(f"Result has no context object: {path}")
        xmeta = context.get("xtranslator_xml")
        recorded_hash = xmeta.get("sha256") if isinstance(xmeta, dict) else None
        if recorded_hash != source_hash:
            raise WritebackError(
                f"Source XML hash mismatch for {relative(path)}: "
                f"result expects {recorded_hash!r}, actual is {source_hash}"
            )
        items = result.get("translations")
        if not isinstance(items, list):
            raise WritebackError(f"Result has no translations array: {path}")

        for item in items:
            if not isinstance(item, dict):
                raise WritebackError(f"Non-object translation item in {path}")
            unit_id = str(item.get("translation_unit_id", ""))
            if not unit_id:
                raise WritebackError(f"Translation item without translation_unit_id in {path}")
            if unit_id in seen_units:
                raise WritebackError(f"Duplicate translation_unit_id across results: {unit_id}")
            seen_units.add(unit_id)

            try:
                index = int(item["xml_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise WritebackError(f"{unit_id}: invalid xml_index") from exc
            if index < 0 or index >= len(xml_strings):
                raise WritebackError(f"{unit_id}: xml_index out of range: {index}")
            if index in by_index:
                other = by_index[index].get("translation_unit_id")
                raise WritebackError(
                    f"Duplicate xml_index across results: {index} ({other}, {unit_id})"
                )

            status = str(item.get("status", "")).upper()
            if status not in {"TRANSLATED", "KEEP"}:
                if not skip_nonfinal:
                    raise WritebackError(f"{unit_id}: non-final status {status!r}")
                skipped.append({
                    "translation_unit_id": unit_id,
                    "status": status,
                    "result_file": relative(path),
                })
                continue
            counts[status] += 1

            node = xml_strings[index]
            expected = {
                "edid": node.findtext("EDID") or "",
                "rec": node.findtext("REC") or "",
                "source": node.findtext("Source") or "",
                "original_dest": node.findtext("Dest") or "",
            }
            for key, actual in expected.items():
                supplied = str(item.get(key, ""))
                if supplied != actual:
                    raise WritebackError(
                        f"{unit_id}: {key} mismatch at xml_index {index}: "
                        f"result={supplied!r}, xml={actual!r}"
                    )

            translation = str(item.get("translation", ""))
            if not translation:
                raise WritebackError(f"{unit_id}: completed item has empty translation")
            if status == "KEEP" and translation != expected["source"]:
                raise WritebackError(f"{unit_id}: KEEP translation must equal source exactly")

            source_tokens = Counter(protected_tokens(expected["source"]))
            translation_tokens = Counter(protected_tokens(translation))
            if source_tokens != translation_tokens:
                raise WritebackError(
                    f"{unit_id}: protected token mismatch; "
                    f"source={dict(source_tokens)}, translation={dict(translation_tokens)}"
                )

            by_index[index] = item

    return by_index, dict(counts), skipped


def raw_dest_spans(xml_text: str, expected_count: int) -> list[tuple[int, int]]:
    blocks = list(STRING_BLOCK_RE.finditer(xml_text))
    if len(blocks) != expected_count:
        raise WritebackError(
            f"Raw <String> block count {len(blocks)} does not match parsed count {expected_count}"
        )
    spans: list[tuple[int, int]] = []
    for index, block in enumerate(blocks):
        dests = list(DEST_RE.finditer(block.group(0)))
        if len(dests) != 1:
            raise WritebackError(
                f"String index {index} has {len(dests)} explicit <Dest> elements; expected exactly 1"
            )
        match = dests[0]
        spans.append((block.start() + match.start(1), block.start() + match.end(1)))
    return spans


def verify_output(
    source_root: ET.Element,
    source_strings: list[ET.Element],
    output_bytes: bytes,
    items: dict[int, dict[str, Any]],
) -> None:
    output_root, output_strings = parsed_strings(output_bytes, "Generated XML")
    if len(output_strings) != len(source_strings):
        raise WritebackError(
            f"Post-write String count changed: {len(source_strings)} -> {len(output_strings)}"
        )
    if params_snapshot(source_root) != params_snapshot(output_root):
        raise WritebackError("Post-write <Params> changed")

    for index, (before, after) in enumerate(zip(source_strings, output_strings, strict=True)):
        for tag in ("EDID", "REC", "Source"):
            if (before.findtext(tag) or "") != (after.findtext(tag) or ""):
                raise WritebackError(f"Post-write {tag} changed at xml_index {index}")
        before_dest = before.findtext("Dest") or ""
        after_dest = after.findtext("Dest") or ""
        item = items.get(index)
        if item is None:
            expected_dest = before_dest
        else:
            expected_dest = str(item.get("translation", ""))
        if after_dest != expected_dest:
            raise WritebackError(
                f"Post-write Dest mismatch at xml_index {index}: "
                f"expected={expected_dest!r}, actual={after_dest!r}"
            )


def write_report(path: Path, report: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise WritebackError(f"Report already exists; use --force to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, help="Source xTranslator XML")
    parser.add_argument("--result", action="append", default=[], help="Result JSON; repeatable")
    parser.add_argument(
        "--result-glob", action="append", default=[], help="Result JSON glob; repeatable"
    )
    parser.add_argument(
        "--patch",
        action="append",
        default=[],
        help="Atomic patch JSON (xml_index -> {expected_dest, translation}); repeatable",
    )
    parser.add_argument("--output", help="Generated XML path")
    parser.add_argument("--report", help="Optional JSON report path")
    parser.add_argument("--check-only", action="store_true", help="Validate without writing XML")
    parser.add_argument(
        "--skip-nonfinal", action="store_true",
        help="Skip non-final (REVIEW/PENDING/…) units instead of rejecting the "
        "whole run; skipped units are listed in the report and never written. "
        "Use for batch writebacks where a few units await review; the skipped "
        "units must be resolved and written by a later generation.",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing output/report")
    args = parser.parse_args()

    try:
        xml_path = resolve_path(args.xml)
        if not xml_path.is_file():
            raise WritebackError(f"Source XML does not exist: {xml_path}")
        source_bytes = xml_path.read_bytes()
        source_hash = sha256_bytes(source_bytes)
        source_root, source_strings = parsed_strings(source_bytes, "Source XML")

        items, status_counts = {}, dict(Counter())
        skipped_units: list[dict[str, str]] = []
        result_paths: list[Path] = []
        patch_paths = [resolve_path(value) for value in args.patch]
        if args.result or args.result_glob:
            result_paths = collect_result_paths(args.result, args.result_glob)
            items, status_counts, skipped_units = collect_translations(
                result_paths, source_hash, source_strings,
                skip_nonfinal=args.skip_nonfinal,
            )
        for patch_path in patch_paths:
            patch_items = load_patch(patch_path, source_strings)
            for index, item in patch_items.items():
                if index in items:
                    raise WritebackError(
                        f"Patch xml_index {index} overlaps with a result entry"
                    )
                items[index] = item
            status_counts["TRANSLATED"] = status_counts.get("TRANSLATED", 0) + len(patch_items)
        if not items and not patch_paths and not result_paths:
            raise WritebackError("No result files or patch files supplied")

        translated_count = status_counts.get("TRANSLATED", 0)
        keep_count = status_counts.get("KEEP", 0)
        report: dict[str, Any] = {
            "source_xml": relative(xml_path),
            "source_sha256": source_hash,
            "string_count": len(source_strings),
            "result_count": len(items),
            "translated_count": translated_count,
            "keep_count": keep_count,
            "result_files": [relative(path) for path in result_paths],
            "patch_files": [relative(path) for path in patch_paths],
            "check_only": bool(args.check_only),
            "skipped_units": skipped_units,
            "skipped_count": len(skipped_units),
        }

        if args.check_only:
            # 预知诊断：KEEP 条目里有多少条会真正回写（当前 Dest != Source）。
            predicted_keep_fix = sum(
                1
                for index, item in items.items()
                if str(item.get("status", "")).upper() == "KEEP"
                and (source_strings[index].findtext("Dest") or "")
                != str(item.get("translation", ""))
            )
            report["prewrite_validation"] = "passed"
            report["predicted_keep_fix_count"] = predicted_keep_fix
            if args.report:
                write_report(resolve_path(args.report), report, args.force)
            print(
                f"Pre-write validation passed: {len(items)} result(s), "
                f"{translated_count} TRANSLATED, {keep_count} KEEP "
                f"({predicted_keep_fix} will be restored to source), "
                f"{len(skipped_units)} skipped non-final, "
                f"{len(source_strings)} XML String(s)."
            )
            return 0

        if not args.output:
            raise WritebackError("--output is required unless --check-only is used")
        output_path = resolve_path(args.output)
        if output_path == xml_path:
            raise WritebackError("Refusing to overwrite the source XML path")
        canonical_re = re.compile(r"^[A-Za-z0-9_.\-]+_english_chinese_translated\.xml$")
        if not canonical_re.fullmatch(output_path.name):
            raise WritebackError(
                f"Writeback output must be named <plugin>_english_chinese_translated.xml "
                f"(no version/label suffixes; version history lives in PROGRESS.md): {output_path.name}"
            )
        siblings = [
            item
            for item in output_path.parent.glob("*_translated*.xml")
            if item != output_path
        ]
        if siblings:
            names = ", ".join(sorted(relative(item) for item in siblings))
            raise WritebackError(
                "Output directory would accumulate multiple translated XML versions; "
                "exactly one canonical writeback artifact is allowed per MOD directory. "
                f"Archive or remove these first: {names}"
            )
        if output_path.exists() and not args.force:
            raise WritebackError(f"Output already exists; use --force to replace: {output_path}")

        if not items:
            # 幂等重跑（空 patch / 无变更）：不改文件，报告 0 变更成功
            noop_report = {
                "source_xml": relative(xml_path),
                "source_sha256": source_hash,
                "string_count": len(source_strings),
                "result_count": 0,
                "translated_count": 0,
                "keep_count": 0,
                "result_files": [],
                "patch_files": [relative(path) for path in patch_paths],
                "check_only": False,
                "noop": True,
                "output_xml": relative(output_path),
            }
            if args.report:
                write_report(resolve_path(args.report), noop_report, args.force)
            print(
                f"No-op writeback: 0 Dest change(s) (idempotent rerun), "
                f"{len(source_strings)} XML String(s)."
            )
            return 0


        source_text = source_bytes.decode("utf-8-sig")
        newline = "\r\n" if "\r\n" in source_text else "\n"
        spans = raw_dest_spans(source_text, len(source_strings))
        replacements: list[tuple[int, int, str]] = []
        changed_count = 0
        keep_fix_count = 0
        for index, item in items.items():
            # KEEP 的语义是「最终 Dest == Source」，不是「什么都不做」：
            # Dest 里若残留旧错误译文，必须回写为 Source（keep_fix_count 单独计数）。
            start, end = spans[index]
            replacement = xml_text(str(item["translation"]), newline)
            if source_text[start:end] != replacement:
                changed_count += 1
                if str(item.get("status", "")).upper() == "KEEP":
                    keep_fix_count += 1
            replacements.append((start, end, replacement))

        output_text = source_text
        if replacements:
            # single-pass splice: replacements are non-overlapping, sorted spans;
            # rebuilding the whole text per replacement was O(replacements × docsize)
            parts: list[str] = []
            last = 0
            for start, end, replacement in sorted(replacements):
                parts.append(source_text[last:start])
                parts.append(replacement)
                last = end
            parts.append(source_text[last:])
            output_text = "".join(parts)

        output_bytes = output_text.encode("utf-8")
        if source_bytes.startswith(UTF8_BOM):
            output_bytes = UTF8_BOM + output_bytes

        verify_output(source_root, source_strings, output_bytes, items)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写回：先写同目录临时文件再替换目标，避免写回中断留下悬空/半截文件。
        # Windows 上 os.replace 对可能被文件监视/杀毒短暂持有的目标会 PermissionError，
        # fallback：先 rename 目标到 .bak（同目录，不跨卷），再 rename 临时文件到目标。
        fd, tmp_path = tempfile.mkstemp(dir=str(output_path.parent), prefix=".druadach-tmp-", suffix=".xml")
        bak_path = str(output_path) + ".old"
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(output_bytes)
            try:
                os.replace(tmp_path, output_path)
            except OSError:
                # fallback: 两步 rename 绕开覆盖删除语义
                if os.path.exists(bak_path):
                    os.unlink(bak_path)
                os.rename(output_path, bak_path)
                try:
                    os.rename(tmp_path, output_path)
                except BaseException:
                    os.rename(bak_path, output_path)  # 回滚
                    raise
                else:
                    try:
                        os.unlink(bak_path)
                    except OSError:
                        pass
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        output_hash = sha256_bytes(output_bytes)
        report.update(
            {
                "output_xml": relative(output_path),
                "output_sha256": output_hash,
                "changed_dest_count": changed_count,
                "keep_fix_count": keep_fix_count,
                "postwrite_validation": "passed",
            }
        )
        if args.report:
            write_report(resolve_path(args.report), report, args.force)

        print(
            f"Wrote {relative(output_path)}: {changed_count} Dest change(s) "
            f"({keep_fix_count} KEEP restored to source), "
            f"{keep_count} KEEP, {len(skipped_units)} skipped non-final, "
            f"post-write validation passed."
        )
        print(f"Source SHA-256: {source_hash}")
        print(f"Output SHA-256: {output_hash}")
        return 0
    except WritebackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

