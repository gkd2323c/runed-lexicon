---
name: proper-noun-index
description: >-
  Build a stable derived index of official Skyrim proper nouns (people, places,
  races, factions) from the whole dictionary tree, then scan a MOD's source XML
  or translation-result JSON to list every official proper noun that appears in
  it — with official Chinese candidates, cross-file support (which dictionary
  files use that dest), occurrence counts and alias-risk hints — for
  pre-translation adjudication.
  Use this skill before starting a new MOD translation or before adjudicating
  official-name candidates (the discovery step), when you need the full set of
  official person/place names a MOD will touch, or when asking "which official
  names does this MOD contain". Complements dictionary-noun-audit: audit checks
  translated Dest rows for missing official names; this scan inventories the
  Source side before/while translating. Read-only, deterministic, reuses
  dictionary-noun-audit matching so it can never drift from audit behavior.
compatibility: Python 3.10+. Standard library only. Imports matching rules from
  dictionary-noun-audit and ambiguity hints from term-contract-compiler, so those
  skills must stay in .agents/skills/. Requires the skyrim-mod-translator layout.
metadata:
  version: "0.2.0"
---

# Proper-Noun Index

Two deterministic, read-only commands over the official dictionary tree:

- `build` — scan `dictionary/**/*.xml` once and emit a stable index JSON. For
  every English source that has official Chinese evidence, the index records
  the official Chinese candidates with cross-file support (which files use
  that dest), the record families that prove the name, and its word count —
  plus a `classify` view
  (`enabled` / `optional`) identical to dictionary-noun-audit's gating, so the
  name pool can never drift from what the audit would check.
- `scan` — given a MOD xTranslator XML or translation-result JSON, list the
  official proper nouns that actually occur in it, with occurrence counts,
  official evidence and tier. This is the discovery step for turning "the
  people/place names have a stable organization in the official dictionary"
  into an explicit inventory the Agent adjudicates into the MOD's
  `DICTIONARY.md` / `terms.json`.

Reuse contract: tokenization, stop/verb heads, strong-record families,
normalization and trailing-punctuation merging are imported from
`dictionary-noun-audit`; ambiguity hints reuse the `term-contract-compiler`
denylist. This tool adds no separate matching rules of its own.

## When to use

- Before starting a new MOD translation: after reading CONTEXT.md/DICTIONARY.md,
  run `scan` on the source XML to see the full set of official names the MOD
  will touch, then adjudicate each into DICTIONARY.md / terms.json.
- Before a terminology pass / convergence claim: confirm which official names
  are present and whether their official forms carry context variants
  (e.g. Whiterun -> 白漫城 / 白漫领) or alias risk.
- Whenever the question is "which official proper nouns does this MOD contain".

Do **not** use this to check a finished translation's Dest for missing names —
that is `dictionary-noun-audit`'s job on the translated output.

## Usage

```text
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py build
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py build --dict <dir> --output <path>
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py scan .work/proper-noun-index/index.json --target mods/<plugin>/<file>_english_chinese.xml
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py scan <index.json> --target <mod.xml> --include-optional --json <out.json>
```

Defaults: `build` writes `.work/proper-noun-index/index.json`; `scan` accepts a
translated XML or a translation-result JSON (executor schema). The index is a
derived artifact — rebuild it whenever `dictionary/` changes; never hand-edit.

Tiers reported by `scan`:

- `ENTITY` — record-proven (NPC_/WRLD/LCTN/CELL/RACE/FACT/…) **multi-word**
  people / places / races / factions. Near-unambiguous proper nouns.
- `SEMANTIC` — weekday / calendar-month system terms (Morndas, Heartfire, …),
  forced HIGH by the audit layer regardless of their weak GMST records.
- `ENTITY_LOW` — record-proven but **single-word** (Skyrim, Delphine, Nord,
  Dragon). The audit treats single words LOW because prose may legitimately
  elide or extend them (天际省 / 诺德人); adjudicate before upgrading to a term.
- `MULTIWORD` — other enabled official names (item / quest / book titles).
- `OPTIONAL` — weak / ambiguous single-word names; only listed with
  `--include-optional`.

