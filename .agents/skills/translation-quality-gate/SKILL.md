---
name: translation-quality-gate
description: Deterministic, read-only pre-writeback quality gate for Skyrim mod translation batches. Verifies that completed translation-result JSON satisfies a compiled Translation Contract (term bindings, KEEP list, protected placeholders, simplified-Chinese charset) before the xTranslator XML writer runs. Use whenever a completed translation batch must be validated before XML writeback, when terminology regressions like Argonian/Blades/sweetroll need mechanical enforcement, or when a contract/regression change must be checked against the incident-derived synthetic regression corpus. Gate only checks declared unit bindings and never re-derives entity identity, and it never modifies translations.
compatibility: Python 3.10+; standard library only. CHAR001 simplified-Chinese detection uses a vendored zh-cn conversion table (scripts/zh_cn_conv.json) — no third-party dependency, deterministic across environments. The tracked Translation Contract interface is documented in references/contract-schema.md.
metadata:
  version: "0.1.6"
---

> 性能基线（见 `skyrim-tool-dev-rules` §2）：
> - MVF1 规模（8555 单元 / 9402 节点，含 XML 预检 + auto-bind）：**0.89s**
> - Druadach 规模（20634 单元 / 849 条全局禁词，auto-bind）：**4.1s**（2026-09-09 优化后；优化前 26.7s）
>
> 回归对照：若同规模耗时超过 5s，先 cProfile 拆账再修复。
>
> 已内建的三层预筛（改动匹配逻辑时不得回退）：
> 1. 正则缓存：`_compiled_literal` / `_compiled_word` / `_strip_html_tags_cached`（原实现对每个 unit × 每个 term 重新 `re.compile`，实测 2200 万次）
> 2. 字面预筛：`find_source_hits` / `_iter_word_matches` 在进正则前先用 `in` 检查锚点
> 3. ban 层预筛：`global_ban_issue` 先查 forbidden 是否出现在 dest，再调 `find_global_ban_hits`

# Translation Quality Gate

Deterministic, read-only gate that runs **before** `xtranslator-xml-writer`. It answers one question:

> Does this completed translation batch satisfy every mechanically checkable translation constraint?

It performs no semantic judgment, re-derives no entity identity from source text, and **never modifies translations**. If a constraint is violated, the batch must be fixed in the translation-result JSON by an Agent, then re-gated. The Gate never silently rewrites.

## When to use

- Any completed translation batch (executor-style JSON with `translations[]`) before XML writeback.
- Whenever a contract or term decision changes and existing results must be re-checked.
- After model/prompt/local-translator changes, to confirm no mechanical regression (run the corpus selftest).

## Hard principles

1. **Checks bindings only.** The contract's `unit_bindings` declare which term each unit is bound to. The Gate never scans source text to guess whether "Blades" means the organization.
2. **Never modify.** Gate emits PASS / FAIL / WARNING with locations. Fixes happen in translation JSON by an Agent; the Gate is not a second translator.
3. **Detection ≠ conversion.** CHAR001 reports offending characters; it does not convert them.
4. **Zero hard errors to pass.** Any FAIL blocks writeback.

## Environment

Standard library only. CHAR001 simplified-Chinese detection uses a vendored
zh-cn conversion table (`scripts/zh_cn_conv.json`, derived from zhconv 1.4.3
zhcdict.json, GPLv2+) — no pip install, no optional dependency, identical
behavior on every machine:

```text
python .agents/skills/translation-quality-gate/scripts/quality_gate.py ...
python .agents/skills/translation-quality-gate/scripts/selftest_corpus.py
```

## Inputs

