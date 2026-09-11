---
name: dictionary-noun-audit
description: >-
  Scan a translated Skyrim mod XML (or translation-result JSON) against the official English-Chinese dictionary corpus, reporting CHECK candidates where the Source contains an official proper noun while the Dest lacks that noun's official Chinese translation. Use this skill whenever a translated mod must be checked for missing or divergent official names (e.g. Source has Skyrim but Dest has no 天际), when opening or finishing a new mod translation, or after any terminology pass — before declaring official-name convergence. Single-word names proven by official record structure (WRLD/NPC_/RACE/LCTN/...) are audited by default, so Skyrim to 天际 is always checked; ambiguous single words from book/misc records are reported as low-confidence only. The script is a candidate finder, not a judge — it reports string facts with dictionary evidence and the Agent decides whether each candidate is a real error, legitimate elision, or a context-specific official form.
compatibility: Python 3.10+. Standard library only. Requires the runed-lexicon layout with dictionary/ and mods/. Read-only — never edits XML or JSON.
metadata:
  version: "0.4.0"
---

# Dictionary Noun Audit

Scan a translated xTranslator XML (or translation-result JSON) and report every row whose Source contains an official dictionary noun while its Dest does **not** contain that noun's official Chinese form.

The tool is a **candidate finder, not a judge**:
- It reports string facts only. Whether a candidate is a real error, a legitimate anaphora elision, or a context-specific official form is the Agent's decision.
- It never edits, never auto-fixes, never upgrades a candidate to REVIEW.

## When to use

- Before declaring a translation batch "official-name converged".
- When opening a new MOD translation, to see which official names are missing from early drafts.
- After any terminology pass, to confirm the pass covered every occurrence — not only the ones it noticed.
- Whenever the question is "Source 有这个官方名词，但译文里没有对应译名".

## Which dictionary nouns are audited (default vs optional)

Evidence comes from the official record structure, not a hardcoded whitelist:

- **Multi-word capitalized names** (2–6 words, e.g. `Sky Haven Temple`, `Amulet of Mara`, `Golden Claw`) — audited by default, HIGH confidence.
- **Single-word names proven by strong records** — audited by default. A word whose standalone official rows live in `WRLD`/`LCTN`/`CELL`/`NPC_`/`RACE`/`FACT`/… is an entity name, so `Skyrim` (WRLD -> 天际), `Delphine` (NPC_ -> 戴尔芬), `Whiterun` (LCTN -> 白漫城) are always checked.
- **Single-word names from ambiguous records** (`BOOK`/`MISC`/`WOOP`/… like `Letter`, `Note`, `Time`) — reported only with `--include-single` or when named via `--entity`. These exist as real book/item titles in official data yet are ordinary words in dialogue; the ambiguity is left to the Agent.
- **Semantic family terms** (weekdays + calendar months) — always audited, HIGH confidence, **regardless of record structure**. Their official rows live in weak `GMST:DATA` records (e.g. `Morndas` -> 周一), which the structural rules would otherwise leave optional — but a model that transliterates a weekday (`Morndas` -> 莫恩达斯) instead of recognizing it must still be caught. Each family member is asserted against the dictionary at startup: a member with no official Chinese evidence is a hard error, never a silent skip.

Confidence per flag:
- multi-word → HIGH (near-unambiguous proper noun)
- single-word → LOW (proven names like Skyrim/Delphine are still audited by
  default, but LOW means "look at it": prose may legitimately extend or elide
  the official form, e.g. 天际省 / 诺德人). `--entity` forces HIGH for a term.

Task-prompt verb phrases (`Speak to X`, `Talk to X`, `Search the…`), sentence fragments (`Let's go`) and dialogue heads (`We know`, `Not yet`) are demoted to optional (reported only with `--include-single` or `--entity`) — they are quest objectives / dialogue, not names. Article-headed titles (`The Bee and Barb`, `The Pale Lady`) stay enabled.

## Usage

```text
py -3 .agents/skills/dictionary-noun-audit/scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml
```

Audit specific entities too (regex; forces them HIGH even if single-word/weak-record):

```text
py -3 .agents/skills/dictionary-noun-audit/scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml --entity "Skyrim|Delphine|Amulet of Mara"
```

Include ambiguous single-word names (noisier):

```text
py -3 .agents/skills/dictionary-noun-audit/scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml --include-single
```

Also accepts a translation-result JSON (executor schema); write flags to a review file:

```text
py -3 .agents/skills/dictionary-noun-audit/scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml --json .work/<plugin>/reports/<plugin>-noun-audit.json
```

Options:
- `--entity REGEX` — audit matching official names regardless of default gating.
- `--include-single` — also audit ambiguous single-word nouns (high noise).
- `--semantic-family weekday|month|all` — restrict the semantic-family layer (default `all`, always on).
- `--no-semantic-families` — disable the semantic layer entirely (weekdays/months unchecked). Diagnostic only; never use in normal gate runs.
- `--limit N` — cap printed rows (0 = all).
- `--json PATH` — write all flags to JSON.
- `--dict DIR` — override dictionary directory (default `<project>/dictionary`).
- `--fail-on-flags` — exit 1 when flags exist (CI gating).

