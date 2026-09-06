---
name: translation-executor
description: Translate Agent-ready Skyrim mod localization batches produced by translation-context-builder into structured, reviewable translation draft JSON, then validate identity, source fidelity, protected placeholders, and batch completeness without touching xTranslator XML. Use whenever actual translation of prepared Skyrim/MOD context is about to begin, when an Agent needs a deterministic result contract for translated strings, or when a translation draft must be checked before any XML writeback step.
compatibility: Requires Python 3.10+ and a translation-context-builder JSON payload. Uses only the Python standard library. This skill never writes xTranslator XML.
metadata:
  version: "0.2.3"
---

# Translation Executor

Translate one prepared context batch into a deterministic draft artifact. Keep semantic work in the Agent and structural safety in the bundled validator.

This is deliberately the layer immediately before XML writeback. It produces translation results only; it does not edit `<Dest>` or any source project file.

## Input

The target MOD directory is identified from `inputs.mod_dir`. Use JSON produced by `translation-context-builder`, for example:

```text
.work/sirenroot-info-context.json
```

The input should contain one or more `batches`, each with traceable `translation_unit_id` values and the original XML metadata, dialogue context, and terminology evidence.

For dialogue, inspect the actual structural evidence under each entry's `dialogue_context`, especially:

- `dialogue.topic_text`
- linked Quest / Branch
- `info.responses`
- `info.conditions`
- `info.speaker_candidates`
- sibling INFO references

Do not infer a speaker merely from prose when structural evidence is absent.

## Create a draft result

Initialize a result file for one batch:

```text
py -3 .agents/skills/translation-executor/scripts/translation_result.py init .work/sirenroot-info-context.json --batch 0 --output .work/sirenroot-info-translation-000.json
```

Use the Python command that passed `skill-creator` preflight if it is not `py -3`.

The generated result copies immutable identity fields and leaves only translation-facing fields pending:

- `translation`
- `status`
- `confidence`
- `notes`
- `terminology_decisions`

Do not change `translation_unit_id`, XML index, EDID, REC, source, original destination, context hashes, or protected-token metadata.

## Translate the batch

For every result item:

1. Locate the same `translation_unit_id` in the context batch.
2. Read the source together with its dialogue / quest / speaker evidence and terminology hits.
3. Translate naturally for Skyrim Chinese localization rather than preserving English word order.
4. Prefer established official terminology when the evidence is unambiguous.
5. Follow MOD-local `CONFIRMED` decisions. Treat `PROVISIONAL` and `REVIEW` terms as context rather than immutable truth.
   - A missing Chinese form for a MOD-original personal name is not, by itself, a reason to stop or return the decision to the user.
   - For a name that can reasonably be transliterated, choose a Skyrim-appropriate Chinese transliteration, use it consistently, and treat it as a reversible `PROVISIONAL` decision.
   - For culture-specific descriptive names, follow the naming convention of the relevant TES culture. For example, Argonian translated names such as `Watches-The-Roots` are semantic names rather than phonetic names.
   - Prefer existing related Chinese localization evidence when available, but the Agent is responsible for making a usable provisional choice when no authoritative translation exists.
6. Preserve every protected runtime token exactly.
7. Set `status`:
   - `TRANSLATED` when the wording is ready for ordinary review.
   - `REVIEW` when a meaningful unresolved semantic ambiguity remains, such as a core concept whose translation depends on what the thing actually is.
   - `KEEP` when the entry has been inspected and intentionally must remain exactly identical to the source, such as an author/creator name, internal technical label, or other non-localizable identifier surfaced by the localization export.
8. Set `confidence` to `HIGH`, `MEDIUM`, or `LOW`.
9. Use `notes` only for information that helps later review; do not narrate routine translation choices.
10. Record important non-obvious terminology choices in `terminology_decisions` when useful.

If an input entry carries `review_reasons` but the Agent can resolve the issue from context, keep the immutable reasons as provenance and record the resolution in `terminology_decisions`. A `TRANSLATED` item with an explicit terminology resolution is considered handled; do not erase the original context evidence.

