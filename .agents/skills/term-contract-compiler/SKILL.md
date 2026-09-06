---
name: term-contract-compiler
description: Deterministically compile Markdown terminology tables (MOD DICTIONARY.md, optionally GLOSSARY.md) into the machine-readable `terms` section of a Translation Contract consumed by translation-quality-gate. Use whenever a DICTIONARY.md / GLOSSARY.md decision must become executable term definitions with forbidden variants and risk flags, when building the gate input for a MOD, or when a terminology decision changed and the compiled contract must be regenerated. The compiler never infers unit bindings (those are semantic, produced by the analysis Agent) and never auto-binds ambiguous terms; terms with alias/knowledge-boundary risk default to FORBIDDEN_ONLY.
compatibility: Python 3.10+. Standard library only. Consumes Markdown tables in the project's DICTIONARY.md format (English | 中文 | 状态 | 来源 | 备注).
metadata:
  version: "0.2.1"
---

# Term-Contract Compiler

Compiles the human-readable terminology decision (DICTIONARY.md / GLOSSARY.md) into the `terms` section of a machine contract. The contract is the input to `translation-quality-gate`.

The compiler is deterministic and faithful: it converts rows and extracts declared forbidden variants. It does **not** decide semantics. In particular:

- It never produces `unit_bindings` — those are semantic declarations from the analysis Agent (who decides whether "Blades" is the organization in a given unit).
- It never auto-binds ambiguous or knowledge-boundary terms to REQUIRED. English forms that are ordinary words (Companions, Guard, Jarl, Thane, blades, hist, jel, sweetroll, dragonborn, black book, dwarven, dwemer) or spoiler-sensitive (Falmer, Snow Elf, Maormer, Sea Elf) default to `FORBIDDEN_ONLY` with `risk_flags=["alias"]` unless the confirmation file explicitly sets `enforcement=REQUIRED` + `force_required=true`.
- Ambiguity is a denylist heuristic, not a semantic guarantee. The confirmation file is the authoritative place to force or lift enforcement.

## When to use

- Before gating a batch: compile the MOD's DICTIONARY.md (and GLOSSARY.md if cross-MOD terms are needed).
- **New MODs and any MOD that needs machine-clean term definitions: prefer the structured `--terms <MOD>/terms.json` source.** The JSON path is the canonical machine form — every field (`english` / `zh` / `status` / `enforcement` / `risk_flags` / `forbidden`) is explicit, so the compiler does no column guessing and cannot silently mis-parse a term (e.g. `Neeth & His Past`, `Mercenaries Needed!`, or a Chinese first column). The Markdown path is kept only for backward compatibility with legacy MODs (MVF1 / evgSIRENROOT) and for human-readable documentation.
- After a terminology decision changes: update the JSON (or Markdown), then recompile so the gate enforces the new decision.
- The compiled contract is a derived artifact; **the JSON (or Markdown) stays the source of truth**. Never edit the compiled JSON by hand as the canonical version (only ephemeral overrides via the confirmation file).

## Usage

### Structured JSON source (preferred for MODs)

```text
python .agents/skills/term-contract-compiler/scripts/compile_contract.py \
  --terms mods/<plugin>/terms.json \
  --id-prefix <plugin>. \
  --global-bans global-forbidden-words.json \
  --output .work/term-contracts/<plugin>.compiled.json \
  --keep-output .work/term-contracts/<plugin>.keep.json
```

`--global-bans <root>/global-forbidden-words.json` 把项目级全局禁用词库嵌入
编译产物：编译出的 contract 会带 `global_bans`（以及有全局 KEEP 时的 `global_keep`）
字段，translation-quality-gate 据此对每个已翻译单元独立执行 TERM004 / KEEP002。
建议在每次编译 MOD 契约时都传入全局词库，让新 MOD 零成本继承全部历史坏形态。

`terms.json` schema (canonical MOD term source):

```json
{
  "id_prefix": "sb1.",
  "terms": [
    { "english": "Neethmarin", "zh": "尼思马林", "status": "PROVISIONAL", "note": "...", "enforcement": "REQUIRED", "risk_flags": ["alias"], "forbidden": ["..."] }
  ]
}
```