1. **Translation result JSON** — executor schema with `translations[]`. Each unit has `translation_unit_id`, `source`, `translation` (or `original_dest`), optional `rec`/`edid`.
2. **Compiled contract JSON** — schema v1 (see `references/contract-schema.md`):
   - `terms`: term_id → Term Definition (target / forbidden / match / enforcement / decision_status / risk_flags).
   - `unit_bindings`: per-unit binding list (term_id, source_span, required, required_target).
   - `global_bans` / `global_keep`（可选）: project-wide ban list + KEEP list, embedded by term-contract-compiler from `global-forbidden-words.json` when `--global-bans` is passed. Gate enforces them on every translated line (see TERM004 / KEEP002).
   - A lightweight gate-case form (`bindings` + `terms` inline, plus optional `keep_list` / `protected_tokens` / `global_bans`) is accepted for corpus selftests.

## Checks

| Code | Meaning | Fails when |
| --- | --- | --- |
| TERM001 | required target missing | a binding with `required:true` has a dest that does not contain the target per `match` |
| TERM002 | forbidden variant | dest contains a term's `forbidden` variant |
| TERM003 | blocked-suffix variant | dest contains the target substring followed by a `blocked_suffix` (e.g. 阿尔贡尼亚人) |
| TERM004 | 全局禁用词（项目级） | dest 含 global_bans 中某条 `forbidden` 形态，且 source 整词出现该条 `english` 锚点（对每个已翻译单元独立执行，无需 unit binding）；detail 携带 reason |
| KEEP002 | 全局 KEEP 被翻译 | 一个 global_keep 英文值在 source 整词出现、dest 却 != 该值 |
| KEEP001 | KEEP modified | a KEEP-list source value was translated |
| PLACEHOLDER001 | protected token lost | a protected token present in source is absent/altered in dest（R16 联动 executor 的 `waived_tokens` 背书：Agent 已背书的方括号中文化不拦） |
| CHAR001 | non-simplified chars | vendored zh-cn conversion table: flagged iff convert(dest,'zh-cn') != dest (reports concrete diff chars; filters out context-sensitivity false positives — see R11 fix, v0.1.4; table vendored in-repo since v0.1.6) |
| TRUNC001 | truncated translation (WARNING) | 源文是完整长句（≥55 字符、不以省略号收尾），译文却以 ……/… 中断且短于源文 62%——疑似翻译时把后半句砍掉带过。**WARNING**：可能是真残缺，也可能是角色“欲言又止/毒舌省略”的合法风格，需 Agent 对照 source 人审消解，不阻断写回 |
| XML001 | identity drift | xml_index block's Source/EDID/REC mismatch (pre-writeback, when `--xml` given) |

## Usage

```text
python .agents/skills/translation-quality-gate/scripts/quality_gate.py \
  --result .work/round4/lucifer-round4-translation-000.json \
  --result .work/round4/lucifer-round4-translation-001.json \
  --contract .work/term-contracts/MVF1FollowerBeta.esp.compiled.json \
  --keep-list .work/term-contracts/mvf1-keep.json \
  --xml mods/MVF1FollowerBeta.esp/MVF1FollowerBeta_english_chinese.xml \
  --report .work/qa-reports/mvf1-round4-gate.json
```

Exit code: 0 = PASS (no FAIL), 1 = FAIL, 2 = usage error. A `--report` JSON carries full per-unit issues for review routing.

> `--result` is repeatable: one call can gate multiple result files in a single run; `units_checked` is the union of all files' units.

## Auto-bind review mode

When explicit `unit_bindings` are not yet authored (e.g. auditing an existing
completed batch), add `--auto-bind`:

```text
... --auto-bind --auto-strict mvf1.sweetroll
```

- Auto-binds every **no-risk REQUIRED** term (enforcement=REQUIRED, no risk_flags)
  whose English source form appears in the source text. The compiler already
  downgrades ambiguous / knowledge-boundary terms (Companions, The Blades,
  Falmer, Jarl...) to FORBIDDEN_ONLY, so the auto-bind set is safe by construction.
- Missing-target findings (`TERM001`) are downgraded to **WARNING** review
  candidates, because legal ellipsis (回指 / 同胞称谓) can legitimately omit the
  target. Terms listed in `--auto-strict` stay FAIL (reserved for must-appear
  unambiguous terms).
