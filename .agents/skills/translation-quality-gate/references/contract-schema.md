# Translation Contract Schema v1

This is the tracked interface shared by the terminology compiler, semantic binding
step, translation quality gate, and downstream review tooling. Markdown term
decisions (`DICTIONARY.md` / `GLOSSARY.md`) remain the human-readable source of
truth; a compiled contract is a deterministic machine-readable derivative.

## Design principles

1. **Term definitions and unit bindings are separate.** A term definition says how
   a concept is translated and enforced. A unit binding says which source span in a
   concrete translation unit has been semantically identified as that concept.
2. **`decision_status` and `enforcement` are independent axes.** A translation may
   be confirmed while still being `FORBIDDEN_ONLY` because aliases, knowledge
   boundaries, or spoiler-sensitive naming make automatic required insertion unsafe.
3. **Required occurrence is a unit-level constraint.** A target is required only
   when an explicit binding has `required: true`.
4. **The gate is read-only.** Detection and enforcement never rewrite translation
   text.

## Top-level shape

```jsonc
{
  "schema_version": "1.0",
  "plugin": "Example.esp",
  "source_xml_sha256": "optional sha256 of the bound source XML",
  "compiled_from": {
    "dictionary_md": "sha256",
    "glossary_md": "sha256"
  },
  "terms": {},
  "unit_bindings": []
}
```

## Term definition

```jsonc
{
  "term_id": "example.argonian",
  "source": "Argonian",
  "decision_status": "CONFIRMED",
  "enforcement": "REQUIRED",
  "target": "阿尔贡",
  "forbidden": ["亚龙人", "阿戈尼安"],
  "match": {
    "kind": "forms",
    "accepted": ["阿尔贡"],
    "blocked_suffixes": ["尼亚", "尼亚人"]
  },
  "risk_flags": [],
  "origin": "OFFICIAL",
  "evidence": "official dictionary record",
  "note": ""
}
```

### Fields

- `term_id`: stable project-local identifier.
- `source`: canonical English source form for the concept.
- `decision_status`: `CONFIRMED`, `PROVISIONAL`, `REVIEW`, or `KEEP`.
- `enforcement`: `REQUIRED`, `FORBIDDEN_ONLY`, or `KEEP`.
- `target`: preferred Chinese target form.
- `forbidden`: known bad or disallowed variants checked as `TERM002` when the term
  is applicable.
- `match.kind`:
  - `forms`: match accepted forms as complete forms and reject configured blocked
    suffixes.
  - `contains_phrase`: target presence succeeds when any accepted phrase occurs.
- `match.accepted`: accepted target forms.
- `match.blocked_suffixes`: suffixes that turn an otherwise accepted substring into
  a disallowed derived form (`TERM003`).
- `risk_flags`: semantic hazards such as `alias`, `knowledge_boundary`,
  `naming_level`, or `spoiler`. Non-empty risk flags prohibit automatic/global
  required binding; those units need explicit semantic binding.
- `origin`: provenance class such as `OFFICIAL`, `MOD_DICTIONARY`, `GLOSSARY`, or
  `PROJECT`.
- `evidence`: concise provenance/evidence pointer.
- `note`: optional human-readable rationale.

`decision_status` is inherited from the human terminology decision. Enforcement is
an executable policy and must not be inferred merely from the status.

## Unit binding

```jsonc
{
  "translation_unit_id": "xml-index:92",
  "bindings": [
    {
      "term_id": "example.argonian",
      "source_span": "an Argonian",
      "required": true,
      "required_target": "阿尔贡"
    }
  ]
}
```

- `translation_unit_id` must match the stable unit ID used by translation-result
  JSON.
- Units with no relevant terminology may have no binding entry or an empty binding
  list.
- `source_span` is provenance/alignment evidence and must actually occur in the
  source unit so a stale or shifted binding can be detected.
- `required: true` means the target must appear according to the term match rule.
- `required: false` explicitly records a legitimate omission, such as anaphora or
  zero-form reference, instead of leaving that case to fuzzy checker behavior.
- `required_target` is optional and defaults to the term's `target`.

## Gate codes

| Code | Meaning |
| --- | --- |
| `TERM001` | a required bound target is missing |
| `TERM002` | a forbidden variant appears |
| `TERM003` | an accepted substring is followed by a blocked suffix |
| `TERM004` | a project-wide global ban is violated |
| `KEEP001` | a local KEEP value was modified |
| `KEEP002` | a project-wide KEEP value was modified |
| `PLACEHOLDER001` | a protected runtime token was lost or changed |
| `CHAR001` | non-simplified Chinese detected by the optional charset check |
| `XML001` | source XML identity/structure drift is detected during pre-writeback checks |

The gate emits `PASS`, `FAIL`, or `WARNING` plus concrete locations. It never
auto-fixes text.

## Translation-result integration

Executor-style translation JSON already carries `translation_unit_id`. A compiled
contract may be referenced at batch level rather than embedded into every unit.

The normal flow is:

```text
DICTIONARY.md / GLOSSARY.md
        │
        ├─ deterministic compiler -> terms
        └─ semantic analysis      -> unit_bindings
                                      │
translation-result JSON + compiled contract
                                      │
                                      v
                              translation quality gate
```

The compiler may construct term definitions from declared terminology decisions,
but it must not silently invent spoiler-sensitive or otherwise ambiguous semantic
bindings.