Exit codes:
- `0` — scan succeeded, regardless of how many candidates were found (finding candidates is a successful run).
- `1` — only with `--fail-on-flags` and candidates present.
- `2` — real failure: missing dictionary evidence, unreadable/parsable target.

## Output and review

Each flag carries full provenance so the Agent can decide without another dictionary search:

```text
[xml_index][REC][EDID][HIGH/LOW] EnglishName -> 官方: 译名1/译名2
  SRC: ...
  DST: ...
  ev: <dictionary_file> [<REC> <EDID>] -> <official dest>
```

JSON flags include: `xml_index`, `edid`, `rec`, `source`, `dest`, `matched_term`, `official_translation`, `confidence`, `official_evidence` (file/rec/edid/dest).

Review checklist per flag:
1. Is the official name truly required here (proper-noun usage, not anaphora)?
2. Legitimate elision (name already stated earlier in the conversation) → no fix.
3. Context-specific official form — e.g. `Cure Disease` as a spell is 疾病治愈 but `Potion of Cure Disease` is 治病药水; `Shadow Stone` is 暗影之石 not "影子". Check the `official_evidence` rows and the actual dictionary entries before "fixing".
4. If it is a real wrong/variant translation, fix it in the translation-result JSON (never the XML), then rewrite via `xtranslator-xml-writer`, then re-audit to confirm the flag is gone.
5. Only after checking context and official evidence with no resolution does a candidate become a human REVIEW item.

## Matching behavior

- The dictionary tree is scanned recursively (`dictionary/**/*.xml`): every XML under the tree is official evidence. Files that fail to parse are reported to stderr — evidence is never silently partial.
- Each `<String>` is parsed structurally (EDID/REC/Source/Dest via ElementTree), never by regex on field adjacency.
- Name matching is deterministic and conservative:
  - case-sensitive start (proper nouns are capitalized in the Source)
  - internal apostrophes and hyphens kept: M'aiq, J'zargo, Nix-Hound, Swims-In-Deep-Water
  - cultural particles allowed lowercase inside a name (Lash gra-Shugurz)
  - possessive stripped: Whiterun's / Whiterun’s match Whiterun
  - plural tolerated: Argonians / Thalmors match Argonian / Thalmor
  - no NLP, no stemming
- Empty Dest and untranslated Dest (Dest == Source) are CHECK candidates — only an empty Source is skipped. For translation-result JSON, a `translation` field that is present but empty is treated as an empty translation (candidate), never silently replaced by `original_dest`; rows explicitly marked `status: KEEP` are skipped.
- English-only dictionary dests (internal technical records) are ignored as evidence.
- Output JSON keeps full Source/Dest; terminal display truncates to 160 chars for readability.

## Known blind spots

Three boundaries are inherent to the evidence model. They are recorded here so a
zero-flag run is never read as "everything is checked".

1. **Composite-only official names.** A proper noun that the official dictionary
   only ever spells inside a longer phrase is not audited in its bare form.
   `Dwemer` has 245 official rows (`Dwemer Actuator` -> 锻莫制动器, `Dwemer Vault
   Door`, …) but no standalone row, so `Dwemer` never enters the enabled map and
   a row like `Frosty Dwemer Abilities` is not checked; `Morrowind` (246 rows,
   all prose) and `Aedra` (17 rows, all prose) are in the same position. A
   token-gloss inference layer was prototyped and rejected: voting over composite
   rows derives `Dwemer` -> 锻莫 correctly (96% / 67 rows) but also `War` -> 战斧
   and `Raven` -> 鸦石镇, producing ~350 mostly-wrong candidates. The fix belongs
   in the project contract: register the name as a REQUIRED term so
   `translation-quality-gate` enforces it.
2. **Single-word names from weak records.** `BOOK`/`MISC`/`WOOP`-only single
   words stay optional unless `--include-single` or `--entity` is given (737 vs
   3755 candidates on Druadach). The switch is a noise trade, not a capability
   gap: the candidates exist, they are simply not printed by default.
3. **Dest-side errors without a Source-side name.** The audit only fires when the
   Source contains the official name. A wrong name that appears only in the Dest
   (a hallucinated place, an invented faction) is out of scope; use
   `fantasy-context-auditor` or review for that class.

## Relationship to the other skills

- `term-contract-compiler` turns DICTIONARY.md into a machine contract; this audit is the discovery step that finds which official nouns actually appear and are missing before binding.
- `translation-quality-gate` enforces declared bindings mechanically before writeback; this audit complements it by finding undeclared misses the gate (by design) will not check.
- Recommended flow: audit draft → fix confirmed misses in DICTIONARY.md / translation JSON → compile contract → bind → gate → write back → re-audit to confirm 0 flags.

## Safety boundaries

- Read-only: never edits the XML or JSON it scans.
- Never auto-fixes a flag; never upgrades a candidate to REVIEW. The Agent decides each case.
- The dictionary is the only evidence source; no hardcoded entity whitelist.
