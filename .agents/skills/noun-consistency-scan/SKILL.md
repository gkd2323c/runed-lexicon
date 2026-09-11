---
name: noun-consistency-scan
description: >-
  Scan a translated Skyrim mod XML for noun-consistency problems — same English Source
  (item/place/quest/faction display names, not dialogue) translated into multiple different
  Chinese Dest forms, plus optional extension pools (construction-suffix divergence,
  official-name HIGH candidates needing recheck, unregistered official names, untranslated
  rows needing human review). Read-only, deterministic, emits JSON shards sized for subagent
  review. Use whenever a translated MOD needs a noun consistency pass (same Source with
  multiple Dest variants), when opening a new MOD or finishing a terminology batch, after a
  writeback that unified names, or before declaring noun-level convergence. The core A pool
  needs only the translated XML; C/D pools additionally consume dictionary-noun-audit and
  proper-noun-index JSON outputs.
compatibility: >-
  Python 3.10+. Standard library only. Optional inputs: dictionary-noun-audit --json
  output (C pool), proper-noun-index scan --json output (D pool), MOD terms.json
  (C/D pool term matching).
metadata:
  version: "1.0.0"
---

# Noun Consistency Scan（名词翻译一致性扫描）

Read-only scanner for noun-level translation consistency in a translated xTranslator XML.
It finds the classic defect pattern: the same English display name translated into several
different Chinese forms across rows (e.g. 70× "Remarkable Gravestone" split 68/2 between
非凡墓碑 and 特别的墓碑). Deterministic; same input → same output.

## When to use

- Before declaring noun-level convergence for a MOD (same-Source-same-Dest is the contract).
- After a writeback that unified names — rerun to prove remaining splits are all adjudicated.
- On a new MOD after batches are translated, to surface splits early.
- Before a subagent fan-out review: the JSON shards are sized for per-subagent judgment
  (default 170 rows/piece, matching the review caps in hana-subagent-ops).

The scanner is a candidate finder, not a judge: a split is either a real defect
(same thing, two names), a legitimate register/layer difference (official dictionary
itself uses 守卫 for factions and 卫兵 for city guards), or a same-source-different-object
case. The Agent decides, using official dictionary evidence (lookup) and the MOD's
DICTIONARY.md — never auto-fix from the scan output alone.

## Rule scans are closed sets; heuristic search is the open-set default

