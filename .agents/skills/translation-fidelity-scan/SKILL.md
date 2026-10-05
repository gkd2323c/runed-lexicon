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
  - **`QUST:NNAM` 目标句的 `lost` 计为机械 FAIL**：任务目标句源文带句末标点则译文必须带，缺则 FAIL 并阻断验收（源词缩写点如 `Acc.`、对话被打断的 `...`→「——」会误报，误报行由 Agent 对照源文裁决）。
  - 其他 REC 的差异仅报不定罪（对话台词标点自然差异）。
  - 判类前先剥离尾随引号/括号（`他说“好！”` 判为 exclam；末尾「（可选）」标记不影响主体判类）。
  - **已知盲区**：源文末尾带括号标记（如 `... (Optional)`）时，两侧类别均为 none，主体缺标点不会被报；此类行需人工复核（当前全库 6 行均正确）。

## 过度泛化扫描（`overgeneralization_scan.py`）

与 `translation_fidelity_scan.py` 分开跑：那个是**单向保真**（源文有的东西译文丢没丢），这个是**反向**（译文有没有源文没说的事）。

```
py -3 .agents/skills/translation-fidelity-scan/scripts/overgeneralization_scan.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --contract .work/<plugin>/contracts/<plugin>.compiled.json --stem <plugin>
```

**核心规则就一条**：译文里出现词条的中文形、源文里没有对应英文锚 → 报。
`中文出现「傲特莫」，原文没有 Altmer` 就是全部。

为什么 gate 抓不到：契约只检查「源文含锚点时译文必须用定形」，**源文不含锚点的行根本不在任何词条的检查范围内**；而中文没有词边界，专名很容易被顺手写上去。失效形态是把源文没说的事补进译文——把种族类别译成具体族（`he's an Elf.` → `他是傲特莫吗？`）、把泛称译成人名。

- 锚点匹配**复用** `translation-quality-gate/scripts/term_match.py` 的 `_anchor_present`（整词优先、专名形容词派生、连字符变体），不写第二套。
- 只取已译行（`Source == Dest` 的行没有「译名」可言）。
- `--term <串>` 只扫锚点含该串的词条；`--stem` 决定报告落点（`.work/<stem>/reports/<stem>-overgeneralization-report.json`）。退出码 0 零候选 / 1 有候选 / 2 用法错误。
- **只报不定罪。** 良性命中占多数，逐类实测：源文分写变体（词表 `Hollyfalls`、源文 `Holly Falls`）、源文作者拼错（词表 `Celamer`、源文 `Celamir`；词表 `Artaeum`、源文 `Arteum`）、源文用同义词（词表 `Labyrinth`、对上 `A Minor Maze`）、承前省译、中文子串误报（`Dremora`→`魔人` 命中 `Vampire Hunter` 的「猎魔人」）。
- **不要在工具里加容差去压这些良性命中。** 实测加过三层容差（连写复合、单复数、子串风险标注）只把 294 降到 260，省下的全是要人工看掉的，裁决价值来自人读候选而不是工具精度。宁可全报，人工分诊。


## 性能

- **全库扫描基线（约 1.5 万行 × 约千条禁形）：2.2s**（回归线 <5s）。
- 禁形 pattern 在 `load_contract` 内**预编译**；禁止存 `re.escape` 字符串后再在循环里 `re.search(pat, ...)`。在 check_units 内现场编译会形成千万级 `re.compile` 调用，全库耗时可达 **293s**（30s 缺陷线近 10 倍）。回归测试 `test_fidelity_scan.py` 含结构断言（pattern 必须为已编译对象）与性能冒烟（250 禁形 × 2500 行 < 3s）。

## 安全边界

只读。不改 XML、译文、词库、契约。候选不自动 FAIL（独立计数），fails 需 Agent 逐条消解。

## 失效机制

禁形必须从契约读取，候选表必须外置；手抄 BANS 会与 `global/terms` 漂移。

任务目标句若缺少句末标点硬约束，不同批次可能出现系统性反向偏差：一批统一保留，另一批统一丢失，规模可达数百行。`QUST:NNAM` 的 `lost` 因此是机械 FAIL。禁形 pattern 必须预编译；循环内编译会把全库扫描从秒级推高到数百秒。
