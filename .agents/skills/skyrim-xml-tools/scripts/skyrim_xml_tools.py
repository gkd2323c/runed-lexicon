#!/usr/bin/env python3
"""Read-only helpers for xTranslator XML used by skyrim-mod-translator.

The first version deliberately does not modify XML.  It provides three
operations needed by translation agents:

* inspect      Summarize an xTranslator XML file.
* untranslated List entries whose Source and Dest are still identical.
* lookup       Search every official dictionary XML under dictionary/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DICTIONARY_DIR = PROJECT_ROOT / "dictionary"


@dataclass(frozen=True)
class Entry:
    index: int
    edid: str
    rec: str
    source: str
    dest: str


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def resolve_xml_target(raw_target: str) -> Path:
    """Resolve either an XML file or a MOD directory containing one XML file."""
    target = Path(raw_target)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target = target.resolve()

    if target.is_file():
        if target.suffix.lower() != ".xml":
            raise ValueError(f"Target is not an XML file: {target}")
        return target

    if target.is_dir():
        xml_files = sorted(target.glob("*.xml"))
        if not xml_files:
            raise ValueError(f"No XML file found in directory: {target}")
        if len(xml_files) > 1:
            names = ", ".join(path.name for path in xml_files)
            raise ValueError(
                f"More than one XML file found in {target}; specify one explicitly: {names}"
            )
        return xml_files[0].resolve()

    raise ValueError(f"Target does not exist: {target}")


def load_entries(xml_path: Path) -> list[Entry]:
    """Parse xTranslator XML and return its String entries in original order."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {xml_path}: {exc}") from exc

    root = tree.getroot()
    strings = root.findall("./Content/String")
    return [
        Entry(
            index=index,
            edid=_text(node.find("EDID")),
            rec=_text(node.find("REC")),
            source=_text(node.find("Source")),
            dest=_text(node.find("Dest")),
        )
        for index, node in enumerate(strings, start=1)
    ]