- Forbidden-variant hits (`TERM002`) always stay FAIL.
- Units where `dest == source` (KEEP / untranslated technical strings) are skipped.
- Exit code stays 0 when only WARNINGs are present; review candidates are printed
  and written to `--report` for human triage.

### Auto-bind substring guard (R6 fix, v0.1.3)

`find_source_hits()` uses word-boundary detection and HTML-tag stripping to prevent
substring false positives reported by R6 (dg04bjornfollower.esp rest audit):

- **"Rathis" won't match "Athis"** — the char before "Athis" is a word char (`a`)
- **"Imperial" won't match "Ria"** — the char before "Ria" is a word char (`I`); same for "Ritual" containing "Ria"
- **HTML tags are stripped first** — `<font face="Adielle">` won't match "Adielle"` because
  the attribute value is inside a tag and gets removed before matching

This prevents auto-bind from flagging units where the English term only appears as a
substring of a longer word or inside markup attributes. These fixes are covered by
the incident-derived synthetic corpus selftest.

## TRUNC001 截断检测（WARNING，需人审）

### 全局禁用词（TERM004）设计说明

`global_bans` 是**独立于 unit_bindings 的全局扫描**：它保护官方名词完整性，
无论某行是否带 binding 都执行（MVF1/SB1 事故证明：大部分错误形态出现在无
binding 的单元，局部 TERM002 查不到）。判定带四道防护，避免误伤：

1. **英文侧整词锚点**：dest 出现坏形态还不够，source 必须整词出现对应英文
   （`blades` 诗歌普通名词不会触发 `Blades` 组织禁令；`PreSolstheim` 嵌入形式
   不触发）。复数与 `'s` 形式仍命中（Argonians / Delphine's）。
2. **不扫描未翻译行**：dest == source（KEEP / 技术行）跳过。
3. **与 TERM002 去重**：同一坏形态若同时被某已绑定 term 的 forbidden 命中，
   保留局部 TERM002（证据更丰富），不重复报 TERM004。
4. **canonical 覆盖豁免（R12, v0.1.5；R13, v0.1.7 补残缺形态）**：forbidden 中某形态是 target 的
   子串（月名条的标准形态：裸词 forbidden + 带月 target，如"晨星"⊂"晨星月"）
   时，其在 dest 中的出现若完整落在任一 target 出现区间内，视为 canonical
   形态的组成部分，不报；未被 target 覆盖的实例仍报——同单元混有裸译缺月
   （"晨星"无"月"）与正确"晨星月"时，裸错误照样拦得住。按区间豁免而非
   整单元豁免，避免误放真错误。修复 Druadach-book 24 条 TERM004 中 20 条
   月名 substring 假 FAIL（2026-09-08），回归用例见 corpus global-bans.json
   010-013。R13：forbidden 命中后紧跟 target 剩余部分（允许间隔 "..."/"…"/
   空白）同样豁免——source 残缺形态（"Morning Star..." 残缺日期）的忠实译文
   "晨星...月" 不是裸译错误（Druadach-book 8530，回归用例 014-015）。

每处 TERM004 命中都带该条目的 `reason`，方便 Agent 判断是误报还是真回归。
收录/维护边界见 GLOSSARY.md §7 与 `global-forbidden-words.json` 顶部 doc。

TRUNC001 检出“译文把后半句吞了”的候选：源文是完整长句、译文却以省略号中断且明显偏短。
背景（SB1 事故 2026-09-04）：一批译文为省事把难译的后半句砍掉用……带过，34 条真残缺
混在 207 条启发式命中里，noun-audit 查不出（它只查名词）。

判定条件（全部满足才报）：
- source 长度 ≥ 55（排除 Gah.../Neeth... 类短叹）；
- source 不以省略号收尾（原文自带欲言又止则跳过）；
- dest 非空、dest != source（排 KEEP/未译）；
- dest 以 …… 或 … 结尾；
- len(dest) < len(source) × 0.62。

