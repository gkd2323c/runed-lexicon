---
name: local-model-translator
description: Use a local Ollama translation model such as Hy-MT2 as a constrained base-translation worker inside the Skyrim MOD localization pipeline. Use this skill whenever an Agent has already understood the quest/dialogue/book context and wants the local model to translate prepared English strings into Chinese, especially for batch translation with fixed terminology, protected placeholders, deterministic IDs, or a 32K local context window. The high-level Agent remains responsible for semantics, spoiler boundaries, terminology decisions, and review; this skill only delegates the basic translation pass and validates the worker output before it can enter translation-executor results.
compatibility: Requires Python 3.10+ and a reachable Ollama HTTP API. Optional dependency opencc (pip install opencc) enables the simplified-Chinese gate; without it the gate degrades to a no-op. The current tested local convention is hy-mt2-32k:latest with num_ctx=32768 and temperature=0. This skill never writes xTranslator XML.
metadata:
  version: "0.2.0"
---

# Local Model Translator

Use the local model as a translation engine, not as the semantic authority for the MOD.

The intended split is:

```text
high-level Agent
  -> understands plot / speaker / quest stage / lore / spoiler boundary
  -> decides terminology and wording constraints
  -> prepares a compact worker request
local Hy-MT2 worker
  -> performs the basic English -> Chinese translation
deterministic checks
  -> verify IDs, order, protected tokens, required terminology
high-level Agent
  -> reviews semantics and Chinese quality
translation-executor
  -> owns the final structured translation result contract
xtranslator-xml-writer
  -> performs later deterministic XML writeback
```

Do not promote the local model into an Agent merely because it has a large context window. Its job is deliberately narrower.

Do not use this skill as an excuse to skip the Agent's own translation work. The local model is a first-pass language worker, not a replacement for semantic reading, terminology judgment, or review.

## Before actual translation

Identify the target `mods/<plugin>/` directory. When resuming work, read `PROGRESS.md` so the active source/context/result files are not guessed.

Use `translation-context-builder` when xTranslator + xEdit structural evidence needs to be assembled first. The local model should receive the Agent's resolved, compact interpretation of that evidence rather than the entire raw evidence dump by default.

## Semantic authority stays with the Agent

Resolve high-level questions before calling the local model:

- who is speaking and to whom;
- the current quest / story stage;
- what a pronoun or ambiguous word refers to;
- whether a term is a proper name, common name, alias, title, or later-revealed identity;
- which TES / MOD terminology must be used;
- character tone and register;
- what information is allowed to be revealed at this point in the story.

Do not give the worker later-story truths and then ask it to "avoid spoilers" if those truths are not needed for the current translation. Preserve the source's information boundary in the request itself.

Ordinary translation freedom still belongs to the local worker: natural Chinese syntax, local phrasing, and routine sentence-level choices do not need to be micromanaged by the Agent.

### Audit the batch before delegation

Do not call the local worker immediately after obtaining a context-builder payload. First inspect the actual semantic unit and classify the translation-sensitive material that appears in it.

At minimum, check:

- every identity-bearing proper name, place, race, faction, item, spell, and lore term that should use an established Chinese form;
- unknown or deliberately untranslated narrative words whose distinct spelling carries story information;
- ambiguous common words whose meaning is quest-specific in this scene;
- pronouns / ellipses whose referent may need to be stated explicitly in natural Chinese;
- pauses, repetitions, broken speech, and intentionally confused wording that must not be normalized away.

For important source material, make an explicit choice before delegation: fixed translation, literal KEEP, item guidance, or intentionally free wording. The local model's general Skyrim knowledge is not a substitute for this audit.

Real Sirenroot testing showed why this matters: omitted term decisions let `Ingun` and `Ayleid` drift to fresh transliterations, unknown Ayleid words were "helpfully" localized, and a context-only identity reveal (`Yes, that's me.`) lost the name that Chinese needed to repeat.

## Do not outsource the Agent's job

Before calling the local worker, the high-level Agent must have done enough reading to understand the semantic unit it is delegating. In practice, the Agent should already be able to answer the questions that would materially change the translation without asking Hy-MT2 to discover them.

The local worker is appropriate after the Agent has already:

- read the relevant continuous quest / dialogue / book / record context rather than isolated rows;
- identified speaker, addressee, quest stage, and important references when that evidence exists;
- resolved or explicitly marked important terminology decisions;
- protected the current story-information boundary so later revelations are not leaked into early text;
- decided any non-obvious per-item meaning that would change the Chinese translation;
- selected a coherent translation unit instead of blindly sending "everything untranslated".

