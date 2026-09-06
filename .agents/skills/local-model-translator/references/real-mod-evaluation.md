# Real MOD evaluation notes

These observations come from `evgSIRENROOT.esm` and are evidence for how this skill should be used, not a generic benchmark claim about every translation model.

## 2026-09-03 Hy-MT2 Q8 / Ollama 32K pass

Configuration:

- model: `hy-mt2-32k:latest` from `HY-MT2-7B-Q8_0.gguf`
- `num_ctx=32768`
- `temperature=0`
- high-level Agent read the MOD's `CONTEXT.md` and `DICTIONARY.md` in full first
- local prompts were split by coherent character / plot stage
- existing canonical Chinese was withheld until after each local-model pass

The expanded set contained 139 unique `INFO:NAM1` lines across Tilael, Cayrice, Peletius, and Yineel, plus 2 Crilduin BOOK records. All 141 worker outputs passed the structural worker contract: IDs/order were intact, completion stopped normally, and the tested BOOK formatting survived.

A strict manual semantic review for this iteration classified the 141 drafts as:

- 68 usable as-is
- 31 needing light wording / tone edits
- 42 needing substantive Agent rewrite
- 0 structural worker failures

These are review judgments, not an automated score. The important result is that a structurally perfect local-model batch can still contain many semantic or narrative-quality errors.

## Repeated failure patterns

### Missing terminology decisions

When a proper name was absent from the worker terminology block, the model freely localized it. Examples included `Ingun -> 英根` and `Ayleid -> 艾莱德` instead of the project's established forms.

Before each call, audit identity-bearing terms actually present in the source batch. Do not assume the worker will recover official or MOD-local names from general Skyrim knowledge.

### Unknown ancient words get "helpfully" localized

In Cayrice's inscription scene the source intentionally contains `Mala`, `Aral`, and `Nen`. Without a KEEP instruction, the worker produced Chinese-looking transliterations. These tokens are narratively unresolved at that point and must remain distinct.

Use `keep_literals` for player-visible narrative tokens that must stay exactly as written. This is separate from runtime placeholders.

### Context-carried meaning can disappear

When the player asks Yineel whether she is `Primes-His-Poison`, the NPC source is only `Yes, that's me.` The local worker naturally translated that as `对，就是我`, while the reviewed Chinese intentionally says `备妥其毒就是我` so the identity reveal lands clearly.

Use item-level `required_phrases` sparingly when the high-level Agent has already decided that Chinese must make an ellipsis, pronoun, identity, or omitted referent explicit even though the phrase is not present in the English source item itself.

In follow-up regression testing, Hy-MT2 twice ignored a hard `备妥其毒` requirement for source `Yes, that's me.` even when the exact requirement was repeated in prompt metadata. The validator correctly rejected both outputs. Treat this as a model-boundary signal: translation-specialized workers may strongly prefer source-faithful wording and resist context-driven additions. Do not loop indefinitely trying to force them. Let the Agent translate or rewrite that item directly.

### Ambiguity is often over-resolved

Examples:

- `But I can't... I need...` became `但我做不到……`, turning an emotionally ambiguous `can't` into inability.
- Peletius's repeated `I can't` during panic was similarly normalized into `做不到`.
- uncertainty and hesitation can become more definite even when the Chinese remains fluent.

Review modality, certainty, negation, hesitation, and unfinished speech against the source instead of judging fluency alone.

### Small relation changes matter

Examples included:

- `She traded some of her own magicka for mine` becoming wording that blurred who gave up whose magicka.
- `we can gather the others` becoming `我肯定能召集其他人`, changing agency.
- `leave him trapped here` becoming merely `把他留在这里`, dropping the important trapped state.
- singular `you` occasionally becoming plural `你们`.

Explicitly review subject, object, direction of transfer, singular/plural reference, and causal relations.

### Lore/mechanism words need Agent interpretation

The worker produced reasonable surface Chinese that was wrong for the story mechanism, for example `magical preservation` as a generic protective effect. In this MOD it refers to magic sustaining life.

Likewise the deliberately provisional inscription word `spring` was translated with an ordinary common meaning when the scene requires the current in-story mistranslation to remain consistent until later correction.

The high-level Agent owns these decisions. Add concise per-item guidance when a common English word has a story-specific interpretation.

### Broken speech and character rhythm degrade easily

The model often keeps the broad emotion but smooths away meaningful pauses, repeats, false starts, and joke timing. This matters especially for Tilael's black humor, Cayrice's memory confusion, Peletius's panic, and corrupted / influenced dialogue.

Review punctuation and disfluency as narrative content, not cosmetic style.

## Practical implication

The local model can remove a large amount of mechanical English-to-Chinese drafting work, but it does not reduce the Agent's semantic responsibility. Every worker line still needs high-level review. A good operating model is:

1. Agent reads and understands the semantic unit.
2. Agent audits terminology, KEEP literals, and context-carried requirements.
3. Local model drafts the Chinese.
4. Deterministic validation rejects structural violations.
5. Agent reviews every line for semantics, information boundary, voice, and TES usage.
6. Only accepted / corrected text enters `translation-executor`.