富文本先 strip 标签再判定（R15, v0.1.7）：此前见 `<` 就整条跳过，导致 BOOK
这类 HTML 信件/书籍的 11 条截断无一条被检出（Druadach-book 2026-09-08）。
标签内省略号 strip 后自然消失不误报；纯标签行 strip 后为空仍被排除。
长度与结尾判定一律用去标签后文本。回归用例见 source-fidelity-core.json
trunc-html-006/007（另：selftest 此前从未调用 truncation_issue，已补上）。

**定位为 WARNING 而非 FAIL**：中英长度差天然存在，且“毒舌省略”是合法风格（尼思这类角色），
机械判定会误伤。每个 TRUNC001 需 Agent 对照完整 source 判断是真残缺（补全）还是风格省略（忽略）。
修复 SB1 34 条后全量 gate TRUNC001 = 0，且无对合法译文的误报。

## CHAR001 确定性实现与误报过滤（v0.1.4 R11；v0.1.6 去 zhconv 环境依赖）

CHAR001 检测非简体字符。v0.1.6 起使用**随仓 vendored 转换表** `scripts/zh_cn_conv.json`
（zhconv 1.4.3 zhcdict.json 的 zh2Hans + zh2CN 合并表，GPLv2+，11,906 条目）加
`quality_gate.py` 内的最长匹配转换副本，语义与 `zhconv.convert(dest, 'zh-cn')`
逐字节一致（全 CJK 单字符域 + 短语探针验证）。不再 import zhconv，无"环境是否
装了 zhconv"的降级分支——检测能力不再随环境漂移（t_2f368a4c）。

历史背景（R11 fix, v0.1.4）：zhconv 存在**上下文敏感性**：当 么 (U+4E48，
什么/怎么/多么 的标准简体字) 后接 正 或 子 等字时，词组级转换会将其变为 幺/什幺。
例如 convert('什么正路', 'zh-cn') 返回 "什幺正路"。这是转换表词组覆盖的固有行为，
不是真正的繁简问题。

修复方式（保留）：`charset_issue()` 在收集 diff 时，对每个 source char `a` 额外
检查其单字符形态在 vendored 表中是否不变（`_CONV_DATA.get(a, a) == a`）。
若不变，说明它已是简体，diff 是词组级转换所致，跳过该 diff。若所有 diff 都是
误报，`charset_issue()` 返回空列表（不报 CHAR001）。

词组保留与误报的边界（vendored 表自动继承）：瞭 (U+77AD) 单字符会转为 了，
但表内含恒等词组条目 `瞭望 → 瞭望`，故 瞭望/瞭望塔 中的 瞭 不会被检出（官方
语料与既有 canonical XML 均用 瞭望）；而裸 瞭 仍会被检出。真繁体字符（歡→欢、
場→场、「→"）无论上下文一律检出。回归用例见 corpus `charset-core.json`
（t_2f368a4c 新增，不改动既有 corpus 语义）。

## Selftest against the incident-derived synthetic corpus

The corpus at `corpus/translation-regression/cases/gate/` encodes failure mechanisms distilled from confirmed project incidents as semantic-equivalent synthetic fixtures (bad translation must FAIL, golden must PASS). Private MOD text and record identifiers are not required for this selftest. After any change to the gate, contract schema, or term matching:

```text
python .agents/skills/translation-quality-gate/scripts/selftest_corpus.py
```

All cases must pass before the gate may be considered safe.

## Safety boundaries

- Do not re-derive entity identity from source text. If a term lacks a binding for a unit, that unit is not forced — even if the source mentions the term.
- Do not auto-fix. If the gate finds a FAIL, return it to the translation JSON layer.
- Do not run on raw XML as a substitute for the writer's own post-write validation; this gate is pre-writeback.
- Terms with `risk_flags` (alias / knowledge_boundary / spoiler) must never receive automatic or global bindings; only explicit per-unit bindings.