Do not send raw `translation-context-builder` output to the local model merely because it contains lots of context. The builder output is evidence for the Agent to read and interpret. The Agent should condense that evidence into the `background`, `terminology`, `style`, and item-level `guidance` fields that the worker actually needs.

Do not ask the local model to perform tasks such as:

- "read this whole MOD and work out the story yourself";
- "infer who is speaking from these hundreds of records";
- "decide which official dictionary hits are correct";
- "work out whether this alias is a spoiler";
- "translate every untranslated row and choose whatever names seem right".

Those are Agent responsibilities. If the Agent cannot yet form a compact worker request without delegating those judgments, continue semantic analysis first instead of calling Ollama.

The existence of a local worker should reduce repetitive sentence-level labor, not reduce the amount of semantic understanding performed by the Agent.

## Prepare a worker request

Create a UTF-8 JSON request under `.work/<plugin>/local-requests/`. A minimal request looks like:

```json
{
  "schema_version": 1,
  "task_id": "sirenroot-info-000",
  "model": "hy-mt2-32k:latest",
  "background": "The speaker is an old mercenary. He is terse and distrustful. The current scene takes place before the party enters the ruin.",
  "style": [
    "Natural Simplified Chinese consistent with Skyrim localization",
    "Keep dialogue concise and character-appropriate"
  ],
  "terminology": [
    {"source": "Whiterun", "target": "白漫城", "enforce": true},
    {"source": "sweetroll", "target": "甜卷", "enforce": true}
  ],
  "keep_literals": ["Mala", "Aral", "Nen"],
  "items": [
    {
      "id": "04012345#INFO:NAM1",
      "record_type": "INFO:NAM1",
      "source": "<Alias=Player>, take %d sweetrolls to Whiterun before dusk.",
      "guidance": "An urgent instruction, not a joke.",
      "required_phrases": []
    }
  ]
}
```

### Request fields

- `schema_version`: currently `1`.
- `task_id`: stable name for this worker call.
- `model`: optional; defaults to `hy-mt2-32k:latest`.
- `background`: compact semantic background already resolved by the Agent. String or list of strings.
- `style`: optional string or list of concise style instructions.
- `terminology`: explicit English -> Chinese decisions relevant to this request.
  - `enforce` defaults to `true`.
  - Put fixed batch terminology here, not an indiscriminate dump of the whole dictionary.
- `keep_literals`: optional player-visible source words that must remain exactly unchanged when they occur. Use this for narratively unresolved ancient-language tokens, deliberate labels, or similar literals. This is separate from runtime placeholders.
- `items`: source strings to translate.
  - `id` must be unique and is copied unchanged through the worker response.
  - `source` is the exact source text to translate.
  - `record_type` is optional read-only metadata. It is not placed inside the source line.
  - `guidance` is optional per-item semantic clarification already decided by the Agent.
  - `keep_literals` optionally adds item-specific exact literals to preserve.
  - `required_phrases` optionally lists Chinese phrases that must appear even when their English wording exists only in surrounding context. Use this sparingly as an acceptance gate for already-resolved referents, identity confirmations, or ellipses that Chinese must make explicit. A translation-specialized model may refuse source-external additions; if it fails this gate, prefer Agent takeover over repeated retries.

The bundled script automatically extracts conservative runtime-sensitive tokens from every source. Do not hand-edit source text merely to make it easier for the model.

## Keep the worker context compact

The current local Ollama model is configured for a 32K context window. That is capacity, not a target prompt size.

Working defaults from the current local smoke tests:

- normally aim for about 8K-16K tokens total;
- 16K-24K is acceptable for unusually large coherent units;
- above roughly 24K, prefer Agent-side context compression or splitting by semantic unit instead of filling the window;
- keep hard rules, required terminology, and the source block near the end of the prompt after the background.

These are operational defaults, not model-theory limits. Real MOD evidence should override synthetic benchmark assumptions when later testing gives stronger data.

Batch by semantic coherence rather than an arbitrary row count. The current local smoke test preserved 100 ordered rows and their protected tokens, so there is no reason to force 10-20 row batches when one coherent dialogue/book/quest unit naturally contains more.

## Inspect the rendered prompt

Before a new request shape or a difficult batch, inspect exactly what the local model will receive:

```text
py -3 .agents/skills/local-model-translator/scripts/ollama_translate.py prompt .work/sirenroot/local-requests/sirenroot-local-request.json
```

The renderer deliberately places background first and repeats the hard translation contract immediately before the source items. This reduces completion-style drift in long prompts.

## Run the local translation worker

Use the HTTP API wrapper rather than piping Chinese prompts through the Windows shell. This avoids command-line encoding damage to terminology.