Do not mark an item `REVIEW` merely because translation is subjective, because a MOD-original name lacks an official translation, or because a transliteration could later be polished. `PROVISIONAL` terminology is allowed to move forward. Reserve `REVIEW` for ambiguity that materially changes meaning, lore interpretation, quest logic, or a shared core concept.

Do not fake completion by translating technical identifiers into Chinese. Use `KEEP` for genuinely non-localizable entries. A `KEEP` result must copy the source text exactly; the validator rejects altered text under this status and does not emit the ordinary identical-source warning for it.

### MOD-original names

When a MOD introduces a new personal name, solve the ordinary localization problem instead of escalating it:

1. Check whether the name belongs to an established TES naming convention or culture.
2. Use pronunciation/spelling, character culture, and nearby names to form a natural Chinese transliteration.
3. If a related existing localization provides a usable form, prefer that evidence unless it conflicts with the current project.
4. Mark the terminology decision as provisional when the evidence is not authoritative, but keep the translation item `TRANSLATED`.
5. Reuse the same provisional form everywhere. A later stronger source may revise it globally.

Examples of things that should normally move forward without user intervention:

- a new Maormer, Breton, Imperial, Nord, or other personal name that can be transliterated;
- a new Argonian descriptive name whose literal construction can be translated according to vanilla naming patterns;
- a surname whose spelling supports a stable transliteration.

Examples that can still justify `REVIEW`:

- a title/name deliberately built on a semantic pun that changes story interpretation;
- a core object or concept whose translation cannot be chosen responsibly until its role in the plot is understood;
- a term whose candidate translations imply different lore identities rather than merely different phonetic spellings.

## Protected tokens

The initializer extracts conservative runtime-sensitive tokens from each source, including:

- angle-bracket placeholders such as `<Alias=...>` and `<Global=...>`
- printf-style placeholders such as `%s` / `%d`
- common brace / bracket variable forms
- escaped control sequences such as `\n`

The validator compares their exact multisets before accepting a completed translation.

If the extractor flags an ordinary piece of text as a protected token, preserve it and note the case for later tool refinement instead of silently deleting it.

## Fill a draft from a translation map

> **2026-09-04 废弃警告**：`sanitize_translation_map.py` 曾把含特定引号格式条目的 JSON 结构（`, "status": "TRANSLATED"...`）泄漏进 translation 值，在 SB1 项目污染 1489 条。**不再使用该工具**。map 的 ASCII 引号问题改为：写 map 时直接用中文引号“”，写完 `json.load` 验证；确需清洗时用 `fill_translations.py` 的校验错误提示定位，手改源文件。

写 map 时注意：中文引号必须用“”，不用 ASCII `"`；写完先 `json.load` 验证格式，再应用 filler：

```text
py -3 .agents/skills/translation-executor/scripts/fill_translations.py \
  --result .work/sirenroot-info-translation-000.json \
  --map .work/sirenroot-info-map-blockA.json \
  --output .work/sirenroot-info-translation-000.json --force
```

The map is a flat JSON object keyed by `xml_index` (string form) whose values are
partial result items:

```json
{
  "813":  { "translation": "<grunt>", "status": "KEEP", "confidence": "HIGH", "notes": "拟声标签" },
  "1313": { "translation": "祈祷吧！你就要去见你的神了。", "status": "TRANSLATED", "confidence": "HIGH" }
}
```

Rules:
- Only PENDING items are touched; a mapped key that is already done is skipped
  unless `--overwrite`. A map key absent from the result is an error (scope mismatch).
- status ∈ TRANSLATED / KEEP / REVIEW; confidence defaults to HIGH when omitted.
- KEEP with translation != source is rejected early (the validator also checks).
- 修订已译条目时使用 `--overwrite`，并在 map 条目中提供可选的 `expected_translation`（精确旧译文）；旧值不匹配则返回 2，整个本次单文件输出不写入。此检查在非 PENDING 跳过判断之后；未指定 `--overwrite` 时仍按原规则跳过已译项。`translation` 必须是字符串。当前仍是单文件操作，不提供多文件原子提交。
- The map is the Agent's translation deliverable — keep it as a reviewable artifact
  alongside the result, rather than baking wording into throwaway code.

