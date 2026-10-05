#!/usr/bin/env python3
"""Batch official-dictionary probe for runed-lexicon.

Answers, for a list of English terms, what the official dictionaries under
``dictionary/`` actually say -- and does it in one pass.

Why this exists
---------------
``skyrim_xml_tools.py lookup <word>`` is single-word.  Probing twenty terms
therefore meant twenty invocations, and every invocation re-parses every
dictionary XML under ``dictionary/`` (see ``lookup_command``).  Probing also
meant redirecting each invocation's output to its own file, so a routine
per-batch check littered ``_tmp/`` with dozens of throwaway files.

Two habits that mattered more than the speed did:

* ``lookup`` is case-sensitive, so a zero result is frequently a false
  negative.  Reporting "zero official evidence" without re-spelling the term
  is how bogus "official wording" claims get made and later retracted.
* What a translator actually needs is the *set of Chinese renderings* the
  official corpus uses for a term, not the raw hit list.

So this tool loads the dictionaries once, probes every term, automatically
retries plausible spelling variants when a term comes back empty, and prints a
compact per-term report to stdout.  With ``--out`` it also writes that report
to a single file instead of many.

Read-only: it never writes to the dictionaries or to any MOD XML.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skyrim_xml_tools import (  # noqa: E402
    Entry,
    iter_dictionary_xml,
    load_entries,
    relative_display,
)


DEFAULT_DICTIONARY_DIR = Path(__file__).resolve().parents[4] / "dictionary"

# A dictionary hit is evidence of an official *rendering* only when it is a
# name-like entry.  Book bodies (BOOK:DESC and friends) mention terms in prose
# all the time, and those mentions are routinely not about the term at all --
# "Sheza" hits Shezarrine, "Aldmeris" hits the language name, "Carnage" hits
# the ordinary word.  Counting those as renderings is how bogus "official
# wording" claims get made, so long destinations are tallied separately and
# never shown as a translation.
MAX_DEST_LEN = 40


def is_long_mention(dest: str) -> bool:
    flat = " ".join(dest.split())
    return len(flat) > MAX_DEST_LEN


def spelling_variants(term: str) -> list[str]:
    """Cheap re-spellings to rule out the common false negatives.

    Title case is the important one: `lookup` is case-sensitive, and the
    recurring false negative is a term that was probed all-lowercase while the
    dictionary entry is capitalised (`numidium` vs `Numidium`).  All-uppercase
    and the de/duplicated forms cover the other habits that have bitten this
    project (`Rabasha` -> `Basha`, `Ra' J' Dar` -> `Ra J'Dar`).
    """
    out: list[str] = []
    stripped = term.strip()
    if stripped != term:
        out.append(stripped)
    if stripped and stripped[0].islower():
        out.append(stripped[0].upper() + stripped[1:])
    if stripped.lower() != stripped:
        out.append(stripped.lower())
    if stripped.upper() != stripped:
        out.append(stripped.upper())
    if " " in stripped:
        out.append(stripped.replace(" ", ""))
        out.append(stripped.replace(" ", "-"))
    if "-" in stripped:
        out.append(stripped.replace("-", ""))
        out.append(stripped.replace("-", " "))
    # De-duplicate, preserve order, drop the original.
    seen = {term}
    uniq: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def load_all(dictionary_dir: Path) -> list[tuple[Path, Entry]]:
    rows: list[tuple[Path, Entry]] = []
    for xml_path in iter_dictionary_xml(dictionary_dir):
        for entry in load_entries(xml_path):
            rows.append((xml_path, entry))
    return rows


def probe(
    rows: list[tuple[Path, Entry]],
    term: str,
    *,
    contains: bool,
    ignore_case: bool,
) -> tuple[list[dict[str, object]], list[str]]:
    """Return (name-like hits, long-mention sources).

    The second list holds the dictionary files whose *body text* merely
    mentions the term.  They are reported so a zero result can be trusted, but
    they are never presented as a rendering.
    """
    needle = term.casefold() if ignore_case else term
    hits: list[dict[str, object]] = []
    long_sources: list[str] = []
    for xml_path, entry in rows:
        hay = entry.source.casefold() if ignore_case else entry.source
        matched = needle in hay if contains else needle == hay
        if not matched:
            continue
        dest = entry.dest
        if not dest.strip():
            continue
        if is_long_mention(dest):
            name = relative_display(xml_path)
            if name not in long_sources:
                long_sources.append(name)
            continue
        hits.append(
            {
                "dictionary": relative_display(xml_path),
                "index": entry.index,
                "rec": entry.rec,
                "source": entry.source,
                "dest": dest,
            }
        )
    return hits, long_sources


def renderings(hits: list[dict[str, object]], max_forms: int) -> str:
    """The set of Chinese renderings, most frequent first."""
    counts: dict[str, int] = defaultdict(int)
    for hit in hits:
        dest = str(hit["dest"]).replace("\r", " ").replace("\n", " ")
        counts[dest] += 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = [f"{form}×{n}" if n > 1 else form for form, n in ordered[:max_forms]]
    extra = len(ordered) - len(shown)
    if extra > 0:
        shown.append(f"…（另 {extra} 种）")
    return " | ".join(shown)


def report_term(
    rows: list[tuple[Path, Entry]],
    term: str,
    *,
    contains: bool,
    ignore_case: bool,
    max_forms: int,
    retry_variants: bool,
) -> dict[str, object]:
    """Probe one term.

    Two very different kinds of hit are kept apart on purpose:

    * An **exact** hit means the official dictionary has an entry whose Source
      equals the term, so its Dest really is an official rendering.
    * A **contains** hit only means the term occurs somewhere in some Source.
      `Dusk` occurs in "Duskglow Crevice", in Azura's dialogue, in the name of
      a star, and in prose.  Reporting those Dests as renderings of `Dusk` is
      how a term picks up a rendering it never had.

    So in contains mode the hits are labelled context, not renderings.
    """
    hits, long_sources = probe(rows, term, contains=contains, ignore_case=ignore_case)
    label = "context" if contains else "renderings"
    result: dict[str, object] = {
        "term": term,
        "mode": "contains" if contains else "exact",
        "exact": len(hits),
        label: renderings(hits, max_forms),
        "long_mentions": len(long_sources),
        "variants": [],
    }
    if hits or not retry_variants:
        return result
    for variant in spelling_variants(term):
        vhits, vlong = probe(rows, variant, contains=contains, ignore_case=ignore_case)
        if vhits:
            result["variants"].append(
                {
                    "term": variant,
                    "exact": len(vhits),
                    label: renderings(vhits, max_forms),
                }
            )
        elif vlong:
            result["variants"].append(
                {
                    "term": variant,
                    "exact": 0,
                    label: "",
                    "note": f"仅长文本提及 {len(vlong)} 处，不构成译形实证",
                }
            )
    return result


def read_terms(args: argparse.Namespace) -> list[str]:
    """Collect terms from positional args and/or a file, de-duplicated.

    Positional terms come first on purpose: most probes are a handful of words
    typed straight into the command, and making a caller stage them in a
    throwaway file first is pure friction.  A file is still accepted for the
    case that really does warrant one -- a long list you want to keep and
    re-run.
    """
    terms: list[str] = list(args.words)
    if args.terms:
        for line in Path(args.terms).read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    seen: set[str] = set()
    uniq: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            uniq.append(term)
    return uniq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe many terms against the official dictionaries in one pass.",
    )
    parser.add_argument(
        "words",
        nargs="*",
        help="Terms to probe, straight on the command line.",
    )
    parser.add_argument(
        "--terms",
        help="Optional file with one English term per line (# comments allowed).",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=str(DEFAULT_DICTIONARY_DIR),
        help="Dictionary directory (default: dictionary).",
    )
    parser.add_argument(
        "--contains",
        action="store_true",
        help="Substring matching instead of exact (matches `lookup --contains`).",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="Case-fold both sides (matches `lookup --ignore-case`).",
    )
    parser.add_argument(
        "--max-forms",
        type=int,
        default=6,
        help="Max distinct Chinese renderings to list per term (default 6).",
    )
    parser.add_argument(
        "--no-retry-variants",
        dest="retry_variants",
        action="store_false",
        help="Do not re-spell terms that came back empty.",
    )
    parser.add_argument("--out", help="Also write the report to this single file.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    dictionary_dir = Path(args.dictionary_dir)
    if not dictionary_dir.is_absolute():
        dictionary_dir = (Path(__file__).resolve().parents[4] / dictionary_dir).resolve()

    terms = read_terms(args)
    if not terms:
        print("no terms given (pass them as arguments or via --terms)", file=sys.stderr)
        return 2

    rows = load_all(dictionary_dir)
    results = [
        report_term(
            rows,
            term,
            contains=args.contains,
            ignore_case=args.ignore_case,
            max_forms=args.max_forms,
            retry_variants=args.retry_variants,
        )
    for term in terms]

    if args.json:
        payload = {
            "dictionary_dir": str(dictionary_dir),
            "dictionary_entries": len(rows),
            "contains": args.contains,
            "ignore_case": args.ignore_case,
            "terms": results,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0

    lines: list[str] = [
        f"官方词典批量查证（{len(rows)} 条目，"
        f"{'contains 语境命中' if args.contains else 'exact 词条命中'}"
        f"{', ignore-case' if args.ignore_case else ''}）",
        "",
    ]
    if args.contains:
        lines.append(
            "注意：contains 只说明该词出现在某些 Source 里，"
            "下列译形未必是这个词的官方译法（可能是更长名称的子串、"
            "对话正文、星辰或法术名）。要断言官方译形请改用 exact。"
        )
        lines.append("")
    field = "context" if args.contains else "renderings"
    field_label = "语境" if args.contains else "译形"
    zeros: list[str] = []
    for res in results:
        term = str(res["term"])
        count = int(res["exact"])
        long_n = int(res.get("long_mentions") or 0)
        head = f"[{term}] 命中 {count}"
        if count:
            head += f"  {field_label}: {res[field]}"
        else:
            zeros.append(term)
            head += "  → 零命中"
        if long_n:
            head += f"（另有 {long_n} 个词典的长文本提及，不算译形实证）"
        lines.append(head)
        for variant in res["variants"]:  # type: ignore[union-attr]
            vcount = int(variant["exact"])
            if vcount:
                lines.append(
                    f"    变体 {variant['term']}: 命中 {vcount}"
                    f"  {field_label}: {variant[field]}"
                )
            else:
                lines.append(f"    变体 {variant['term']}: {variant.get('note', '零命中')}")
    lines.append("")
    if zeros:
        retried = [
            str(res["term"])
            for res in results
            if not res["exact"] and res["variants"]
        ]
        suffix = f"（其中 {len(retried)} 个经换拼写复验后有命中）" if retried else ""
        lines.append(f"零命中 {len(zeros)} / {len(terms)}: {', '.join(zeros)}{suffix}")
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