```text
py -3 .agents/skills/local-model-translator/scripts/ollama_translate.py run .work/sirenroot/local-requests/sirenroot-local-request.json --output .work/sirenroot/local-results/sirenroot-local-response.json
```

Useful overrides:

```text
--host http://127.0.0.1:11434
--model hy-mt2-32k:latest
--num-ctx 32768
--temperature 0
--num-predict 4096
--timeout 600
--keep-alive 5m
```

For batch production, keep `temperature=0` unless real A/B review shows that sampling gives materially better Chinese. The local worker is easier to inspect and rerun when identical inputs produce identical drafts.

## Worker output and hard checks

The script writes a JSON response containing:

- the task and model configuration;
- one translation for every requested ID;
- the original source for traceability;
- extracted protected tokens;
- Ollama timing / token metrics;
- the raw model output;
- a deterministic validation report.

The worker validation checks:

- every requested ID appears exactly once;
- IDs stay in the requested order;
- no unexpected ID is added;
- no completed translation is empty;
- protected runtime tokens have the exact same multiset as the source;
- source line-break structure survives the local-model round trip;
- every enforced terminology entry that occurs in a source item also occurs in that item's translation;
- request/item `keep_literals` remain byte-for-byte present when they occur in that source item;
- every item-level `required_phrases` entry appears in the translation;
- non-`stop` Ollama completion reasons are surfaced.

Protected-token extraction is deliberately conservative and covers common Skyrim/runtime-sensitive forms such as:

- `<Alias=...>` / `<Global=...>` and other angle-bracket tags;
- `%s`, `%d`, and similar printf-style placeholders;
- `{...}` and `[...]` variable-like forms;
- XML / HTML entities such as `&amp;`;
- escaped control sequences such as `\n`.

A failed worker validation means the local draft is not eligible for automatic promotion into a translation result. Fix the request/prompt or re-run the affected batch. Do not silently repair a missing placeholder and then pretend the worker output passed.

For `required_phrases` failures specifically, one retry with clearer compact guidance is enough. If the worker still omits context-carried material that the Agent has already decided Chinese needs, translate or rewrite that item at the Agent layer. The worker is not required to be good at source-external expansion.

Revalidate an existing response without calling Ollama:

```text
py -3 .agents/skills/local-model-translator/scripts/ollama_translate.py validate .work/sirenroot/local-results/sirenroot-local-response.json --request .work/sirenroot/local-requests/sirenroot-local-request.json
```

## 安全生成 executor 导入 map

将 worker 响应转入 executor 时使用 `import-map`，禁止自行按 Source 合并、按数字猜 ID 或手改 `validation.ok`。先准备显式绑定 JSON，例如 `{"worker-id": "result-translation-unit-id"}`，覆盖请求中的每个 ID；目标必须唯一。

```text
python .agents/skills/local-model-translator/scripts/ollama_translate.py import-map response.json --request request.json --result result.json --bindings bindings.json --output new-review-map.json
```

工具重新验证原始 `raw_output` 和结构化 `translations`，要求二者一致、task_id 一致、`done_reason=stop`，不信任保存的验证标记；拒绝缺占位符、缺条目、重复 ID、错误绑定、Source 不一致及非 PENDING 目标。只创建新 map，不覆盖任何已有文件，不修改 result。输出全部是 `REVIEW/LOW`，含预期旧译文；结构通过不等于语义通过。旧响应缺 raw_output 或停止原因时拒绝此自动导入路径，不能伪造字段补过门禁；人工修订结果也不能继续冒充原始 worker 响应。

随后用 translation-executor 的 filler 应用 map，再逐条语义审阅并更新状态，最后运行 executor validate / gate。map 中的旧值检查只能保护译文字段；输入 identity/source 是否漂移仍须后续 executor 校验。当前一次处理一个请求与一个 result，不自动跨批消重或复用同源译文。

永久回归：`python .agents/skills/local-model-translator/scripts/test_import_map.py`。已有工具覆盖该导入需求时，不另写临时恢复或合并脚本。

## Review before translation-executor

Passing the worker validator proves structural compliance only. It does not prove that the translation is semantically correct or good Skyrim Chinese.

After a worker response passes:

1. Review every translated item against the original semantic unit and the full MOD-local rules. Do not spot-check a large local-model batch and assume the uninspected remainder is fine.
2. Compare the wording with the surrounding dialogue / quest / book flow, not just the individual English source line.
3. Explicitly check subject/object relations, direction of transfer, singular/plural reference, modality, uncertainty, ellipses, deliberate repetitions, and unfinished speech. Fluent Chinese can still be wrong on these details.
4. Correct ordinary wording mistakes, speaker-tone problems, bad terminology use, semantic drift, and suspiciously literal or generic phrasing at the Agent layer.
5. Treat repeated or patterned mistakes as evidence that the worker prompt or terminology package needs correction; do not manually patch the same failure across hundreds of rows while leaving the cause intact.
6. Transfer only accepted translations into the appropriate `translation-executor` draft/result items.
7. Run the `translation-executor` validator before treating the batch as structurally ready for later XML writeback.

