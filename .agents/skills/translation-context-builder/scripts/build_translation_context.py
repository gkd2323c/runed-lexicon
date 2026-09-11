#!/usr/bin/env python3
"""Build read-only, Agent-ready translation context batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DICTIONARY_DIR = PROJECT_ROOT / "dictionary"
FORM_ID_RE = re.compile(r"^\[([0-9A-Fa-f]{8})\]$")
WORD_RE = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ'’]+")
TERM_RECORD_PREFIXES = {
    "ACTI", "ALCH", "AMMO", "ARMO", "BOOK", "CELL", "CLAS", "CONT",
    "DOOR", "ENCH", "FACT", "FLOR", "FURN", "INGR", "KEYM", "LCTN",
    "MGEF", "MISC", "NPC_", "PERK", "QUST", "RACE", "SCRL", "SLGM",
    "SPEL", "TREE", "WEAP", "WRLD",
}

# For prose-like records such as INFO dialogue, a dictionary hit should mean
# "this phrase is plausibly the same named/canonical thing as in the base
# game", not merely "these English letters occurred somewhere in an official
# XML". Single generic words from effects, doors, books, UI verbs, etc. create
# high-noise matches (for example Between -> 两界间 or Gate -> 大门) and are
# therefore excluded from automatic dialogue hints. Multi-word FULL names are
# still useful because they are much less likely to be accidental prose
# overlap.
DIALOGUE_SINGLE_TOKEN_TERM_PREFIXES = {
    "NPC_", "RACE", "FACT", "LCTN", "WRLD", "ALCH", "INGR",
}
PROSE_RECORD_PREFIXES = {"INFO", "DIAL", "BOOK", "MESG"}


@dataclass(frozen=True)
class XmlEntry:
    index: int
    list_id: str
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=None)
def relative(path: Path) -> str:
    """Display path relative to PROJECT_ROOT.

    Cached: Path.resolve() hits the filesystem (nt._getfinalpathname on
    Windows) on every call — measured ~0.16ms per call, and the dictionary
    index builder calls this once per String row (37k+ calls = 12s of a
    15s run). The path set is tiny (one per dictionary file), so caching
    collapses the cost to one resolve per distinct file. Cache lifetime =
    one CLI process; filesystem state is assumed stable within a run.
    """
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _raw_normalize_phrase(value: str) -> str:
    tokens = WORD_RE.findall(value.replace("’", "'"))
    return " ".join(token.casefold() for token in tokens)


def _raw_source_ngrams(source: str, max_words: int = 6) -> Iterable[str]:
    tokens = [token.casefold() for token in WORD_RE.findall(source.replace("’", "'"))]
    count = len(tokens)
    for width in range(min(max_words, count), 0, -1):
        for start in range(0, count - width + 1):
            yield " ".join(tokens[start : start + width])


# The game wraps spell/creature names in single quotes and marks possessives
# with a trailing apostrophe ("cast 'Flame Atronach'", "conjure a 'Familiar'",
# "Magnus' notes"). WORD_RE counts ' as a word character, so those tokens came
# out as "'familiar'", "'flame", "atronach'", "magnus'" and could never equal
# an index key: the official-name lookup silently returned nothing for every
# quoted term, the digest printed "OFF: -" (absence looked real), and the
# translator invented a form. Drop apostrophes that sit outside a word (next to
# a non-alphanumeric or a string edge) before tokenizing. Interior ones survive,
# so "it's" and "Mara's Blessing" keep their shape. Applied on both the index
# side and the query side, so the two stay consistent by construction.
_STRAY_APOSTROPHE_RE = re.compile(r"(?<![A-Za-z0-9])'|'(?![A-Za-z0-9])")


def dequote_stray_apostrophes(value: str) -> str:
    return _STRAY_APOSTROPHE_RE.sub(" ", value.replace("\u2019", "'"))


def normalize_phrase(value: str) -> str:
    return _raw_normalize_phrase(dequote_stray_apostrophes(value))


def source_ngrams(source: str, max_words: int = 6) -> Iterable[str]:
    return _raw_source_ngrams(dequote_stray_apostrophes(source), max_words)


def resolve_path(value: str | None, base: Path = PROJECT_ROOT) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def resolve_mod_dir(input_value: str | None) -> Path | None:
    if not input_value:
        return None
    path = resolve_path(input_value)
    if path is None or not path.exists():
        raise ValueError(f"Input path does not exist: {input_value}")
    if not path.is_dir():
        raise ValueError(f"MOD input must be a directory when used as positional input: {path}")
    return path


def single_file(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(path for path in directory.glob(pattern) if path.is_file())
    if not matches:
        raise ValueError(f"No {label} file found in {directory}")
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise ValueError(f"Multiple {label} files found; pass one explicitly: {names}")
    return matches[0].resolve()


def resolve_inputs(args: argparse.Namespace) -> dict[str, Path | None]:
    mod_dir = resolve_mod_dir(args.input)

    xml_path = resolve_path(args.xml) if args.xml else None
    dialogue_context_path = (
        resolve_path(args.dialogue_context) if args.dialogue_context else None
    )
    mod_terms_path = resolve_path(args.mod_terms) if args.mod_terms else None

    if mod_dir:
        xml_path = xml_path or single_file(mod_dir, "*.xml", "xTranslator XML")
        if mod_terms_path is None:
            candidate = mod_dir / "terms.json"
            mod_terms_path = candidate if candidate.is_file() else None
        elif not mod_terms_path.is_file():
            raise ValueError(f"MOD terms file does not exist: {mod_terms_path}")
        if dialogue_context_path is None:
            candidates = sorted(
                path for path in mod_dir.glob("*dialogue_context*.json") if path.is_file()
            )
            if len(candidates) == 1:
                dialogue_context_path = candidates[0].resolve()
            elif len(candidates) > 1:
                names = ", ".join(path.name for path in candidates)
                raise ValueError(
                    f"Multiple dialogue context JSON files found; pass one explicitly: {names}"
                )

    required = {
        "xml": xml_path,
    }
    missing = [name for name, path in required.items() if path is None or not path.is_file()]
    if missing:
        raise ValueError(
            "Missing required input file(s): "
            + ", ".join(missing)
            + ". Provide a MOD directory or explicit paths."
        )

    if dialogue_context_path is not None and not dialogue_context_path.is_file():
        raise ValueError(f"Dialogue context JSON does not exist: {dialogue_context_path}")

    return {
        "mod_dir": mod_dir,
        "xml": xml_path,
        "dialogue_context": dialogue_context_path,
        "mod_terms": mod_terms_path,
    }


def load_xml_entries(xml_path: Path) -> tuple[dict[str, str], list[XmlEntry]]:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid xTranslator XML in {xml_path}: {exc}") from exc

    params = {
        child.tag: text_of(child)
        for child in root.findall("./Params/*")
        if isinstance(child.tag, str)
    }
    entries: list[XmlEntry] = []
    for index, node in enumerate(root.findall("./Content/String")):
        entries.append(
            XmlEntry(
                index=index,
                list_id=node.attrib.get("List", ""),
                edid=text_of(node.find("EDID")),
                rec=text_of(node.find("REC")),
                source=text_of(node.find("Source")),
                dest=text_of(node.find("Dest")),
            )
        )
    return params, entries


def iter_dictionary_files(dictionary_dir: Path) -> list[Path]:
    if not dictionary_dir.is_dir():
        raise ValueError(f"Dictionary directory does not exist: {dictionary_dir}")
    files = sorted(path for path in dictionary_dir.rglob("*.xml") if path.is_file())
    if not files:
        raise ValueError(f"No dictionary XML files found in: {dictionary_dir}")
    return files


def is_term_like_dictionary_record(rec: str, normalized_source: str) -> bool:
    prefix = rec.split(":", 1)[0]
    if prefix in TERM_RECORD_PREFIXES:
        return True
    return rec == "GMST:DATA" and " " in normalized_source


def build_official_dictionary_index(
    dictionary_dir: Path,
) -> tuple[dict[str, list[DictionaryHit]], list[dict[str, object]]]:
    index: dict[str, list[DictionaryHit]] = defaultdict(list)
    files_meta: list[dict[str, object]] = []

    for xml_path in iter_dictionary_files(dictionary_dir):
        rel_file = relative(xml_path)  # hoisted: one path resolution per file, not per row
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"Invalid dictionary XML in {xml_path}: {exc}") from exc

        file_string_count = 0
        indexed_count = 0
        for node in root.findall("./Content/String"):
            file_string_count += 1
            source = text_of(node.find("Source")).strip()
            dest = text_of(node.find("Dest")).strip()
            rec = text_of(node.find("REC"))
            normalized = normalize_phrase(source)
            if not normalized or not dest or source == dest:
                continue
            if not is_term_like_dictionary_record(rec, normalized):
                continue
            index[normalized].append(
                DictionaryHit(
                    source=source,
                    dest=dest,
                    dictionary_file=rel_file,
                    rec=rec,
                    edid=text_of(node.find("EDID")),
                )
            )
            indexed_count += 1

        files_meta.append(
            {
                "path": rel_file,
                "string_count": file_string_count,
                "indexed_term_count": indexed_count,
            }
        )

    return index, files_meta


def hits_for_source(
    source: str,
    index: dict[str, list[DictionaryHit]],
    max_terms: int,
    target_rec: str = "",
) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    results: list[dict[str, str]] = []

    for phrase in source_ngrams(source):
        if " " not in phrase and len(phrase) < 4:
            continue
        for hit in index.get(phrase, []):
            if not dictionary_hit_allowed_for_target(hit, target_rec):
                continue
            key = (normalize_phrase(hit.source), hit.dest, hit.dictionary_file)
            if key in seen:
                continue
            seen.add(key)
            results.append(asdict(hit))
            if max_terms and len(results) >= max_terms:
                return results
    return results


def dictionary_hit_allowed_for_target(hit: DictionaryHit, target_rec: str) -> bool:
    """Filter automatic official-term hints by how the target text is used.

    Prose records need identity-bearing terminology, not broad translation
    memory.  The complete official XML remains available for explicit lookup
    when the Agent sees a lore term that the conservative automatic pass did
    not attach.
    """
    target_prefix = target_rec.split(":", 1)[0]
    if target_prefix not in PROSE_RECORD_PREFIXES:
        return True

    hit_prefix, separator, hit_field = hit.rec.partition(":")
    if not separator or hit_field != "FULL":
        return False

    token_count = len(normalize_phrase(hit.source).split())
    if token_count >= 2:
        return hit_prefix in TERM_RECORD_PREFIXES

    return hit_prefix in DIALOGUE_SINGLE_TOKEN_TERM_PREFIXES


def load_mod_terms(terms_path: Path | None) -> list[dict[str, str]]:
    """Load machine-readable MOD term rows from terms.json.

    The human DICTIONARY.md is documentation: translation agents (AI) read it
    directly; tools never parse it. terms.json is the structured mirror
    maintained alongside it and is the only machine-side term input.
    """
    if terms_path is None or not terms_path.is_file():
        return []
    try:
        data = json.loads(terms_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid terms JSON in {terms_path}: {exc}") from exc

    rows: list[dict[str, str]] = []
    for term in data.get("terms", []):
        if not isinstance(term, dict):
            continue
        english = str(term.get("english", "")).strip()
        if not english:
            continue
        rows.append(
            {
                "english": english,
                "chinese": str(term.get("zh", "")).strip(),
                "type": "",
                "status": str(term.get("status", "")).strip(),
                "source": "",
                "note": str(term.get("note", "")).strip(),
            }
        )
    return dedupe_dicts(rows)


def dedupe_dicts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    result: list[dict[str, str]] = []
    for item in items:
        key = tuple(sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def mod_terms_for_source(source: str, terms: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized_source = normalize_phrase(source)
    if not normalized_source:
        return []
    hits = []
    for term in terms:
        english = term.get("english", "")
        normalized_term = normalize_phrase(english)
        if not normalized_term:
            continue
        if re.search(rf"\b{re.escape(normalized_term)}\b", normalized_source):
            hits.append(term)
    return hits


def load_dialogue_context(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid dialogue context JSON in {path}: {exc}") from exc


def build_dialogue_indexes(
    dialogue_context: dict[str, object] | None,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    info_index: dict[str, dict[str, object]] = {}
    dial_index: dict[str, dict[str, object]] = {}
    if not dialogue_context:
        return info_index, dial_index

    for dialogue in dialogue_context.get("dialogues", []):
        if not isinstance(dialogue, dict):
            continue
        editor_id = str(dialogue.get("editor_id", ""))
        if editor_id:
            dial_index[editor_id] = dialogue
        infos = dialogue.get("infos", [])
        if not isinstance(infos, list):
            continue
        for ordinal, info in enumerate(infos):
            if not isinstance(info, dict):
                continue
            form_id = normalize_form_id(str(info.get("form_id", "")))
            if form_id:
                info_index[form_id] = {
                    "dialogue": dialogue,
                    "info": info,
                    "info_ordinal": ordinal,
                }
    return info_index, dial_index


def normalize_form_id(value: str) -> str:
    cleaned = value.strip().strip("[]").upper()
    if re.fullmatch(r"[0-9A-F]{8}", cleaned):
        return cleaned
    return ""


def form_id_from_edid(edid: str) -> str:
    match = FORM_ID_RE.match(edid.strip())
    if not match:
        return ""
    return match.group(1).upper()


def compact_record(record: dict[str, object], excluded: set[str] | None = None) -> dict[str, object]:
    excluded = excluded or set()
    return {key: value for key, value in record.items() if key not in excluded}


def build_info_context(match: dict[str, object]) -> dict[str, object]:
    dialogue = match["dialogue"]
    info = match["info"]
    infos = dialogue.get("infos", []) if isinstance(dialogue.get("infos"), list) else []
    ordinal = int(match["info_ordinal"])

    sibling_refs = []
    for sibling_index, sibling in enumerate(infos):
        if not isinstance(sibling, dict):
            continue
        sibling_refs.append(
            {
                "form_id": sibling.get("form_id", ""),
                "editor_id": sibling.get("editor_id", ""),
                "prompt": sibling.get("prompt", ""),
                "position": sibling_index,
                "is_current": sibling_index == ordinal,
            }
        )

    context: dict[str, object] = {
        "join_type": "info_form_id",
        "dialogue": compact_record(dialogue, {"infos"}),
        "info": info,
        "info_position_in_dialogue": ordinal,
        "sibling_infos": sibling_refs,
        "field_presence": {
            "responses": "responses" in info,
            "conditions": "conditions" in info,
            "speaker_candidates": "speaker_candidates" in info,
            "previous_info": any(
                key in info for key in ("previous_info", "previous_info_form_id")
            ),
        },
    }
    return context


def build_dialogue_context_for_entry(
    entry: XmlEntry,
    info_index: dict[str, dict[str, object]],
    dial_index: dict[str, dict[str, object]],
) -> dict[str, object]:
    rec_prefix = entry.rec.split(":", 1)[0]
    if rec_prefix == "INFO":
        form_id = form_id_from_edid(entry.edid)
        if not form_id:
            return {
                "status": "unmatched",
                "reason": "INFO EDID is not a bracketed FormID",
            }
        match = info_index.get(form_id)
        if not match:
            return {
                "status": "unmatched",
                "reason": "No INFO FormID match in dialogue context JSON",
                "form_id": form_id,
            }
        return {"status": "matched", **build_info_context(match)}

    if rec_prefix == "DIAL" and entry.edid in dial_index:
        dialogue = dial_index[entry.edid]
        return {
            "status": "matched",
            "join_type": "dial_editor_id",
            "dialogue": compact_record(dialogue, {"infos"}),
            "child_info_refs": [
                {
                    "form_id": info.get("form_id", ""),
                    "editor_id": info.get("editor_id", ""),
                    "prompt": info.get("prompt", ""),
                    "position": index,
                }
                for index, info in enumerate(dialogue.get("infos", []))
                if isinstance(info, dict)
            ],
        }

    return {"status": "not_applicable", "reason": "No deterministic dialogue join for this REC"}


def select_entries(
    entries: list[XmlEntry],
    rec_filters: set[str],
    limit: int,
    include_translated: bool,
) -> list[XmlEntry]:
    candidates = [
        entry
        for entry in entries
        if entry.source
        and (include_translated or entry.source == entry.dest)
        and (not rec_filters or entry.rec in rec_filters)
    ]

    if limit == 0 or len(rec_filters) <= 1:
        return candidates if limit == 0 else candidates[:limit]

    buckets = {
        rec: [entry for entry in candidates if entry.rec == rec]
        for rec in sorted(rec_filters)
    }
    positions = {rec: 0 for rec in buckets}
    selected: list[XmlEntry] = []

    while len(selected) < limit:
        added = False
        for rec in sorted(buckets):
            position = positions[rec]
            if position >= len(buckets[rec]):
                continue
            selected.append(buckets[rec][position])
            positions[rec] = position + 1
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


def chunked(entries: list[dict[str, object]], batch_size: int) -> list[dict[str, object]]:
    if batch_size <= 0:
        return [{"batch_index": 0, "entry_count": len(entries), "entries": entries}]
    return [
        {
            "batch_index": index,
            "entry_count": len(entries[start : start + batch_size]),
            "entries": entries[start : start + batch_size],
        }
        for index, start in enumerate(range(0, len(entries), batch_size))
    ]


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    paths = resolve_inputs(args)
    xml_path = paths["xml"]
    mod_terms_path = paths["mod_terms"]
    assert isinstance(xml_path, Path)

    dictionary_dir = resolve_path(args.dictionary_dir) or DEFAULT_DICTIONARY_DIR
    if not dictionary_dir.is_dir():
        raise ValueError(f"Dictionary directory does not exist: {dictionary_dir}")

    xml_params, entries = load_xml_entries(xml_path)
    if mod_terms_path is None:
        print(
            "note: no terms.json found for this MOD; terminology.mod_terms_hits will be empty",
            file=sys.stderr,
        )
    mod_terms = load_mod_terms(mod_terms_path if isinstance(mod_terms_path, Path) else None)
    official_index, official_files = build_official_dictionary_index(dictionary_dir)

    dialogue_context_path = paths["dialogue_context"]
    dialogue_context = load_dialogue_context(
        dialogue_context_path if isinstance(dialogue_context_path, Path) else None
    )
    info_index, dial_index = build_dialogue_indexes(dialogue_context)

    rec_filters = set(args.rec)
    if args.index_file:
        index_path = resolve_path(args.index_file)
        if not index_path or not index_path.is_file():
            raise ValueError(f"Index file does not exist: {args.index_file}")
        target_indices = set()
        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                target_indices.add(int(line))
        selected = [entry for entry in entries if entry.index in target_indices]
    else:
        selected = select_entries(
            entries=entries,
            rec_filters=rec_filters,
            limit=args.limit,
            include_translated=args.include_translated,
        )
    duplicate_counts = Counter(entry.source for entry in entries if entry.source)

    output_entries: list[dict[str, object]] = []
    for entry in selected:
        form_id = form_id_from_edid(entry.edid)
        unit_id = (
            f"{entry.rec}:{form_id}#{entry.index}"
            if form_id
            else f"xml-index:{entry.index}"
        )
        output_entries.append(
            {
                "translation_unit_id": unit_id,
                "xml": {
                    **asdict(entry),
                    "form_id": form_id,
                    "duplicate_count": duplicate_counts[entry.source],
                    "source_equals_dest": entry.source == entry.dest,
                },
                "dialogue_context": build_dialogue_context_for_entry(
                    entry, info_index=info_index, dial_index=dial_index
                ),
                "terminology": {
                    "mod_terms_hits": mod_terms_for_source(entry.source, mod_terms),
                    "official_dictionary_hits": hits_for_source(
                        entry.source,
                        official_index,
                        args.max_official_terms,
                        target_rec=entry.rec,
                    ),
                },
            }
        )

    untranslated_total = sum(
        1 for entry in entries if entry.source and entry.source == entry.dest
    )
    selected_unmatched = sum(
        1
        for entry in output_entries
        if entry["dialogue_context"].get("status") == "unmatched"
    )

    return {
        "schema_version": 1,
        "purpose": "translation_context_only_no_translation_no_xml_writeback",
        "inputs": {
            "mod_dir": relative(paths["mod_dir"]) if isinstance(paths["mod_dir"], Path) else None,
            "xtranslator_xml": {
                "path": relative(xml_path),
                "sha256": sha256_file(xml_path),
                "params": xml_params,
                "string_count": len(entries),
            },
            "dialogue_context_json": (
                {
                    "path": relative(dialogue_context_path),
                    "sha256": sha256_file(dialogue_context_path),
                    "dialogue_count": len(dialogue_context.get("dialogues", []))
                    if isinstance(dialogue_context, dict)
                    else 0,
                    "info_index_count": len(info_index),
                }
                if isinstance(dialogue_context_path, Path)
                else None
            ),
            "mod_terms": {
                "path": relative(mod_terms_path) if isinstance(mod_terms_path, Path) else None,
                "sha256": sha256_file(mod_terms_path) if isinstance(mod_terms_path, Path) else "",
                "term_count": len(mod_terms),
            },
            "official_dictionary": {
                "directory": relative(dictionary_dir),
                "file_count": len(official_files),
                "indexed_term_count": sum(
                    int(item["indexed_term_count"]) for item in official_files
                ),
                "files": official_files,
            },
        },
        "selection": {
            "rule": "source_nonempty_and_source_equals_dest_unless_include_translated",
            "rec": sorted(rec_filters),
            "include_translated": args.include_translated,
            "limit": args.limit,
            "batch_size": args.batch_size,
            "selected_count": len(output_entries),
            "untranslated_candidate_total": untranslated_total,
            "selected_unmatched_dialogue_context_count": selected_unmatched,
        },
        "batches": chunked(output_entries, args.batch_size),
    }


def write_output(payload: dict[str, object], output: str | None, force: bool) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        print(rendered)
        return

    output_path = resolve_path(output)
    assert output_path is not None
    if output_path.exists() and not force:
        raise ValueError(
            f"Output file already exists: {output_path}. Use --force to replace it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")

    entry_count = payload["selection"]["selected_count"]
    batch_count = len(payload["batches"])
    print(f"Wrote {entry_count} entries in {batch_count} batch(es) to {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build structured translation context batches without translating or writing XML."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="MOD directory containing the xTranslator XML plus optional terms.json / dialogue context JSON",
    )
    parser.add_argument("--xml", help="Explicit xTranslator XML path")
    parser.add_argument("--dialogue-context", help="Explicit xEdit dialogue context JSON path")
    parser.add_argument(
        "--mod-terms",
        help="Explicit MOD terms.json path (machine-readable term source)",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=str(DEFAULT_DICTIONARY_DIR),
        help="Official dictionary XML directory",
    )
    parser.add_argument(
        "--rec",
        action="append",
        default=[],
        help="Include only this REC type; may be repeated",
    )
    parser.add_argument("--limit", type=int, default=20, help="Maximum entries; 0 means all")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Entries per output batch; 0 keeps one batch",
    )
    parser.add_argument(
        "--max-official-terms",
        type=int,
        default=12,
        help="Maximum official dictionary hits per entry; 0 means unlimited",
    )
    parser.add_argument(
        "--include-translated",
        action="store_true",
        help="Include entries even when Source and Dest differ",
    )
    parser.add_argument(
        "--index-file",
        help="Path to a line-delimited file of integer XML indices to select directly",
    )
    parser.add_argument("--output", help="Optional output JSON path")
    parser.add_argument("--force", action="store_true", help="Replace existing output")
    args = parser.parse_args()

    if not args.input and not args.xml:
        parser.error("provide a MOD directory or --xml plus explicit companion files")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.batch_size < 0:
        parser.error("--batch-size must be >= 0")
    if args.max_official_terms < 0:
        parser.error("--max-official-terms must be >= 0")
    return args


def main() -> int:
    args = parse_args()
    try:
        payload = build_payload(args)
        write_output(payload, args.output, args.force)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
