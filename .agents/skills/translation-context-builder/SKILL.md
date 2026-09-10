---
name: translation-context-builder
description: Build structured, traceable translation context batches for Skyrim mod localization by merging xTranslator XML, xEdit dialogue context JSON, the mod's CONTEXT.md and DICTIONARY.md, and all official XML dictionaries under dictionary/. Use this before a translation Agent starts working when INFO dialogue needs quest/topic/speaker/context evidence attached. This skill prepares context only; it does not translate text or write changes back to XML.
compatibility: Requires Python 3.10+ and the runed-lexicon project layout with mods/, dictionary/, and xEdit context JSON. Uses only the Python standard library and does not require network access.
metadata:
  version: "0.2.2"
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

That directory must contain `CONTEXT.md`, `DICTIONARY.md`, and preferably one `*_dialogue_context.json` file. Early in a project there may be only one xTranslator XML, but after writeback a MOD directory can legitimately contain both the untouched source XML and one or more generated translated XML files.

When multiple XML files are present, do not guess which one is the translation source. Read the MOD's `PROGRESS.md` when present and pass the documented source explicitly with `--xml`. The same rule applies when multiple dialogue-context JSON files exist: use the current file documented by the project state or pass it explicitly.

The builder also reads every XML file recursively under the project root `dictionary/`. Treat that directory as the official dictionary trust boundary; do not hard-code dictionary filenames, DLC names, or subdirectory layouts.

`PROGRESS.md` is not a semantic translation input and is not embedded in the output context. Its role here is operational: it helps select the current source XML / dialogue context instead of accidentally using a generated translated XML or a historical artifact.

## Required command

Run from the project root:

```text
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 20 --output .work/sirenroot-info-context.json
```

Use the Python command that passed `.agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate` if it is not `py -3`.

Useful options:

```text
--xml <file.xml>                 Explicit xTranslator XML file
--dialogue-context <file.json>   Explicit xEdit dialogue context JSON
--context <CONTEXT.md>           Explicit MOD context Markdown
--mod-dictionary <DICTIONARY.md> Explicit MOD dictionary Markdown
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
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --xml mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese.xml --rec INFO:NAM1 --output .work/sirenroot-info-context.json --force
```

Do not feed the generated translated XML back into a fresh translation pass unless that is intentionally the new source state for a later revision workflow.

## Output shape

The JSON output contains:

- input file paths and counts;
- full `CONTEXT.md` and `DICTIONARY.md` content for the translation Agent;
- parsed MOD dictionary term rows when Markdown tables can be read. Column matching accepts the header forms 原文/英文/English/Source (source column) and 中文/译名/译法/Chinese/Dest (target column); a trailing parenthesized note such as `原文 (English)` / `译名 (Chinese)` is stripped before matching, so both historical header styles resolve to the same parsed row;
- batches of traceable XML entries;
- conservative official dictionary term hits found in each source string;
- MOD dictionary hits found in each source string;
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

Automatic single-token hints are limited to high-value identity / world-term record families such as NPCs, races, factions, locations/worldspaces, potions, and ingredients. Multi-word names from term-like `*:FULL` records may also be attached because accidental overlap is much less likely.

Do not automatically attach generic one-word prose merely because an official record happens to use the same English word. Examples that should stay out of ordinary INFO hints include action verbs and generic nouns such as `Talk`, `Place`, `Fill`, `Between`, `Gate`, `Cave`, `Letter`, or `Scholar` when they are just sentence vocabulary.

This conservative pass is not expected to discover every lore term. Terms such as `Ayleid` or `High Rock` may need an explicit official-corpus lookup when they are not represented by a suitable canonical `FULL` record. Missing an automatic hit is preferable to presenting ordinary prose as an authoritative terminology decision.

## Safety boundaries

- Do not translate anything.
- Do not modify, reserialize, or write back the xTranslator XML.
- Do not update `<Dest>` values.
- Do not mutate `CONTEXT.md` or `DICTIONARY.md`.
- Do not treat official dictionary hits as mandatory replacements.
- Do not reinterpret the absence of an automatic official hit as evidence that a phrase has no established TES translation; use explicit dictionary/corpus lookup for suspected lore terms.
- Do not let MOD dictionary hits silently override source text; include them as context evidence for the later translation Agent.
- Do not claim a missing xEdit join is solved by heuristics.

This skill prepares context. Translation, review, and XML writeback are separate later steps.

## Validation

After changing this skill, follow `.agents/skills/skill-creator/SKILL.md`:

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/translation-context-builder
```

Then run:

```text
py -3 -m py_compile .agents/skills/translation-context-builder/scripts/build_translation_context.py
py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 3 --output .work/translation-context-builder-smoke.json --force
```

For the Sirenroot sample, verify mechanically that:

- the output has three entries when `--limit 3` is used;
- `INFO:NAM1` entries with `[xxxxxxxx]` EDIDs include an xEdit dialogue join when the FormID exists in the dialogue JSON;
- `CONTEXT.md`, `DICTIONARY.md`, and official dictionary metadata are present;
- the source xTranslator XML remains unchanged.