The local worker response is an intermediate draft artifact, not the final translation contract.

The high-level Agent must not report a batch as "translated", "reviewed", or "complete" merely because Ollama returned text and the deterministic worker validator passed. Worker validation proves formatting and protected-token compliance; semantic acceptance still requires Agent review.

### Warning signs of over-reliance

Stop and move work back to the Agent layer when any of these patterns appear:

- the worker request has a giant raw context dump but almost no Agent-written semantic guidance;
- important names or concepts are left for the worker to invent despite available context evidence;
- the Agent cannot explain why a translation is correct except that "Hy-MT2 produced it";
- batches are promoted after structural validation with no item-by-item semantic review;
- the worker is repeatedly asked to repair its own lore, speaker, identity, or spoiler mistakes;
- batch size is chosen mainly to minimize Agent attention rather than preserve semantic coherence.

These are workflow failures even when the local model output looks fluent.

For concrete examples from the expanded Sirenroot test, including terminology drift, ancient-word overtranslation, context-carried identity loss, ambiguity over-resolution, relation changes, and broken-speech smoothing, read `references/real-mod-evaluation.md` when tuning this worker or deciding whether more automation is safe.

## Ollama 运行时事实（环境常态，非故障）

- **模型闲置会自动从显存卸载**：Ollama 长时间未被调用时会把模型从 VRAM 卸载以释放资源。这是预期行为，不是错误、不需要修复。再次调用时会自动重新加载模型，首请求延迟变高属正常，不是故障。
- 闲置卸载的典型表现是请求变慢或首请求超时；`WinError 10061 / 无法连接` 则是服务进程不在线的信号。两者要区分：**先确认服务进程是否存活（如 `Get-Process ollama` 或 GET /api/tags），再决定是否重启服务**；仅仅因模型卸载就重启 `ollama serve` 属于多余动作。
- 遇到超时/慢首请求时，直接重试或补一次热身请求即可，不要臆断服务挂了。

## Failure handling

If Ollama cannot be reached, report the connection failure clearly. Do not fall back to an unrelated cloud model without user direction. 区分环境常态与真故障：模型闲置卸载（自动重载，首请求变慢）不是连接失败，只有进程不响应 / API 拒绝连接才算（见上节）。

If the configured model is missing, report the requested model name. Do not download or pull a model automatically.

If the model returns malformed IDs, missing placeholders, glossary failures, or a length-truncated result:

- keep the raw response for diagnosis;
- reject that worker result;
- reduce or improve the request and retry only the affected semantic unit;
- prefer stronger compact guidance over dumping more raw context.

## Safety boundaries

- Never modify or reserialize xTranslator XML in this skill.
- Never edit `<Source>`, `<Dest>`, `<EDID>`, `<REC>`, or XML metadata here.
- Never let the local worker decide lore identity, quest logic, speaker identity, or spoiler-sensitive terminology when the Agent has not resolved it.
- Never use the local worker as a substitute for reading the relevant semantic unit.
- Never promote an unreviewed worker batch directly into final translation results simply because deterministic checks passed.
- Never treat a terminology list as an automatic substitute for reading context.
- Never feed a whole MOD dictionary or giant context dump merely because the window is available.
- Never claim a worker-validation pass is human review or in-game validation.
- Do not introduce source/version hashes into this worker layer unless a later concrete writeback-safety need requires them; `translation-executor` / XML writeback already own the relevant provenance checks.

## Validate this Skill

Before using a modified version, follow `.agents/skills/skill-creator/SKILL.md` and run the preflight first:

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/local-model-translator
py -3 -m py_compile .agents/skills/local-model-translator/scripts/ollama_translate.py
```

Render the bundled smoke request without calling Ollama:

```text
py -3 .agents/skills/local-model-translator/scripts/ollama_translate.py prompt .agents/skills/local-model-translator/assets/smoke-request.json
```

When `hy-mt2-32k:latest` is already registered and Ollama is running, run the live smoke test:

```text
py -3 .agents/skills/local-model-translator/scripts/ollama_translate.py run .agents/skills/local-model-translator/assets/smoke-request.json
```

The smoke response should preserve all four IDs in order, keep `<Alias=Player>`, `%d`, `%s`, and `Mala` exactly, and use `白漫城` / `甜卷` where required.

