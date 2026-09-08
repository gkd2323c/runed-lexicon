---
name: xtranslator-xml-writer
description: Safely apply validated Skyrim mod translation-result JSON files or minimal atomic patches to xTranslator XML by changing only intended destination text and verifying source version, XML identity, protected tokens, duplicate units, and post-write structure. Use whenever completed translation batches need to become an xTranslator-importable XML file, or when a small revision (a handful of strings) must be applied to an existing translated XML without rebuilding full batch context. This skill writes a new XML by default and must not freely reserialize the source document.
compatibility: Requires Python 3.10+. Uses only the Python standard library. Expects translation-result JSON produced by translation-executor, or a flat atomic patch JSON.
metadata:
  version: "0.2.1"
---

> 性能基线（见 `skyrim-tool-dev-rules` §2；MVF1 规模 8528 替换 / 9402 节点 / 7 结果文件）：0.43s。回归对照：若同规模耗时超过 5s，先 cProfile 拆账再修复；已知反面模式是循环内反复重建整份文档字符串。

# xTranslator XML Writer

Apply completed structured translation results to xTranslator XML with a deterministic, surgical writeback.

This is the layer after `translation-executor`. It performs no semantic translation and must not invent or revise wording while writing XML.

## Atomic patch mode (small revisions)

When a revision touches only a handful of strings, do not rebuild full batch context or
stage whole result generations. Use `--patch` with a flat JSON file keyed by 0-based
`xml_index`. This replaces the old "create a fake context + copy sibling translations"
workaround:

```json
{
  "1264": {
    "expected_dest": "<current Dest text exactly as in the baseline XML>",
    "translation": "<new Dest text>"
  }
}
```

```text
py -3 .agents/skills/xtranslator-xml-writer/scripts/write_translations.py \
  --xml .work/<mod>-archive/<previous-sha256>/<mod>_english_chinese_translated.xml \
  --patch .work/<mod>-fix-patch.json \
  --output mods/<mod>/<mod>_english_chinese_translated.xml \
  --report .work/<mod>-writeback-report.json \
  --force
```

Before a canonical revision, move the previous canonical artifact into a
content-addressed archive directory such as
`.work/<mod>-archive/<previous-sha256>/`, then use that archived file as the patch
baseline. The writer intentionally rejects `--xml` and `--output` resolving to the
same path. Never manufacture `_final_vN` filenames to preserve history.

Safety model, identical guarantees to result mode:

- `expected_dest` is a mandatory compare-and-swap guard: it must equal the baseline XML's
  current Dest exactly, or the whole patch is rejected (stale baseline protection).
  Optional `source` / `edid` / `rec` keys get the same CAS treatment when present.
- Protected runtime tokens are verified between Source and the new translation.
- Only intended `<Dest>` inner text is spliced; BOM, line endings and everything else
  byte-preserved; post-write structural validation runs as in result mode.
- Patch indices must not overlap entries from any `--result` files in the same run.

Rule of thumb: 1-10 strings -> patch mode; a full batch or a revision with its own review
gate chain -> result mode. Both can be mixed in one run when needed.

## Writeback version governance (hard rule)

A MOD directory may hold exactly TWO translation XMLs at any time: the original source
`*_english_chinese.xml` and the single canonical writeback artifact. The canonical output
filename is exactly `<plugin>_english_chinese_translated.xml` — version labels such as
`_final`, `_v6`, `_round8` or `_backup` in writeback filenames are forbidden (the writer
enforces this mechanically). Version history lives in `PROGRESS.md` (hash chain +
supersede notes), never in filenames. The writer also rejects any output into a directory
that already contains other `*_translated*.xml` files.

Workflow for every new writeback generation:

1. Archive the previous canonical into a content-addressed directory under
   `.work/<mod>-archive/<previous-sha256>/` (move, do not copy). This preserves the
   canonical filename while allowing multiple historical generations without
   `vN` / `roundN` labels.
2. Write the new generation as `<plugin>_english_chinese_translated.xml`
   (overwriting the previous canonical filename with `--force` is allowed and expected:
   same name, new bytes, new hash recorded in PROGRESS.md).
3. Update `PROGRESS.md`: new SHA-256, supersede note, archive location.

Never refer users to "version vX is current" in docs without an explicit supersede note;
the directory state itself must make the canonical version unambiguous: if there is more
than one candidate, the workspace is wrong, not the reader.

## Before writeback

Read `PROGRESS.md` when present so the current canonical result batches are known instead of guessing from old `.work/` files, and make sure the intended batches are complete and already reviewed/validated as appropriate.

`PROGRESS.md` is workflow state, not a semantic authority. `CONTEXT.md` and `DICTIONARY.md` remain the semantic inputs.

## Core safety model

Do not serialize the whole XML tree back through a generic XML writer. Re-serialization can change unrelated whitespace, entity style, BOMs, line endings, or formatting.

The bundled writer instead:

- parses the source XML to verify its structure;
- checks the SHA-256 recorded by every result file against the actual source XML;
- verifies every target by `xml_index`, `EDID`, `REC`, exact parsed `Source`, and exact parsed `original_dest`;
- rejects duplicate translation units or duplicate XML indices across result files;
- accepts only completed `TRANSLATED` and `KEEP` results;
- checks protected runtime tokens before writing;
- locates existing `<String>` blocks in raw source text and replaces only the inner text of intended `<Dest>` elements;
- preserves the source BOM and line-ending convention;
- reparses the generated XML and verifies all `EDID`, `REC`, and `Source` values are unchanged;
- verifies every untouched `<Dest>` remained unchanged and every target `<Dest>` equals the intended result.

`KEEP` entries are checked but not rewritten.

## Usage

Pass explicit result files with repeatable `--result`:

```text
py -3 .agents/skills/xtranslator-xml-writer/scripts/write_translations.py \
  --xml mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese.xml \
  --result .work/batch-0.json \
  --result .work/batch-1.json \
  --output mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese_translated.xml
```

For deterministic families of files, `--result-glob` may be repeated:

```text
py -3 .agents/skills/xtranslator-xml-writer/scripts/write_translations.py \
  --xml mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese.xml \
  --result-glob ".work/sirenroot-info-translation-325-batch-*.json" \
  --result-glob ".work/sirenroot-info-translation-549-batch-1[3-9].json" \
  --result-glob ".work/sirenroot-noninfo-translation-batch-*.json" \
  --output mods/evgSIRENROOT.esm/evgSIRENROOT_english_chinese_translated.xml \
  --report .work/sirenroot-writeback-report.json
```

Use quoted glob patterns so the script, rather than a shell, resolves the intended files consistently.

Use `--force` only when intentionally replacing an already generated output/report file. The script refuses to overwrite the source XML path itself.

Use `--check-only` to run all pre-write validation and result aggregation without producing XML.

**写回前必跑一次 `--check-only`**：它会在不产出 XML 的情况下报告跨批 duplicate xml_index / translation_unit_id、scope 不一致、KEEP 不匹配等问题。新批次若基于“旧写回快照”收集（散件收尾常见），几乎必然与已写回批次重叠；先 check-only 看清冲突清单，再决定去重或调整，而不是直接跑正式写回被 error 弹回。

## Result selection

Do not use a broad glob when `.work/` contains obsolete generations of the same translation batches. Result selection is part of correctness.

Prefer the exact current ranges documented in the MOD's `PROGRESS.md`. The writer rejects duplicate XML indices, which catches many accidental overlaps, but it cannot decide which of two historical translations the user intended.

## Status handling

- `TRANSLATED`: write its `translation` to `<Dest>`.
- `KEEP`: verify `translation == source`; leave the existing `<Dest>` untouched.
- `PENDING`: reject.
- `REVIEW`: reject.

The XML writeback stage should not silently resolve translation uncertainty.

Partial-batch writeback (`--skip-nonfinal`): when a batch legitimately contains a
few units still awaiting review (e.g. long BOOK texts held for a second pass),
`--skip-nonfinal` skips exactly those units instead of rejecting the whole run.
Skipped units are listed in the report (`skipped_units` with file/unit/status +
`skipped_count`) and never written; they must be resolved and written by a later
generation. Without the flag the default stays strict-reject. R14 (2026-09-08):
Druadach BOOK 15 files carried 8 REVIEW units; manual subset extraction produced
430 TRANSLATED + 5 KEEP, and `--skip-nonfinal --check-only` on the original
files reproduces exactly the same counts.

## Protected tokens

The writer independently checks runtime-sensitive tokens such as:

- `<Alias=...>` / `<Global=...>` and other angle-bracket placeholders;
- printf-style placeholders such as `%s` and `%d`;
- common brace/bracket variable forms;
- escaped control tokens such as `\n`.

Ordinary percentages such as `10% permanently` are not printf placeholders.

## Output and report

The generated XML is a new file by default. With `--report`, the script writes JSON containing:

- source and output paths/hashes;
- total XML String count;
- total result count;
- `TRANSLATED` and `KEEP` counts;
- number of `<Dest>` elements actually changed;
- input result file list;
- confirmation that post-write structural checks passed.

The report is evidence of deterministic writeback only. It does not mean the Chinese has been human-approved or game-tested.

## Safety boundaries

- Never alter `<Source>`, `<EDID>`, `<REC>`, `<Params>`, String order, or unrelated `<Dest>` values.
- Never make semantic translation decisions in this skill.
- Never overwrite the source XML path.
- Never bypass a source hash or identity mismatch merely to make writeback proceed.
- Do not require current `CONTEXT.md` / `DICTIONARY.md` hashes to equal historical result hashes. Those hashes are provenance; source XML identity and per-record identity are the writeback guards.
- If post-write validation fails, treat the output as invalid and do not describe it as safe to import.

## Validate this Skill

After modifying this skill, follow `.agents/skills/skill-creator/SKILL.md` and run:

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/xtranslator-xml-writer
py -3 -m py_compile .agents/skills/xtranslator-xml-writer/scripts/write_translations.py
```

For a real writeback, first run `--check-only`, then generate a new XML and inspect the report.