Rule-based scans (this scanner's pools, term-contract gate, dictionary-noun-audit) can only
find what is already registered or already patterned: registered terms, display-name rows,
bound units. They are closed sets by construction. Zero hits from rules never means
converged — it means "nothing registered drifted", and unregistered word families, spoken
name variants in dialogue rows, and same-root derivations are invisible to them.

Noun-convergence review therefore defaults to an open-set heuristic pass, not to rule scans
alone:

1. **Seed discovery**: an Agent reads a contiguous region of the translated XML and lists
   proper-noun candidates (people/places/factions/organizations) from the English Source.
   No whitelist, no "correct form" knowledge — only pattern recognition of what looks like a
   name.
2. **Full-text diffusion**: for each seed, search the whole canonical for every occurrence
   of that English anchor (case-insensitive), and group the Chinese forms found at those
   rows.
3. **Candidate reporting**: report every anchor whose occurrences show ≥2 distinct Chinese
   forms, with the full row list per form. The reporter does not judge which form is
   correct and does not classify splits.
4. **Adjudication by the main Agent**: each reported candidate is decided on the current
   XML — EDID ancestry, register/layer semantics, official dictionary evidence, DICTIONARY.md.
   A split is a real drift, a legitimate homograph/layer split, or a same-root different
   object; only real drifts are written back.

Discovery and adjudication are separated roles: the discoverer (subagent) supplies raw
candidates without correctness knowledge; the main Agent adjudicates. A convergence claim
requires both the rule pass and the heuristic pass — rule pass alone is an unfinished
claim. Dialogue rows (INFO/DIAL) are prime drift territory precisely because display-name
scans exclude them; the heuristic pass must cover them, not inherit the A-pool exclusion.

## Usage

```text
py -3 .agents/skills/noun-consistency-scan/scripts/noun_consistency_scan.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --out _tmp/data/noun-scan \
  --pools A
```

Core pool (A, same Source → multiple Dest splits):

```text
--pools A --min-variants 2
```

Extension pools (each needs its own input; run separately when needed):

```text
# B: construction-suffix divergence (experimental, noisy) — same XML input
--pools B

# C: official-name HIGH candidates on translated rows — needs noun-audit --json output
--pools C --audit .work/<plugin>/reports/<plugin>-noun-audit.json --terms mods/<plugin>/terms.json

# D: official names present (occ≥3, name-level hits) but not registered in terms
--pools D --scan .work/<plugin>/context/<plugin>-proper-noun-scan.json --terms mods/<plugin>/terms.json

# E: untranslated rows (Dest==Source) with no dev-residue signal — needs human visibility judgment
--pools E
```

Outputs go under `--out` (default `_tmp/data/noun-scan`):

- `pool-<letter>.json` — full pool array
- `pool-<letter>-<n>.json` — shards (cap `--cap`, default 170), each entry carries
  `kid` (A1..An / D1..Dn) or `idx` (C/E rows) as the stable key for subagent verdict maps
- `manifest.json` — {pool letter: [shard paths]}

## Row model

Rows are xTranslator `<String>` elements; `REC`/`EDID` are child elements (not attributes).
Dialogue rows (`INFO:NAM1`, `DIAL:FULL`) are excluded — the A/B/D/E pools target display
names (WEAP/ARMO/SPEL/MGEF/CELL/LCTN/NPC_/QUST/FACT/MESG/… :FULL/:DESC/:NNAM etc).

## Interpreting pool A entries

```json
{"kid": "A1", "source": "Remarkable Gravestone", "variant_count": 2,
 "variants": ["非凡墓碑", "特别的墓碑"],
 "rows": [{"idx": 1105, "rec": "ACTI:FULL", "dest": "特别的墓碑"}, ...]}
```

Adjudication defaults (Agent judgment still required):

- Same object across RECs (ACTI object name vs MESG title) → unify to the majority /
  player-visible form (MESG/REFR/LCTN usually beats ACTI/editor rows).
- Official dictionary shows the layer split itself (Guard → 守卫 in FACT names, 卫兵 in
  city-guard compounds like 卫兵营房) → legitimate, document it, do not unify blindly.
- Same source, two different objects (e.g. Boulder as 巨石 in an earth-spell line vs 投石
  in a power-core line) → check EDID ancestry first: same EDID family must be same name;
  different EDID families with different meanings are legitimate layering.
- Before choosing a target form, check the official dictionary (`lookup --contains`) and
  the MOD DICTIONARY.md; never invent a third form.

## Safety boundaries

- Read-only: never edits the XML, terms.json, DICTIONARY.md or any MOD document.
- Never auto-fixes splits. Output is evidence for Agent adjudication → DICTIONARY.md /
  terms.json registration → contract recompile → gate → writeback (skyrim-term-contract-workflow).
- Deterministic output: same input XML → byte-identical JSON (dict order preserved).
- Do not run on the source XML (untranslated); target the translated canonical.

## Verification

Smoke checks after any change:

```text
py -3 -m py_compile .agents/skills/noun-consistency-scan/scripts/noun_consistency_scan.py
py -3 .agents/skills/noun-consistency-scan/scripts/noun_consistency_scan.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml --out _tmp/data/noun-scan-smoke --pools A
```

A pool group count after a convergence pass: each remaining group must be re-read from the
current XML and judged on its own (same object vs legitimate layer split vs different
objects), never skipped on the strength of an earlier label or record. A count of 0 is the
converged target, but low counts are not proof of convergence — the scanner only groups by
Source text; it cannot see REC-field semantics, so a legitimate verb/noun homograph split
(e.g. ACTI:FULL name vs ACTI:RNAM activation prompt) stays visible forever and is resolved
by reading the rows, not by re-running the scanner. Never auto-fix from scan output alone.
