---
name: translation-batch-preparer
description: Prepare structured translation batches from xTranslator XML for Skyrim mod localization without modifying the source XML. Use this skill whenever a Skyrim mod translation needs to be broken into reviewable batches, untranslated entries need stable identifiers and duplicate counts, or official Skyrim/DLC terminology should be attached before an Agent begins translating. Before any actual translation decisions, fully read the target mod's CONTEXT.md and DICTIONARY.md; if either file is missing, stop and ask the user whether to create it.
compatibility: Requires Python 3.10+ and the skyrim-mod-translator project layout with dictionary/ and mods/ directories. Uses only the Python standard library and does not require network access.
metadata:
  version: "0.1.3"
---

# Translation Batch Preparer

Prepare xTranslator strings for an Agent without touching the source XML. The output is a JSON batch with stable source metadata, duplicate counts, and official terminology candidates.

## Before preparing translation work

Locate the target `mods/<plugin>/` directory. When resuming work, read `PROGRESS.md` so the current source XML / result generation is not guessed from historical files.

The bundled script also checks that `CONTEXT.md` and `DICTIONARY.md` exist when the input is a MOD directory, so missing project memory surfaces mechanically.

## Use the bundled script

Run from the project root:

```text
python .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 20
```

Write a batch to a JSON file when the result should be reused:

```text
python .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 20 --output .work/sirenroot-info-001.json
```

If a MOD directory contains both the untouched source XML and one or more generated translated XML files, pass the intended source XML file directly instead of the directory. Use `PROGRESS.md` to identify the current source when available:

```text
python .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese.xml --rec INFO:NAM1 --limit 20
```

Use `--force` only when intentionally replacing an existing batch file.

## What goes into a batch

Each entry keeps enough source information to remain traceable:

- zero-based XML string index
- `EDID`
- `REC`
- original `Source`
- current `Dest`
- number of occurrences of the same `Source` in the MOD XML
- official terminology candidates found inside the source text

Official terminology candidates include their English source, Chinese destination, source dictionary file, dictionary record type, and dictionary EDID when present.

Treat official-term matches as evidence, not automatic replacements. A dictionary hit can be contextually wrong, especially for short or polysemous words.

## Candidate selection

By default, include strings where `Source == Dest` and `Source` is non-empty. This is a mechanical untranslated-candidate rule, not a claim that every included string must be translated.

Use `--rec` to restrict the batch to one or more record types:

```text
python .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --rec DIAL:FULL --limit 50
```

Use `--limit 0` for all matching entries.

When multiple `--rec` filters are supplied together with a positive limit, the script samples them round-robin so each requested record type with available candidates is represented before one type consumes the whole batch. XML order is preserved within each record type.

## Official terminology matching

The script recursively loads every XML file under `dictionary/` and builds an exact normalized phrase index. The directory is the trust boundary: every XML anywhere under that directory is treated as an official dictionary source. Do not assume fixed filenames, a fixed number of files, a fixed subdirectory layout, a fixed DLC set, or filename-specific priority; newly added XML files must participate automatically.

For each MOD source string it checks word n-grams against that index, preferring longer phrases. Automatic hints are limited to term-like records such as names, locations, items, spells, effects, quests, and similar records. Original `INFO` / `DIAL` dialogue snippets are deliberately excluded so ordinary phrases are not mislabeled as terminology.

This first version deliberately favors precision over fuzzy recall:

- It does not perform semantic or fuzzy matching.
- It does not infer MOD-specific names.
- It intentionally omits many ordinary dialogue and UI phrase matches even when they exist in the official XML.
- It does not write terminology into `DICTIONARY.md`.
- It does not decide whether a dictionary candidate is correct for the current context.

Use the existing `skyrim-xml-tools` skill for broader manual dictionary searches when a needed term is not attached automatically.

## Safety boundaries

- Never modify or reserialize the xTranslator XML with this script.
- Never treat the generated JSON as authoritative translation output.
- Preserve `Source`, `EDID`, and `REC` exactly as read.
- Refuse to guess which XML file to use when a directory contains multiple XML files. Use the source documented in `PROGRESS.md` when available and pass that XML file explicitly.
- Fail clearly when `CONTEXT.md` or `DICTIONARY.md` is missing from a MOD directory so the Agent can ask the user whether to create it.
- Do not let an official dictionary match silently override a MOD-local decision in `DICTIONARY.md`.

## Validate changes

Before using a modified version of this Skill, run the `skill-creator` preflight and validator described by `.agents/skills/skill-creator/SKILL.md`.

Then run the script checks from the project root:

```text
python -m py_compile .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py
python .agents/skills/translation-batch-preparer/scripts/prepare_translation_batch.py mods/evgSIRENROOT.esm --rec INFO:NAM1 --limit 3
```

For the current Sirenroot sample, the first `INFO:NAM1` untranslated entry should retain its XML index, EDID, record type, and English source, and should attach established terms such as `Nirnroot` or `skooma` when those exact normalized terms exist in the official dictionaries.

