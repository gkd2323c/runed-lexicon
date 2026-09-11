---
name: translation-context-builder
description: Build structured, traceable translation context batches for Skyrim mod localization by merging xTranslator XML, xEdit dialogue context JSON, the MOD's machine-readable terms.json, and all official XML dictionaries under dictionary/. Use this before a translation Agent starts working when INFO dialogue needs quest/topic/speaker/context evidence attached. Human-facing documents (CONTEXT.md, DICTIONARY.md) are read by translation agents directly and are never read, parsed, embedded, or hashed by this tool. This skill prepares context only; it does not translate text or write changes back to XML.
compatibility: Requires Python 3.10+ and the runed-lexicon project layout with mods/, dictionary/, and xEdit context JSON. Uses only the Python standard library and does not require network access.
metadata:
  version: "0.3.1"
---

# Translation Context Builder

Build Agent-ready translation context batches without modifying source XML.

## ⚠️ xml_index 基准（重要）

本工具生成 entry 的 `xml_index` 为 **0-based**（从 0 起枚举 `<Content/String>`），与 `translation-executor` / `xtranslator-xml-writer` 一致。但 `skyrim-xml-tools`（inspect / untranslated）输出的 index 为 **1-based**，两套差 1。跨工具对照时先确认基准，否则会误判批次 scope；拿不准用 `Source` 文本核对。`--index-file` 传 0-based 序号。

Use this after xTranslator XML exists and, for dialogue-heavy work, after `xedit-context-exporter` has produced a dialogue context JSON. The builder joins deterministic record context to XML translation strings so the translating Agent can focus on wording instead of rediscovering plugin structure.

## Inputs

The normal input is a MOD directory such as:

```text
mods/evgSIRENROOT.esm
```

The directory's human-facing documents (`CONTEXT.md`, `DICTIONARY.md`) are read by the translation agents directly; this tool never reads, parses, embeds, or hashes them. Machine inputs are structured only: an xTranslator XML, an optional `*_dialogue_context.json`, and an optional `terms.json` (the machine-readable term source — when absent, MOD term hits are simply empty). Early in a project there may be only one xTranslator XML, but after writeback a MOD directory can legitimately contain both the untouched source XML and one or more generated translated XML files.

When multiple XML files are present, do not guess which one is the translation source. Read the MOD's `PROGRESS.md` when present and pass the documented source explicitly with `--xml`. The same rule applies when multiple dialogue-context JSON files exist: use the current file documented by the project state or pass it explicitly.

The builder also reads every XML file recursively under the project root `dictionary/`. Treat that directory as the official dictionary trust boundary; do not hard-code dictionary filenames, DLC names, or subdirectory layouts.

`PROGRESS.md` is not a semantic translation input and is not embedded in the output context. Its role here is operational: it helps select the current source XML / dialogue context instead of accidentally using a generated translated XML or a historical artifact.

## Required command

Run from the project root:

```text
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 20 --output .work/sirenroot/context/sirenroot-info-context.json
```

Use the Python command that passed `.agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate` if it is not `py -3`.

Useful options:

```text
--xml <file.xml>                 Explicit xTranslator XML file
--dialogue-context <file.json>   Explicit xEdit dialogue context JSON
--mod-terms <terms.json>         Explicit MOD terms.json (machine-readable term source)
--dictionary-dir <dictionary/>   Official dictionary directory
--rec INFO:NAM1                  Include only this XML REC; repeatable
--limit 0                        Include all matching entries
--batch-size 25                  Split output entries into Agent-sized batches
--include-translated             Include entries where Source and Dest differ
--output <file.json>             Write JSON output instead of stdout
--force                          Replace an existing output file
```

If the MOD directory contains both an original XML and a generated translated XML, prefer an explicit command such as:

```text
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --xml mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese.xml --rec INFO:NAM1 --output .work/sirenroot/context/sirenroot-info-context.json --force
```

Do not feed the generated translated XML back into a fresh translation pass unless that is intentionally the new source state for a later revision workflow.

## Output shape

The JSON output contains:

- input file paths and counts;
- no human documents are embedded — translation agents read `CONTEXT.md` / `DICTIONARY.md` directly from the MOD directory;
- MOD term hits sourced from the machine-readable `terms.json` (structured; no Markdown parsing). Hit fields: `english` / `chinese` / `status` / `note`;
- batches of traceable XML entries;
- conservative official dictionary term hits found in each source string;
- MOD term hits found in each source string (from terms.json);
- deterministic xEdit dialogue context for joined `INFO` records.

Each entry preserves:

- XML string index;
- `EDID`;
- `REC`;
- exact `Source`;
- exact current `Dest`;
- duplicate count for the same `Source`;
- join status.

For `INFO` entries, join by FormID extracted from XML `EDID` values such as `[04026BC9]`. Do not join INFO by text, XML order, or nearby FormIDs. When no xEdit match exists, keep the entry and mark the join as unresolved.

## Dialogue context rules

Treat these xEdit fields as structural evidence when present:

- parent `DIAL` identity and topic text;
- linked Quest and Branch;
- sibling INFO records under the same DIAL;
- previous INFO links;
- prompts;
- responses;
- conditions;
- explicit speaker and structurally resolved `speaker_candidates`.

