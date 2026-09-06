#!/usr/bin/env python3
"""Prepare structured, read-only translation batches from xTranslator XML."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DICTIONARY_DIR = PROJECT_ROOT / "dictionary"
WORD_RE = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ'’]+")
TERM_RECORD_PREFIXES = {
    "ACTI", "ALCH", "ARMO", "BOOK", "CELL", "CLAS", "CONT", "DOOR",
    "ENCH", "FACT", "FLOR", "FURN", "INGR", "LCTN", "MGEF", "MISC",
    "NPC_", "PERK", "QUST", "RACE", "SPEL", "TREE", "WEAP", "WRLD",
}


@dataclass(frozen=True)
class XmlEntry:
    index: int
    edid: str
    rec: str
    source: str
    dest: str


@dataclass(frozen=True)
class DictionaryHit:
    source: str
    dest: str
    dictionary_file: str
    rec: str
    edid: str


def text_of(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def normalize_phrase(value: str) -> str:
    tokens = WORD_RE.findall(value.replace("’", "'"))
    return " ".join(token.casefold() for token in tokens)


def resolve_mod_xml(path_value: str) -> tuple[Path, Path | None]:
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()

    if not path.exists():
        raise ValueError(f"Input path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() != ".xml":
            raise ValueError(f"Input file is not XML: {path}")
        return path, path.parent if path.parent.parent.name == "mods" else None

    xml_files = sorted(path.glob("*.xml"))
    if not xml_files:
        raise ValueError(f"No XML files found in directory: {path}")
    if len(xml_files) != 1:
        names = ", ".join(item.name for item in xml_files)
        raise ValueError(
            f"Directory contains multiple XML files; specify one explicitly: {names}"
        )
    return xml_files[0], path


def check_mod_memory_files(mod_dir: Path | None) -> dict[str, str] | None:
    if mod_dir is None:
        return None

    context_path = mod_dir / "CONTEXT.md"
    dictionary_path = mod_dir / "DICTIONARY.md"
    missing = [
        path.name for path in (context_path, dictionary_path) if not path.is_file()
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(
            f"Missing required MOD context file(s): {joined}. "
            "Stop before translation and ask the user whether to create them."
        )

    return {
        "context": str(context_path.relative_to(PROJECT_ROOT)),
        "dictionary": str(dictionary_path.relative_to(PROJECT_ROOT)),
    }


def load_entries(xml_path: Path) -> list[XmlEntry]:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in {xml_path}: {exc}") from exc

    entries: list[XmlEntry] = []
    for index, node in enumerate(root.findall("./Content/String")):
        entries.append(
            XmlEntry(
                index=index,
                edid=text_of(node.find("EDID")),
                rec=text_of(node.find("REC")),
                source=text_of(node.find("Source")),
                dest=text_of(node.find("Dest")),
            )
        )
    return entries


def iter_dictionary_files(dictionary_dir: Path) -> Iterable[Path]:
    if not dictionary_dir.is_dir():
        raise ValueError(f"Dictionary directory does not exist: {dictionary_dir}")
    files = sorted(path for path in dictionary_dir.rglob("*.xml") if path.is_file())
    if not files:
        raise ValueError(f"No dictionary XML files found in: {dictionary_dir}")
    # The dictionary directory defines the trust boundary. Every XML file
    # directly under it is an official dictionary source; filenames carry no
    # special meaning and no filename-specific priority is applied.
    return files


def is_term_like_dictionary_record(rec: str, normalized_source: str) -> bool:
    prefix = rec.split(":", 1)[0]
    if prefix in TERM_RECORD_PREFIXES:
        return True
    if rec == "GMST:DATA" and " " in normalized_source:
        return True
    return False


def build_dictionary_index(
    dictionary_dir: Path,
) -> dict[str, list[DictionaryHit]]:
    index: dict[str, list[DictionaryHit]] = defaultdict(list)

    for xml_path in iter_dictionary_files(dictionary_dir):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"Invalid dictionary XML in {xml_path}: {exc}") from exc

        for node in root.findall("./Content/String"):
            source = text_of(node.find("Source")).strip()
            dest = text_of(node.find("Dest")).strip()
            normalized = normalize_phrase(source)
            rec = text_of(node.find("REC"))
            if not normalized or not dest or source == dest:
                continue
            if not is_term_like_dictionary_record(rec, normalized):
                continue
            index[normalized].append(
                DictionaryHit(
                    source=source,
                    dest=dest,
                    dictionary_file=str(xml_path.relative_to(dictionary_dir)),
                    rec=rec,
                    edid=text_of(node.find("EDID")),
                )
            )
    return index


def source_ngrams(source: str, max_words: int = 6) -> Iterable[str]:
    tokens = [token.casefold() for token in WORD_RE.findall(source.replace("’", "'"))]
    count = len(tokens)
    for width in range(min(max_words, count), 0, -1):
        for start in range(0, count - width + 1):
            yield " ".join(tokens[start : start + width])


def official_terms_for(
    source: str,
    dictionary_index: dict[str, list[DictionaryHit]],
    max_terms: int,
) -> list[DictionaryHit]:
    seen: set[tuple[str, str]] = set()
    results: list[DictionaryHit] = []

    for phrase in source_ngrams(source):
        # Single-character/very short single-word matches are usually noise.
        if " " not in phrase and len(phrase) < 4:
            continue
        for hit in dictionary_index.get(phrase, []):
            key = (normalize_phrase(hit.source), hit.dest)
            if key in seen:
                continue
            seen.add(key)
            results.append(hit)
            if max_terms and len(results) >= max_terms:
                return results
    return results


def select_entries(
    entries: list[XmlEntry],
    rec_filters: set[str],
    limit: int,
) -> list[XmlEntry]:
    candidates = [
        entry
        for entry in entries
        if entry.source
        and entry.source == entry.dest
        and (not rec_filters or entry.rec in rec_filters)
    ]

    if limit == 0 or len(rec_filters) <= 1:
        return candidates if limit == 0 else candidates[:limit]

    # A limited multi-REC batch should represent each requested record type
    # when matching entries exist. Keep XML order within each REC while taking
    # one entry per REC in rounds until the limit is reached.
    buckets = {
        rec: [entry for entry in candidates if entry.rec == rec]
        for rec in sorted(rec_filters)
    }
    positions = {rec: 0 for rec in buckets}
    selected: list[XmlEntry] = []

    while len(selected) < limit:
        added_this_round = False
        for rec in sorted(buckets):
            position = positions[rec]
            bucket = buckets[rec]
            if position >= len(bucket):
                continue
            selected.append(bucket[position])
            positions[rec] = position + 1
            added_this_round = True
            if len(selected) >= limit:
                break
        if not added_this_round:
            break

    return selected


def build_batch(
    xml_path: Path,
    mod_files: dict[str, str] | None,
    entries: list[XmlEntry],
    selected: list[XmlEntry],
    dictionary_index: dict[str, list[DictionaryHit]],
    rec_filters: set[str],
    max_terms: int,
) -> dict[str, object]:
    duplicate_counts = Counter(entry.source for entry in entries if entry.source)

    batch_entries = []
    for entry in selected:
        batch_entries.append(
            {
                **asdict(entry),
                "duplicate_count": duplicate_counts[entry.source],
                "official_terms": [
                    asdict(hit)
                    for hit in official_terms_for(
                        entry.source,
                        dictionary_index,
                        max_terms=max_terms,
                    )
                ],
            }
        )

    untranslated_total = sum(
        1 for entry in entries if entry.source and entry.source == entry.dest
    )

    return {
        "schema_version": 1,
        "source_xml": str(xml_path.relative_to(PROJECT_ROOT)),
        "mod_memory_files": mod_files,
        "selection": {
            "rule": "source_equals_dest_and_source_nonempty",
            "rec": sorted(rec_filters),
            "selected_count": len(selected),
            "untranslated_candidate_total": untranslated_total,
        },
        "entries": batch_entries,
    }


def write_output(payload: dict[str, object], output: str | None, force: bool) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        print(rendered)
        return

    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path = output_path.resolve()

    if output_path.exists() and not force:
        raise ValueError(
            f"Output file already exists: {output_path}. Use --force to replace it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['entries'])} entries to {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare structured translation batches from xTranslator XML."
    )
    parser.add_argument("input", help="MOD directory or xTranslator XML file")
    parser.add_argument(
        "--rec",
        action="append",
        default=[],
        help="Include only this REC type; may be repeated",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum entries to emit; 0 means all (default: 20)",
    )
    parser.add_argument(
        "--max-official-terms",
        type=int,
        default=12,
        help="Maximum official dictionary hits attached per entry; 0 means unlimited",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=str(DEFAULT_DICTIONARY_DIR),
        help="Directory containing official dictionary XML files",
    )
    parser.add_argument("--output", help="Optional output JSON path")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow replacing an existing --output file",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.max_official_terms < 0:
        parser.error("--max-official-terms must be >= 0")
    return args


def main() -> int:
    args = parse_args()
    try:
        xml_path, mod_dir = resolve_mod_xml(args.input)
        mod_files = check_mod_memory_files(mod_dir)
        entries = load_entries(xml_path)

        dictionary_dir = Path(args.dictionary_dir)
        if not dictionary_dir.is_absolute():
            dictionary_dir = PROJECT_ROOT / dictionary_dir
        dictionary_index = build_dictionary_index(dictionary_dir.resolve())

        rec_filters = set(args.rec)
        selected = select_entries(entries, rec_filters, args.limit)
        payload = build_batch(
            xml_path=xml_path,
            mod_files=mod_files,
            entries=entries,
            selected=selected,
            dictionary_index=dictionary_index,
            rec_filters=rec_filters,
            max_terms=args.max_official_terms,
        )
        write_output(payload, args.output, args.force)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

