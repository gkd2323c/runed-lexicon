---
name: translation-fidelity-scan
description: "Deterministic source-fidelity scan for Skyrim MOD translations: negation loss, empty/untranslated lines, English residue, and anachronism candidates. Use when a translated batch or canonical XML needs a mechanical fidelity check before review, or when hardfix-style rule scanning is needed without hand-copied ban tables. Complements translation-quality-gate (contract conformance) and fantasy-context-auditor (semantic judgment)."
compatibility: Python 3.10+. Standard library only. Read-only, never modifies translations.
metadata:
  version: "0.3.0"
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

`--report` 写 JSON（fails + anachronism_candidates + punctuation_mismatches）。退出码 0 无 FAIL / 1 有 FAIL / 2 用法错误。

## 规则说明

- 疑似丢否定：源文含 not/no/never/without/n't 且非双重否定（not unX），译文无中文否定表达。中文否定远多于不/没，名单见脚本 NEG_PAT。
- 空译/未译：译文空或与源文完全相同（KEEP 行同样会命中，属预期内噪音，复核时排除）。
- 英文残留：去标签/占位符后仍有 4+ 字母串，且源文非纯英文句。
- 时代错位候选：命中候选词即标记，不定罪（议会/网络/医生等在合法语境可存在）。
- **句末标点一致性**（`punctuation_mismatches`）：源文终结标点类别（句号/叹号/问号/省略号/无）与译文类别不一致即报，分 `lost`（源有译无）/ `added`（源无译有）/ `changed`（类别不同）。
  - **`QUST:NNAM` 目标句的 `lost` 计为机械 FAIL**（裁决 A，2026-09-12）：任务目标句源文带句末标点则译文必须带，缺则 FAIL 并阻断验收（源词缩写点如 `Acc.`、对话被打断的 `...`→「——」会误报，误报行由 Agent 对照源文裁决）。
  - 其他 REC 的差异仅报不定罪（对话台词标点自然差异）。
  - 判类前先剥离尾随引号/括号（`他说“好！”` 判为 exclam；末尾「（可选）」标记不影响主体判类）。
  - **已知盲区**：源文末尾带括号标记（如 `... (Optional)`）时，两侧类别均为 none，主体缺标点不会被报；此类行需人工复核（当前全库 6 行均正确）。

## 性能

- **全库扫描基线（Artaeum，14968 行 × 1072 禁形）：2.2s**（回归线 <5s）。
- 禁形 pattern 在 `load_contract` 内**预编译**；禁止退回存 `re.escape` 字符串再在循环里 `re.search(pat, ...)`。旧实现在 check_units 内现场编译（866 禁形 × 14968 行 ≈ 1300 万次 `re.compile`），全库实测 **293s**（30s 缺陷线 10 倍）。回归测试 `test_fidelity_scan.py` 含结构断言（pattern 必须为已编译对象）与性能冒烟（250 禁形 × 2500 行 < 3s）。

## 安全边界

只读。不改 XML、译文、词库、契约。候选不自动 FAIL（独立计数），fails 需 Agent 逐条消解。

## 来源

`_tmp/scripts/hardfix-triage.py` 与 `scan-anachronism.py` 的扶正（2026-09-09）：禁形改读契约，候选表外置，前两者的手抄 BANS 已合入 global/terms。

句末标点一致性检查与预编译性能修复（2026-09-12）：因 NI-QUST-001~003（174 行保留句号）与 NI-QUST-007~009（192 行丢失句号）两波系统性反向偏差而加；同一次修复将禁形 pattern 改为预编译（293s → 2.2s）。当日裁决 A（统一保留句号）后，`QUST:NNAM` 的 `lost` 从“只报”提升为机械 FAIL；192 行已回正（gen126），全库 qust_lost_fail=0。