If a field is absent from the dialogue JSON, leave it absent or empty. Do not infer speaker, quest stage, branch membership, or dialogue order from semantics.

## Official dictionary policy

Treat `dictionary/` as two related resources, not one giant automatic replacement table:

1. **Canonical terminology source** for identity-bearing names and concepts that should remain continuous with Skyrim / DLC localization.
2. **Reference corpus** for explicit on-demand lookup when the Agent notices a likely TES term that the conservative automatic matcher did not attach.

For prose-like records such as `INFO`, `DIAL`, `BOOK`, and `MESG`, automatic substring hits are intentionally conservative. The practical question is: if this phrase were translated freely, could the player mistake it for something different from the same base-game entity or concept?

Automatic single-token matches are limited to high-value identity / world-term record families such as NPCs, races, factions, locations/worldspaces, potions, and ingredients. Multi-word names from term-like `*:FULL` records may also be attached because accidental overlap is much less likely.

Do not automatically attach generic one-word prose merely because an official record happens to use the same English word. Examples that should stay out of ordinary INFO term attachment include action verbs and generic nouns such as `Talk`, `Place`, `Fill`, `Between`, `Gate`, `Cave`, `Letter`, or `Scholar` when they are just sentence vocabulary.

**Quoted names must still match（引号剥离）**: the game wraps spell and creature names in single quotes ("cast 'Flame Atronach'", "conjure a 'Familiar'") and marks possessives with a trailing apostrophe ("Magnus' notes"). The tokenizer counts `'` as a word character, so before this was handled the tokens came out as `'familiar'`, `'flame`, `atronach'`, `magnus'` and matched nothing: every quoted official name came back as `OFF: -`, which reads as "no official form exists", and translators then invented one. `dequote_stray_apostrophes()` now removes apostrophes that sit outside a word (adjacent to a non-alphanumeric or a string edge) on **both** the index side and the query side, so quoted and possessive forms resolve to the same key. Interior apostrophes survive: `it's`, `Mara's Blessing`, `M'aiq` are unchanged.

This conservative pass is not expected to discover every lore term. Terms such as `Ayleid` or `High Rock` may need an explicit official-corpus lookup when they are not represented by a suitable canonical `FULL` record. Missing an automatic hit is preferable to presenting ordinary prose as an authoritative terminology decision.

## Safety boundaries

- Do not translate anything.
- Do not modify, reserialize, or write back the xTranslator XML.
- Do not update `<Dest>` values.
- Do not read, parse, embed, hash, or mutate `CONTEXT.md` / `DICTIONARY.md` or any other human-facing document. Machine inputs are structured files only (`terms.json`, dialogue-context JSON).
- A documentation edit must never be able to break a build or invalidate a context file.
- Do not treat official dictionary hits as mandatory replacements.
- Do not reinterpret the absence of an automatic official hit as evidence that a phrase has no established TES translation; use explicit dictionary/corpus lookup for suspected lore terms.
- Do not let MOD dictionary hits silently override source text; include them as context evidence for the later translation Agent.
- Do not claim a missing xEdit join is solved by heuristics.

This skill prepares context. Translation, review, and XML writeback are separate later steps.

## Validation

### Performance baseline (v0.2.3)

Per-batch build (Artaeum scale: 4.7MB xTranslator XML + 29.7MB / 81-file dictionary tree) measured **~1.4s** (was ~15.6s before v0.2.3). The fix: `Path.resolve()` hit the filesystem (`nt._getfinalpathname` on Windows, ~0.16ms/call) once per dictionary String row (37k+ calls ≈ 12s); `relative()` is now `lru_cache`-d and the dictionary index builder hoists one resolution per file. Output verified byte-identical before/after on two independent batches. Regression watch: if a per-batch build exceeds ~5s, re-profile with `cProfile` before touching anything else.

### v0.3.0 / v0.3.1 change summary

Human-facing documents no longer enter the machine path: v0.3.0 removed `DICTIONARY.md` (parsing, embedding, hashing); v0.3.1 removed `CONTEXT.md` (reading, embedding, hashing). Inputs are structured-only: xTranslator XML, dialogue-context JSON, and `terms.json`. Consequences: (1) editing either document can never break a build; (2) context provenance tracks `terms.json` and no longer carries `mod_context` (the executor passes a historical `mod_context` through when present, so old contexts keep validating); (3) `terminology.mod_terms_hits` replaces `terminology.mod_dictionary_hits` (the executor accepts both keys when reading historical contexts).

After changing this skill, follow `.agents/skills/skill-creator/SKILL.md`:

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/translation-context-builder
```

Then run:

```text
py -3 -m py_compile .agents/skills/translation-context-builder/scripts/build_translation_context.py
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 3 --output _tmp/data/translation-context-builder-smoke.json --force
```

For the Sirenroot sample, verify mechanically that:

- the output has three entries when `--limit 3` is used;
- `INFO:NAM1` entries with `[xxxxxxxx]` EDIDs include an xEdit dialogue join when the FormID exists in the dialogue JSON;
- official dictionary metadata is present, `inputs` contains no `mod_context` key, and `inputs.mod_terms` reflects `terms.json` (or a graceful empty fallback `{path: null, sha256: "", term_count: 0}` when the MOD has none);
- the source xTranslator XML remains unchanged.