### Two keying modes

Default keying is by `xml_index`. When batch scope is unstable (散件收尾、跨批重叠、
或从 untranslated 1-based index 抄序号），改用 `--by-source`：map 以精确 Source 文本为 key，
与序号基准（skyrim-xml-tools 1-based / context·executor·writer 0-based）无关，且可读性更好：

```json
{
  "Typical Nord arrogance. Not all Argonians are alike.": {"translation": "典型的诺德式傲慢。不是所有阿尔贡人都一个样。", "status": "TRANSLATED", "confidence": "HIGH"}
}
```

```text
py -3 .agents/skills/translation-executor/scripts/fill_translations.py --result r.json --map m.json --output r.json --by-source --force
```

`--by-source` 限制：仅当 **map 用到的某个 source** 在 result 中重复（同一句多行）时才拒绝该 key 并要求改用 xml_index 模式（批内其他未映射的重复句不影响）；source 不匹配任何条目会报错（scope 不一致）。两种模式默认只填 PENDING，非 PENDING 条目跳过。

## Validate the result

After translating, run:

```text
py -3 .agents/skills/translation-executor/scripts/translation_result.py validate .work/sirenroot-info-translation-000.json --context .work/sirenroot-info-context.json
```

Validation fails if, among other things:

- the result belongs to a different context file or batch;
- an expected translation unit is missing or duplicated;
- an unknown translation unit was added;
- immutable source metadata changed;
- a completed item has an empty translation;
- a protected token changed, disappeared, or was added;
- a `KEEP` item does not preserve its source text exactly;
- an invalid status or confidence value was used;
- any item is still `PENDING`.

Use `--allow-pending` only for checking an initialized template or an intentionally incomplete work-in-progress file. A batch is not complete while pending entries remain.

Get a compact progress summary with:

```text
py -3 .agents/skills/translation-executor/scripts/translation_result.py summary .work/sirenroot-info-translation-000.json
```

## 多文件修订：生成新结果集合

已有 filler 的批量封装为 `scripts/fill_translation_set.py`，复用单文件校验，不复制翻译逻辑。用它代替一次性 apply/merge 脚本。manifest 路径相对于 manifest 所在目录解析：

```json
{"schema_version":1,"files":[{"result":"r1.json","map":"m1.json","output_name":"r1.json","overwrite":true,"by_source":false}]}
```

```text
python .agents/skills/translation-executor/scripts/fill_translation_set.py --manifest corrections.json --output-dir new-results
```

`output-dir` 必须不存在且父目录已存在。输出名只能是无目录的 `.json` 文件名，禁止重复输出名或重复 result。`overwrite:true` 时每条 map 必须声明 `expected_translation`。全部输入先读入快照，在输出父目录建立临时数据目录逐个调用现成 filler；任一失败不发布新结果目录，原始文件始终不写。全部通过后以同文件系统目录重命名发布；它是新副本集合的发布，不是多原文件覆盖事务，不保证突然断电或敌对并发下的事务语义。进程被强杀可能留下 `.fill-set-*` 数据目录，应核实后清理，不能将其视为有效交付。正常成功/失败均清理暂存数据，不生成临时脚本。

之后必须针对新集合运行 executor validate / gate，再按 MOD 的 PROGRESS 决定采用哪一组。filler 成功仅代表 map 应用成功，不代表语义审阅或 XML 写回验证通过。永久回归：`python .agents/skills/translation-executor/scripts/test_fill_set.py`。

## 跨批查询与门禁审阅（只读）

需要查询已生成的结果、按 REC/状态/原文筛选、展开 gate 警告时，使用现成 `query`，不要另写索引拼接或报告解析脚本。

```text
python .agents/skills/translation-executor/scripts/translation_result.py query --result r1.json --result r2.json --rec DIAL:FULL --text Whiterun --limit 20
python .agents/skills/translation-executor/scripts/translation_result.py query --result r1.json --gate-report gate.json --contract contract.json --limit 20
```

`--result` 可重复，必须显式指定同一审阅范围的文件，不能用历史目录通配符混入多代结果。筛选可用 `--uid`、`--rec`、`--status`、`--text`（原文或译文，不区分大小写）；分页用 `--offset` / `--limit`，`--limit 0` 返回全部。输出 JSON 含 `total_matches`、`returned`、`has_more` 与 `rows`，每行保留完整 `item` 和来源文件，不截短正文。

门禁审阅要求报告具有 `fails` / `warnings` 数组，以完整 `translation_unit_id` 关联，不解析 ID 尾部数字。警告无法唯一关联、输入文件重复、术语不存在或缺 `source` 字段均报错，不静默跳过。附带契约时返回完整 `term_definition`，不凭字段缺失推断误报。此入口只展示原报告和条目，不重跑 gate、不裁定语义、不声称报告仍适用于当前结果；若结果或契约已修改，先重新生成报告。当前不查询 context 批次，也不自动证明跨文件 ID 的命名空间相同。

回归：`python .agents/skills/translation-executor/scripts/test_result_query.py`（合成临时数据，不执行工作区临时脚本）。

## Output contract

The result JSON is a review artifact, not an XML patch. It contains:

- the SHA-256 of the exact context payload used;
- the selected batch index;
- the source xTranslator XML path and hash copied from the context payload;
- MOD context / dictionary hashes;
- one result object for every translation unit in the selected batch.

This provenance records exactly what evidence a draft was created from. A later writeback tool must reject the draft when the source XML identity no longer matches; context/dictionary hash differences are diagnostic evidence, not an automatic global-invalidity rule.

Treat provenance hashes as safety evidence, not as a requirement to constantly rebuild historical drafts. The source XML hash is the important writeback guard. A later edit to `CONTEXT.md` or `DICTIONARY.md` does not by itself invalidate every already validated translation result.

Rebuild or revalidate an older batch when the new context actually changes something that batch depends on, for example a corrected term, speaker identity, quest interpretation, or protected-token rule. Do not rebind hundreds of unchanged translations merely to make context/dictionary hashes match the newest documentation revision.

## Safety boundaries

- Never modify or reserialize xTranslator XML in this skill.
- Never edit `<Source>`, `<Dest>`, `<EDID>`, `<REC>`, or XML metadata here.
- Never silently drop a translation unit that is difficult to translate; use `REVIEW` with a concise reason.
- Never translate internal labels, creator names, or other deliberately non-localizable strings merely to reduce the untranslated count; mark inspected cases `KEEP`.
- Never invent missing speaker / quest / relationship data.
- Do not confuse `PROVISIONAL` with permanent or human-approved. The Agent should still make reversible working decisions for ordinary MOD-original names instead of leaving English in otherwise translated dialogue.
- Treat official dictionary hits as evidence. Conflicting or contextually inappropriate hits require judgment.
- A successful validator result means the draft is structurally ready for review/writeback tooling; it does not mean the Chinese wording has been human-approved or tested in game.

## 工作流治理常规回归入口

修改查询、filler、集合修订或 worker 导入相关逻辑后，运行：

```text
python .agents/skills/translation-executor/scripts/test_workflow_regressions.py
```

统一运行 5 个永久测试文件（查询、旧值保护、集合发布、导入校验、导入到 filler 的离线 CLI 链路）；任一失败返回非零。只用合成临时数据，不联网、不调用模型、不改真实译文。它不替代 translation-quality-gate 的真实事故语料自测和实际批次校验。

## Validate this Skill

Before using a modified version, follow `.agents/skills/skill-creator/SKILL.md` and run:

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/translation-executor
py -3 -m py_compile .agents/skills/translation-executor/scripts/translation_result.py
```

Then smoke-test initialization and pending validation against a real context payload:

```text
py -3 .agents/skills/translation-executor/scripts/translation_result.py init .work/status-context-v2.json --batch 0 --output .work/translation-executor-smoke.json --force
py -3 .agents/skills/translation-executor/scripts/translation_result.py validate .work/translation-executor-smoke.json --context .work/status-context-v2.json --allow-pending
```