Rules:
- `enforcement` / `risk_flags` declared in JSON are authoritative and are **not** overridden by the ambiguous-word heuristic (unlike Markdown, where `Companions` / `Dragonborn` etc. are auto-downgraded to `FORBIDDEN_ONLY` unless a conf file forces them). This lets a term like `the Guild` (REQUIRED + alias-risk in this MOD's context) stay REQUIRED when it is genuinely unambiguous in context.
- Missing optional fields fall back to documented defaults: status → PROVISIONAL, enforcement → REQUIRED (FORBIDDEN_ONLY for REVIEW), risk_flags → none, forbidden → [], additional_accepted → [].
- `status: KEEP` or `enforcement: KEEP` routes the term to the keep list (not emitted as a term).
- The validator rejects (exit 1) missing `english` / `zh`, an `english` that is a bare status word, and unknown `status` / `enforcement` values — it never silently emits a garbage term.

### Markdown source (legacy / backward compatible)

```text
python .agents/skills/term-contract-compiler/scripts/compile_contract.py \
  --dictionary mods/MVF1FollowerBeta.esp/DICTIONARY.md \
  --id-prefix mvf1. \
  --output .work/term-contracts/MVF1FollowerBeta.esp.compiled.json \
  --keep-output .work/term-contracts/MVF1FollowerBeta.esp.keep.json
```

Markdown tables must keep the first cell of every term row as the pure-English source (no `&`, `!`, or Chinese in that cell) and the second cell as the Chinese translation; the compiler identifies the English / Chinese columns heuristically, so rows that violate this can be silently mis-parsed. Prefer the structured JSON source for new content.

Optional `--conf <file>.json` supplies per-term overrides:

```json
{
  "id_prefix": "mvf1.",
  "overrides": {
    "Argonian": { "enforcement": "REQUIRED", "forbidden": ["亚龙人", "阿戈尼安"], "match_kind": "forms" },
    "Companions": { "enforcement": "FORBIDDEN_ONLY", "risk_flags": ["alias"] }
  }
}
```



## What it extracts

- **Terms** from Markdown table rows (English + 中文 columns), with `decision_status` from the 状态 column.
- **Forbidden variants** from 备注 clauses containing 严禁/不得/禁止/不要/不许 (e.g. `严禁使用"亚龙人/阿戈尼安"`), cleaned of parentheses/quotes/connective noise. `错认成/误认成/混淆` clauses are skipped (they name an entity to avoid confusing, not a wrong translation).
  - Parsing guards (v0.2.0): only quoted spans and free text **after** the first ban keyword in a segment are treated as bans (descriptive quotes before it, e.g. `统一为"护甲"，严禁"盔甲"`, are usage notes and never become forbidden); unquoted free text stops at the first comma (commas start a new descriptive clause; variant lists use `/` `、` `或`); `或` is treated as a variant separator (`白漫城或斯凯尔姆` → both); any part that overlaps the target (substring in either direction) is dropped, so a forbidden variant can never make the gate's TERM002 fire on a correct translation.
- **Additional accepted variants** from 备注 clauses containing `additional_accepted: X, Y, Z` — these are appended to the term's `match.accepted` list, allowing natural shorthand (e.g., "白望塔" for "Whitewatch Tower") to satisfy the gate's TERM001 check. This prevents false positives when translators use abbreviated but correct forms.
  - The JSON path also accepts `additional_accepted` as an explicit array field on the term object.
  - This is the canonical way to encode natural shorthand into DICTIONARY.md (v0.2.1): the compiler reads it from the note and appends to `match.accepted`, so recompiling replaces hand-edits to the compiled contract. This eliminates the R6 anti-pattern of manually editing compiled.json.
- **Keep list** from rows with 状态 KEEP (written to `--keep-output`).

## Enforcement defaults

| decision_status | default enforcement | note |
| --- | --- | --- |
| CONFIRMED / PROVISIONAL (unambiguous) | REQUIRED | match contains_phrase |
| CONFIRMED / PROVISIONAL (ambiguous/risk) | FORBIDDEN_ONLY | risk_flags alias; explicit binding required |
| REVIEW | FORBIDDEN_ONLY | |
| KEEP | (keep list) | not emitted as term |

## Safety boundaries

- Never add `unit_bindings` automatically. The gate only enforces explicit bindings.
- Never emit REQUIRED for an ambiguous English form without explicit confirmation (the structured JSON path honors an explicit `enforcement` declaration instead).
- Never edit DICTIONARY.md / terms.json / global-forbidden-words.json from this tool; it is read-only over its inputs.
- Recompile after source changes; do not hand-edit the compiled JSON as canonical.

## Global ban list (project-wide TERM004)

The compiler embeds the root `global-forbidden-words.json` into every contract
when `--global-bans` is passed. Each entry: `english` (source-side anchor),
`forbidden` (wrong Chinese forms), `target` (canonical form), `reason` (why it
is banned — surfaces in the gate report so an Agent can judge a hit). Canonical
source: `global-forbidden-words.json` (project root). It is a curated list of
cross-MOD official-name error forms, NOT a full term dictionary — see
GLOSSARY.md §7 for收录边界. The compiler only validates/normalizes it; the
curation happens by editing the JSON (each term carries its reason).
