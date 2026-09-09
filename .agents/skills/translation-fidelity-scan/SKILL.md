---
name: translation-fidelity-scan
description: "Deterministic source-fidelity scan for Skyrim MOD translations: negation loss, empty/untranslated lines, English residue, and anachronism candidates. Use when a translated batch or canonical XML needs a mechanical fidelity check before review, or when hardfix-style rule scanning is needed without hand-copied ban tables. Complements translation-quality-gate (contract conformance) and fantasy-context-auditor (semantic judgment)."
compatibility: Python 3.10+. Standard library only. Read-only, never modifies translations.
metadata:
  version: "0.1.0"
---

# Translation Fidelity Scan

源文忠实度的确定性扫描。回答一个问题：译文有没有把源文的意思弄丢（否定、整句、英文残留），以及有没有时代错位嫌疑。

## 与 gate/auditor 的分工

- `translation-quality-gate`：契约符合度（term 绑定、全局禁形、占位符、简繁）。
- 本工具：源文忠实度（否定丢失、空译、英文残留）+ 时代错位候选。
- `fantasy-context-auditor`：语义层出戏判断（LLM，需人审）。

## 禁形零维护

禁形不手抄。`--contract` 给出编译契约时，自动从 `terms[].forbidden` 与 `global_bans[].forbidden` 提取禁形；不给则只跑忠实度规则。时代错位候选表见 `scripts/anachronism_candidates.json`（候选层，命中只标记，Agent 对照源文裁决；真出戏回填 `global-forbidden-words.json`）。

## 用法

```text
py -3 .agents/skills/translation-fidelity-scan/scripts/fidelity_scan.py --result <translation.json> [--contract <compiled.json>]
py -3 .agents/skills/translation-fidelity-scan/scripts/fidelity_scan.py --xml <translated-xml> [--contract <compiled.json>]
```

`--report` 写 JSON（fails + anachronism_candidates）。退出码 0 无 FAIL / 1 有 FAIL / 2 用法错误。

## 规则说明

- 疑似丢否定：源文含 not/no/never/without/n't 且非双重否定（not unX），译文无中文否定表达。中文否定远多于不/没，名单见脚本 NEG_PAT。
- 空译/未译：译文空或与源文完全相同（KEEP 行同样会命中，属预期内噪音，复核时排除）。
- 英文残留：去标签/占位符后仍有 4+ 字母串，且源文非纯英文句。
- 时代错位候选：命中候选词即标记，不定罪（议会/网络/医生等在合法语境可存在）。

## 安全边界

只读。不改 XML、译文、词库、契约。候选不自动 FAIL（独立计数），fails 需 Agent 逐条消解。

## 来源

`_tmp/scripts/hardfix-triage.py` 与 `scan-anachronism.py` 的扶正（2026-09-09）：禁形改读契约，候选表外置，前两者的手抄 BANS 已合入 global/terms。