Each inventory row carries the official Chinese candidate list with the number
of supporting dictionary files (every file under `dictionary/` counts equally;
no filename is special-cased, so adding a dictionary XML later needs no code
change). Dest candidates that are residual
full sentences (trailing 。！？…) are filtered from the display/JSON `zh` list —
they are not usable match targets for the gate; the raw index still holds them.

## Output and adjudication

Terminal output groups the inventory by tier, sorted by occurrence. `--json`
writes the same inventory to a file for structured consumption.

Adjudication is the Agent's job (this tool never decides):

1. For each ENTITY / ENTITY_LOW name, pick the official Chinese form that fits
   the MOD context. When the official candidates differ by semantic variant
   (Whiterun -> 白漫城 for the city, 白漫领 for the hold; the Rift -> 裂痕 /
   裂痕领), decide from the sentence context, not by always taking the first.
2. Disputed single-word names present in several dictionary files with
   different dests (e.g. Nikulas -> 尼古拉斯 vs 尼库拉斯) need a MOD-level
   ruling into DICTIONARY.md.
3. Alias-risk entries (`ambiguous_hint`) — Blades, Companions, Jarl, Thane,
   Dragonborn, the Guild — stay FORBIDDEN_ONLY or get explicit per-unit
   bindings; never auto-REQUIRED.
4. Confirmed rulings land in the MOD `DICTIONARY.md` / `terms.json`, then the
   normal contract workflow continues (compile -> bind -> gate -> writeback).

## Relationship to the other skills

- `dictionary-noun-audit`: audit is the Dest-side checker (does the translation
  contain the official form?). This scan is the Source-side inventory (which
  official names does the MOD contain?). Same matching logic, same dictionary.
- `term-contract-compiler`: consumes DICTIONARY.md / terms.json. This tool's
  inventory is a candidate list that feeds that adjudication; it does not emit
  terms and never writes DICTIONARY.md / terms.json.
- `translation-quality-gate`: enforced after terms exist. This tool adds no
  enforcement.

## Article-case handling (the / The are the same word)

A leading article is not a semantic distinction: `the Rift` and `The Rift` are
the same hold. The index canonicalizes a leading lowercase `the ` to `The `
when folding dictionary rows, so each place has one entry (`The Rift` carries
both 裂痕 and 裂痕领). `scan` aligns a sentence-leading lowercase `the` before
matching, so prose `go to the Rift` finds the same entry as title-case
The Rift. The source shown in samples stays untouched.

## Known boundaries (do not silently "fix" these)

- **Single-word strong-record names are ENTITY_LOW, never auto-CONFIRMED.**
  Official dest may be extended/elided in prose; the Agent decides.
- The index is a derived artifact of `dictionary/`; it is evidence, not a
  semantic decision. No entry becomes a contract term without adjudication.

## Safety boundaries

- Read-only: never edits the MOD XML, translation JSON, DICTIONARY.md or
  terms.json. `build` writes only the index JSON (default under `.work/`).
- Never auto-writes terms; never upgrades a candidate to CONFIRMED/REVIEW.
- Does not decide semantic variants; context ruling stays with the Agent.

## Verification

Smoke checks after any change to the script:

```text
py -3 -m py_compile .agents/skills/proper-noun-index/scripts/proper_noun_index.py
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py build
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py scan .work/proper-noun-index/index.json --target mods/SB1NeethmarinFollower.esp/SB1NeethmarinFollower_english_chinese.xml --json .work/proper-noun-index/sb1-smoke.json
```

Known baseline (79 dictionary files, 2026-09-06):
- `build`: ~3.0s; index has 66946 English sources; `classify` 19940 enabled /
  11640 optional names. Schema 1.1 adds a `classify.semantic` list of the 19
  weekday/month family terms (also folded into `enabled`, kept out of
  `optional`).
- `scan` SB1 (3632 rows): ~2s, 114 distinct names (29 ENTITY / 28 MULTIWORD /
  57 ENTITY_LOW) before the semantic layer; with it, names present in the MOD
  that are family terms surface as `SEMANTIC`.
- Performance gate: well under the 30s defect threshold; if a larger corpus
  pushes a run past 5s, profile first (cProfile) before optimizing.