def relative_display(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def inspect_command(args: argparse.Namespace) -> int:
    xml_path = resolve_xml_target(args.target)
    entries = load_entries(xml_path)

    rec_counts = Counter(entry.rec for entry in entries)
    source_counts = Counter(entry.source for entry in entries if entry.source)
    duplicate_sources = {
        source: count for source, count in source_counts.items() if count > 1
    }
    source_eq_dest = sum(entry.source == entry.dest for entry in entries)
    empty_dest = sum(not entry.dest for entry in entries)

    payload = {
        "file": relative_display(xml_path),
        "strings": len(entries),
        "source_equals_dest": source_eq_dest,
        "source_differs_from_dest": len(entries) - source_eq_dest,
        "empty_dest": empty_dest,
        "record_types": dict(rec_counts.most_common()),
        "duplicate_source_values": len(duplicate_sources),
        "top_duplicate_sources": [
            {"source": source, "count": count}
            for source, count in sorted(
                duplicate_sources.items(), key=lambda item: (-item[1], item[0])
            )[: args.duplicates]
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"File: {payload['file']}")
    print(f"Strings: {payload['strings']}")
    print(f"Source == Dest: {payload['source_equals_dest']}")
    print(f"Source != Dest: {payload['source_differs_from_dest']}")
    print(f"Empty Dest: {payload['empty_dest']}")
    print("Record types:")
    for rec, count in rec_counts.most_common():
        print(f"  {rec or '<empty>'}: {count}")
    print(f"Duplicate Source values: {len(duplicate_sources)}")
    if payload["top_duplicate_sources"]:
        print("Top duplicate Source values:")
        for item in payload["top_duplicate_sources"]:
            source = item["source"].replace("\r", "\\r").replace("\n", "\\n")
            print(f"  {item['count']:>4}  {source}")
    return 0


def _entry_payload(entry: Entry) -> dict[str, object]:
    return asdict(entry)


# ---------------------------------------------------------------- termrules

RULE_BLOCK_RE = re.compile(
    r"StartRule\s*\r?\n(.*?)\r?\nEndRule", re.DOTALL
)
RULE_LINE_RE = re.compile(r"^(Search|Replace|Pattern|select|mode|AllLists)=(.*?)\s*$", re.MULTILINE)


def parse_term_rules(path: Path) -> list[dict[str, str]]:
    """Parse an xTranslator term-conversion rule file into (search, replace) pairs.

    Format is StartRule/EndRule blocks with Search= / Replace= lines. Only blocks
    that carry both Search and Replace are returned. All fields are kept as raw
    strings (the rule file is plain text, not XML).
    """
    text = path.read_text(encoding="utf-8-sig")
    rules = []
    for block in RULE_BLOCK_RE.finditer(text):
        body = block.group(1)
        fields = {}
        for m in RULE_LINE_RE.finditer(body):
            fields[m.group(1)] = m.group(2)
        search = fields.get("Search", "")
        replace = fields.get("Replace", "")
        if search and replace:
            rules.append({"search": search, "replace": replace})
    return rules


def termrules_command(args: argparse.Namespace) -> int:
    """Scan a translated XML's Dest fields against a term-conversion rule file.

    Heuristic only: reports where a Dest contains a rule's Search text (usually an
    old / non-official translation) that the rule would replace. Read-only — never
    edits the XML. Rules whose Search is pure ASCII are skipped (they are usually
    English-source rewrite rules, not old-Chinese -> new-Chinese fixes).
    """
    xml_path = resolve_xml_target(args.target)
    rules_path = Path(args.rules)
    if not rules_path.is_absolute():
        rules_path = (PROJECT_ROOT / rules_path).resolve()
    if not rules_path.is_file():
        raise ValueError(f"Rules file does not exist: {rules_path}")

    rules = parse_term_rules(rules_path)
    if not rules:
        print(f"No rules parsed from {relative_display(rules_path)}")
        return 0

    # keep only rules whose Search contains Chinese (old-CN -> new-CN fixes)
    cn_rules = [r for r in rules if re.search(r"[\u4e00-\u9fff]", r["search"])]
    min_len = max(1, args.min_len)
    cn_rules = [r for r in cn_rules if len(r["search"]) >= min_len]

    entries = load_entries(xml_path)
    # optional per-REC filter
    wanted_recs = set(args.rec or [])

    hits = []
    seen = set()
    for entry in entries:
        if wanted_recs and entry.rec not in wanted_recs:
            continue
        dst = entry.dest
        if not dst or dst == entry.source:
            continue  # untranslated / KEEP rows cannot carry old-CN residuals
        for rule in cn_rules:
            if rule["search"] not in dst:
                continue
            # Skip only when the dest already carries the FULL replacement AND the
            # replacement is an extension of the search (e.g. search='扎克之塔',
            # replace='姆扎克之塔', dest='姆扎克之塔'): the rule already ran or
            # the search was a sub-string of a longer correct term. Do NOT skip
            # when replace is a *shortening* of search (e.g. search='阿尔贡人',
            # replace='阿尔贡', dest='阿尔贡人') — that is exactly a real hit.
            if rule["replace"] and rule["replace"] in dst and rule["search"] in rule["replace"]:
                continue
            dedup_key = (entry.index, rule["search"])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            hits.append(
                {
                    "xml_index": entry.index,
                    "edid": entry.edid,
                    "rec": entry.rec,
                    "source": entry.source,
                    "dest": dst,
                    "search": rule["search"],
                    "replace": rule["replace"],
                }
            )

    if args.json:
        payload = {
            "file": relative_display(xml_path),
            "rules_file": relative_display(rules_path),
            "rules_total": len(rules),
            "rules_cn": len(cn_rules),
            "hits": hits,
            "total_hits": len(hits),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"File: {relative_display(xml_path)}")
    print(f"Rules: {len(rules)} total, {len(cn_rules)} Chinese-search (old->new)")
    print(f"Hits: {len(hits)}")
    for h in hits:
        dst_short = h["dest"].replace("\r", "\\r").replace("\n", "\\n")
        print(f"[{h['xml_index']}] {h['rec']} | {h['edid']}")
        print(f"  DEST contains {h['search']!r}")
        print(f"  rule suggests -> {h['replace']!r}")
        print(f"  dest: {dst_short[:80]}")
    return 0


def untranslated_command(args: argparse.Namespace) -> int:
    xml_path = resolve_xml_target(args.target)
    entries = load_entries(xml_path)
    wanted_recs = set(args.rec or [])

    matches = [
        entry
        for entry in entries
        if entry.source == entry.dest
        and entry.source != ""
        and (not wanted_recs or entry.rec in wanted_recs)
    ]
    shown = matches if args.limit == 0 else matches[: args.limit]

    if args.json:
        payload = {
            "file": relative_display(xml_path),
            "total_matches": len(matches),
            "shown": len(shown),
            "entries": [_entry_payload(entry) for entry in shown],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"File: {relative_display(xml_path)}")
    print(f"Untranslated (Source == Dest): {len(matches)}")
    if args.limit and len(matches) > len(shown):
        print(f"Showing first {len(shown)} entries (use --limit 0 for all).")
    for entry in shown:
        source = entry.source.replace("\r", "\\r").replace("\n", "\\n")
        print(f"[{entry.index}] {entry.rec} | {entry.edid} | {source}")
    return 0


def iter_dictionary_xml(dictionary_dir: Path) -> Iterable[Path]:
    if not dictionary_dir.is_dir():
        raise ValueError(f"Dictionary directory does not exist: {dictionary_dir}")
    files = sorted(path for path in dictionary_dir.rglob("*.xml") if path.is_file())
    if not files:
        raise ValueError(f"No dictionary XML files found in: {dictionary_dir}")
    # The directory defines official dictionary membership. Do not whitelist,
    # blacklist, or prioritize files by filename.
    return files


def lookup_command(args: argparse.Namespace) -> int:
    dictionary_dir = Path(args.dictionary_dir)
    if not dictionary_dir.is_absolute():
        dictionary_dir = PROJECT_ROOT / dictionary_dir
    dictionary_dir = dictionary_dir.resolve()

    query = args.query
    query_cmp = query.casefold() if args.ignore_case else query
    results: list[dict[str, object]] = []

    for xml_path in iter_dictionary_xml(dictionary_dir):
        for entry in load_entries(xml_path):
            source_cmp = entry.source.casefold() if args.ignore_case else entry.source
            matched = query_cmp in source_cmp if args.contains else query_cmp == source_cmp
            if not matched:
                continue
            results.append(
                {
                    "dictionary": str(xml_path.relative_to(dictionary_dir)),
                    **_entry_payload(entry),
                }
            )
            if args.limit and len(results) >= args.limit:
                break
        if args.limit and len(results) >= args.limit:
            break

    if args.json:
        payload = {
            "query": query,
            "mode": "contains" if args.contains else "exact",
            "ignore_case": args.ignore_case,
            "matches": len(results),
            "entries": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    mode = "contains" if args.contains else "exact"
    print(f"Query: {query!r} ({mode}, ignore_case={args.ignore_case})")
    print(f"Matches shown: {len(results)}")
    for item in results:
        source = str(item["source"]).replace("\r", "\\r").replace("\n", "\\n")
        dest = str(item["dest"]).replace("\r", "\\r").replace("\n", "\\n")
        print(
            f"{item['dictionary']} [{item['index']}] {item['rec']} | "
            f"{item['edid']} | {source} => {dest}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only inspection and lookup tools for xTranslator XML."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Summarize one xTranslator XML or MOD directory."
    )
    inspect_parser.add_argument("target", help="XML file or directory containing one XML file.")
    inspect_parser.add_argument(
        "--duplicates",
        type=int,
        default=10,
        help="Number of most frequent duplicate Source strings to show (default: 10).",
    )
    inspect_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    inspect_parser.set_defaults(func=inspect_command)

    untranslated_parser = subparsers.add_parser(
        "untranslated", help="List non-empty entries where Source == Dest."
    )
    untranslated_parser.add_argument(
        "target", help="XML file or directory containing one XML file."
    )
    untranslated_parser.add_argument(
        "--rec",
        action="append",
        help="Only include this exact REC value; repeat to include multiple types.",
    )
    untranslated_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum entries to show; 0 means all (default: 50).",
    )
    untranslated_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    untranslated_parser.set_defaults(func=untranslated_command)

    lookup_parser = subparsers.add_parser(
        "lookup", help="Search official Skyrim/DLC English->Chinese dictionary XML."
    )
    lookup_parser.add_argument("query", help="English Source text to search for.")
    lookup_parser.add_argument(
        "--dictionary-dir",
        default=str(DEFAULT_DICTIONARY_DIR.relative_to(PROJECT_ROOT)),
        help="Dictionary directory (default: dictionary).",
    )
    lookup_parser.add_argument(
        "--contains", action="store_true", help="Use substring matching instead of exact matching."
    )
    lookup_parser.add_argument(
        "--ignore-case", action="store_true", help="Match using Unicode case folding."
    )
    lookup_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum matches to show; 0 means all (default: 50).",
    )
    lookup_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    lookup_parser.set_defaults(func=lookup_command)

    termrules_parser = subparsers.add_parser(
        "termrules",
        help="Scan a translated XML's Dest against a term-conversion rule file (read-only heuristic).",
    )
    termrules_parser.add_argument(
        "target", help="XML file or directory containing one XML file."
    )
    termrules_parser.add_argument(
        "--rules",
        required=True,
        help="xTranslator term-conversion rule file (StartRule/Search/Replace format).",
    )
    termrules_parser.add_argument(
        "--rec", action="append", help="Only check this exact REC value; repeat to include multiple types."
    )
    termrules_parser.add_argument(
        "--min-len", type=int, default=2,
        help="Ignore rules whose Search is shorter than this (default: 2, filters single chars).",
    )
    termrules_parser.add_argument(
        "--json", action="store_true", help="Emit JSON."
    )
    termrules_parser.set_defaults(func=termrules_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
