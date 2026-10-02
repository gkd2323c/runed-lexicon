---
name: translation-quality-gate
description: Deterministic, read-only pre-writeback quality gate for Skyrim mod translation batches. Verifies that completed translation-result JSON satisfies a compiled Translation Contract (term bindings, KEEP list, protected placeholders, simplified-Chinese charset) before the xTranslator XML writer runs, plus a TypeSafe semantic gate (semantic_gate.py) for context-sensitive term applicability, semantic mistranslation, and register judgments the mechanical layer cannot make. Use whenever a completed translation batch must be validated before XML writeback, when terminology regressions like Argonian/Blades/sweetroll need mechanical enforcement, or when a contract/regression change must be checked against the incident-derived synthetic regression corpus. Gate only checks declared unit bindings and never re-derives entity identity, and it never modifies translations.
compatibility: Python 3.10+; standard library only. CHAR001 simplified-Chinese detection uses a vendored zh-cn conversion table (scripts/zh_cn_conv.json) — no third-party dependency, deterministic across environments. The tracked Translation Contract interface is documented in references/contract-schema.md.
metadata:
  version: "0.4.4"
---

> 性能基线（见 `skyrim-tool-dev-rules` §2）：
>
> - MVF1 规模（8555 单元 / 9402 节点，含 XML 预检 + auto-bind）：**0.89s**
> - Druadach 规模（20634 单元 / 849 条全局禁词，auto-bind）：**4.1s**（2026-09-09 优化后；优化前 26.7s）
>
> 回归对照：若同规模耗时超过 5s，先 cProfile 拆账再修复。
>
> 已内建的三层预筛（改动匹配逻辑时不得回退）：
>
> 1. 正则缓存：`_compiled_literal` / `_compiled_word` / `_strip_html_tags_cached`（原实现对每个 unit × 每个 term 重新 `re.compile`，实测 2200 万次）
> 2. 字面预筛：`find_source_hit_spans` / `_iter_word_matches` 在进正则前先用 `in` 检查锚点
> 3. ban 层预筛：`global_ban_issue` 先查 forbidden 是否出现在 dest，再调 `find_global_ban_hits`
> 4. 候选集复用：`run_gate` 把 auto-bind 候选表算一次传进 `_resolve_auto`（R21），不在每行重扫 `term_index`；实测 1411 候选 / 0.52ms 每行，略快于修复前的 0.64ms

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

| Code           | Meaning                         | Fails when                                                                                                                                                                                                                |
| -------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TERM001        | required target missing         | a binding with `required:true` has a dest that does not contain the target per `match`                                                                                                                                    |
| TERM002        | forbidden variant               | dest contains a term's `forbidden` variant                                                                                                                                                                                |
| TERM003        | blocked-suffix variant          | dest contains the target substring followed by a `blocked_suffix` (e.g. 阿尔贡尼亚人)                                                                                                                                     |
| TERM004        | 全局禁用词（项目级）            | dest 含 global_bans 中某条 `forbidden` 形态，且 source 整词出现该条 `english` 锚点（对每个已翻译单元独立执行，无需 unit binding）；detail 携带 reason                                                                     |
| KEEP002        | 全局 KEEP 被翻译                | 一个 global_keep 英文值在 source 整词出现、dest 却 != 该值                                                                                                                                                                |
| KEEP001        | KEEP modified                   | a KEEP-list source value was translated                                                                                                                                                                                   |
| PLACEHOLDER001 | protected token lost            | a protected token present in source is absent/altered in dest（R16 联动 executor 的 `waived_tokens` 背书：Agent 已背书的方括号中文化不拦）                                                                                |
| CHAR001        | non-simplified chars            | vendored zh-cn conversion table: flagged iff convert(dest,'zh-cn') != dest (reports concrete diff chars; filters out context-sensitivity false positives — see R11 fix, v0.1.4; table vendored in-repo since v0.1.6)      |
| TRUNC001       | truncated translation (WARNING) | 源文是完整长句（≥55 字符、不以省略号收尾），译文却以 ……/… 中断且短于源文 62%——疑似翻译时把后半句砍掉带过。**WARNING**：可能是真残缺，也可能是角色“欲言又止/毒舌省略”的合法风格，需 Agent 对照 source 人审消解，不阻断写回 |
| XML001         | identity drift                  | xml_index block's Source/EDID/REC mismatch (pre-writeback, when `--xml` given)                                                                                                                                            |

## Usage

```text
python .agents/skills/translation-quality-gate/scripts/quality_gate.py \
  --result .work/lucifer/round4/lucifer-round4-translation-000.json \
  --result .work/lucifer/round4/lucifer-round4-translation-001.json \
  --contract .work/MVF1FollowerBeta/contracts/MVF1FollowerBeta.esp.compiled.json \
  --keep-list .work/MVF1FollowerBeta/contracts/mvf1-keep.json \
  --xml mods/MVF1FollowerBeta.esp/MVF1FollowerBeta_english_chinese.xml \
  --report .work/MVF1FollowerBeta/reports/mvf1-round4-gate.json
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

### Forbidden variants of unbound terms (R19, v0.2.0)

Auto-bind only covers **no-risk REQUIRED** terms; FORBIDDEN_ONLY and risk-flagged
terms never bind, so before v0.2.0 their `forbidden` lists **silently never ran**
in the auto-bind workflow (live cases: `hour`/「时辰」and `Soul Mine`/「魂石矿」
shipped translated with the banned form despite an explicit ban entry).

Fix: on every translated line, `standalone_forbidden_issues()` additionally
checks all **unbound** terms with a non-empty `forbidden` list, gated by the
term's English anchor appearing in source (`_anchor_present`, same matcher as
the TERM004 exemption path — plural / hyphen tolerant). Bound terms are skipped
(check_unit already reports them with richer evidence). Cross-term target
coverage exemption (`_cross_term_target_covered`) mirrors the check_unit path.
Anchor gating keeps lookalike substrings on unrelated lines from firing
(「克瑞斯」⊂「佛克瑞斯」, 「大法师」泛用). Corpus cases:
`term-contract.fwonly-forbidden-001` / `-anchor-absent-002` / `-clean-003`.

The `selftest_corpus.py` gate path now calls the real `standalone_forbidden_issues`
from `quality_gate` instead of replicating it, so gate and selftest cannot drift.

### 短锚被长专名覆盖时的豁免（R21, v0.4.1）

auto-bind 的整词边界判断有个结构性盲区：**短词条锚点会命中以它为前缀的更长专名**。

事故（TheKalpicAnomaly_GLENMORIL，已复现）：契约里 `the Eyes`（裸形，译“眼线”，指贾'泽尔的
情报网）与 `the Eyes of Hinnom`（实体专名，译“欣嫩之眼”）是两条严格分立的词条。源文
`Is the Eyes of Hinnom here with me now?` 只含全形专名，译文“欣嫩之眼”完全正确，但
`the Eyes` 落在 `the Eyes of Hinnom` 内部、被整词边界判为命中，于是报
`TERM001 WARN: required target 未出现: '眼线'`——纯假阳性，6 行
（2869 / 2870 / 13102 / 14343 / 29180 / 31831）。

规则：**短锚的某次命中若完整落在某个更长的、契约里已登记的专名锚点区间内，该次命中不算
短锚的有效出现**。长锚照常按自己的规则绑定与检查（不放过长锚的缺失）。

| 环节 | 做法 |
| --- | --- |
| 长锚从哪来 | **检查期**从契约 `terms[*].source` 推导（`_resolve_auto` 收集本行所有候选锚点的命中区间）。不硬编码专名列表，也不需要改编译期或重编译契约——已有 `.compiled.json` 直接生效 |
| 豁免判定 | 区间**完整包含**（`term_match.maximal_spans` / `is_shadowed`）：`be-bs > e-s` 且 `bs<=s and e<=be`。相邻、部分重叠、同区间都不豁免 |
| 只作用于 auto-bind | `_resolve_auto` 内部完成。`global_bans`（`_iter_word_matches` / `find_global_ban_hits`）与 `cross_target_covered` 走各自的锚点逻辑，**行为不变**；R19 的 `standalone_forbidden_issues` 也不变 |
| 不作用于显式绑定 | `unit_bindings` 路径（`_resolve`）不经过本规则：显式 binding 是分析 Agent 的语义声明，门禁不二次解释 |

**防漏报（关键）**：按**区间**豁免而非按整行豁免。同一句里裸形与全形并存时，裸形那次不在
任何长锚区间内，仍会绑定、仍会触发 TERM001。同一行两次裸形锚点时，被长专名吞掉的那次豁免、
独立的那次照常。回归用例见 `scripts/test_anchor_shadow.py`
（`test_bare_short_anchor_alongside_long_name_still_triggers` /
`test_two_occurrences_one_shadowed_one_not`）。

**已知漏报边界（不粉饰）**：若契约把一个普通名词短语也登记成更长锚点，而该长锚其实是
长词条的一部分而非独立专名，则长锚区间内的短锚命中会一并豁免。代价是该行不再要求短锚的
target 出现——但长锚自身的 required target 仍会检查，所以不会出现整条无人管的情况。
反之，若短锚在长锚之外另有独立出现则不受影响。`FORBIDDEN_ONLY` / 带 risk_flags 的词条
从不进 auto-bind 候选集，因此不会成为覆盖方。

### Auto-bind substring guard (R6 fix, v0.1.3)

`find_source_hit_spans()` uses word-boundary detection and HTML-tag stripping to prevent
substring false positives reported by R6 (dg04bjornfollower.esp rest audit). It returns
`(start, end)` spans so R21 can test containment against longer anchors;
`find_source_hits()` is the offsets-only wrapper (`term_match.py`, also used directly by
callers that don't need the R21 shadowing).

- **"Rathis" won't match "Athis"** — the char before "Athis" is a word char (`a`)
- **"Imperial" won't match "Ria"** — the char before "Ria" is a word char (`I`); same for "Ritual" containing "Ria"
- **HTML tags are stripped first** — `<font face="Adielle">` won't match "Adielle"` because
  the attribute value is inside a tag and gets removed before matching

### 整句词条只认整句（v0.4.3）

锚点匹配是**字面量匹配**，所以一个**整句词条**（英文以 `.` / `!` / `?` 结尾）会命中更长句子
尾部的同形子串。匹配默认大小写不敏感，`He did.` 于是逐字等于 `...what he did.` 的尾部。

**真实事故（TheKalpicAnomaly_GLENMORIL INFO-480 idx 33438）**：
`It does not rewrite what he did.` → 「它改写不了他做过的事。」译文**完全正确**，却被
`He did.` 词条报 `TERM001: required target 未出现: '他照做了。'`。

**规则**：`is_standalone_sentence(source)` 为真时，命中必须覆盖源文**去掉空白与成对引号后的
全部内容**——即「源文整句就是这条词条」才命中。自动判定，不需要逐词条登记。

**为什么自动判定是安全的**：对 MOD 词表普查，英文以句末标点结尾的只有 4 条
（`He did.` / `There it is.` / `Timing matters.` / `Unknown.`），**全是真整句，
没有 `U.S.` / `Jr.` 这类缩写**。

**逃生口**：缩写型词条写 `substring_match: true` 退回宽松子串匹配
（`find_source_hits` 传 `standalone=False`）。缺省不传，走自动判定。

**与 `case_sensitive` 的关系**：两条规则正交、都进 `find_source_hit_spans` 的关键字参数，
`case_sensitive` 管匹配时的比较方式、`standalone` 管命中范围。**注意别把 `standalone`
的默认值写成布尔值**——写成 `term.get('x') is None` 会给**每个**词条都打开「必须覆盖整句」，
全库普通词条（`command`、`master` 嵌在长句里）会集体失配（`test_case_sensitive` 会红）。
正确写法是「缺省传 `None`，只有逃生口显式传 `False`」。

**与 §5.1 第七类的区别**：`scene` / `identity` 那类是**词表漏登义位**（改词表）；
本类是**匹配精度缺陷**（改工具）。`He did.` 的 4 行整句定形本身完全正确，不该降
`FORBIDDEN_ONLY`——那会丢掉整句锁。**判别信号：若译文正确且源文并非整句，就是匹配问题。**

15 项测试见 `scripts/test_standalone_sentence.py`（含与 `case_sensitive` 的交互护栏）。

### 大小写敏感词条（`case_sensitive`, v0.4.2）

锚点匹配**默认大小写不敏感**（`_compiled_literal` 用 `re.IGNORECASE`）。当**源文大小写本身
承载语义**时，逐词条开 `case_sensitive: true`，锚点只按登记的大小写命中。

**为什么需要这个字段**：契约里原本**没有任何机制能表达大小写敏感**，词条 note 写了
「本条只走大写 C」也拦不住小写触发。两个真实事故（TheKalpicAnomaly_GLENMORIL）：

| 事故 | 词条 | 源文 | 症状 |
| --- | --- | --- | --- |
| 第一例（26848） | `the Eyes`→眼线 | 小写 `the eye` | 普通义误触发专名 REQUIRED |
| 第二例（31517） | `Command`→掌权者 | `a chain of command` | 门禁报 `TERM001: required target 未出现: '掌权者'`，而译文「指挥链」**完全正确** |

第二例的小写 `command` 源行中形分布是 命令 63 : 指挥 34 : 指挥链 5 : 发号施令 2 : 掌权者 1，
主形「掌权者」只对应大写 C 的 3 行。

**两条绕过方案都不采用**：
- 往 `match.accepted`（`additional_accepted`）塞「指挥链/指挥/命令」→ 丢掉大写 C 的主形约束；
- 降 `FORBIDDEN_ONLY` → 把大写 C 的约束一并丢掉。

只有真匹配开关能同时保住两者。**纯增量**：没有该字段的词条行为与修复前逐字节一致；
`_compiled_literal` 的缓存键含该标志，两种模式互不污染；`auto_bind_candidates` 的候选资格
判定不看该标志。字段契约与详细取舍见 `references/contract-schema.md`。

#### ⚠ `case_sensitive` 有两条执行路径，漏一条 = 字段完全失效（v0.4.4）

`case_sensitive` 不是只在 `_resolve_auto` 里生效。判定「这条词条适不适用于本行」的闸门
一共有**两处**，两处都必须透传该字段：

| 路径 | 位置 | 用途 |
| --- | --- | --- |
| 主路径 | `_resolve_auto` → `find_source_hit_spans` | 词条绑定（TERM001 required / TERM002 bound） |
| **独立路径** | `standalone_forbidden_issues` → `_anchor_present` | **未绑定词条的 forbidden 强制**（TERM002 unbound） |

`_anchor_present` 的匹配器 `_iter_word_matches` 硬编码 `re.IGNORECASE`——那是**豁免侧**
的有意宽松（跨条 target 覆盖、派生词、连字符变体）。独立路径原本不传 `case_sensitive`，
于是该字段在第二条路径上**静默失效**。

**真实事故（TheKalpicAnomaly_GLENMORIL idx 9039，`the Serpent`）**：词条已开
`case_sensitive: true`，全库普查证明大小写与中文形一对一——大写 `Serpent` 12 行 → 巨蛇，
小写 `serpent` 仅 9038/9039 两行 → 巨蟒（蜕皮铁皮喻）。修词表、加 note、跑 lint 全过，
**全库门禁照报 `TERM002 the-serpent: forbidden 变体出现: '巨蟒'`**。译文是对的，是工具在说谎。

> **判据**：开 `case_sensitive` 后**必须重跑全库门禁**确认消解。只跑 lint 或逐批门禁
> 看不到它——那只查当批，而这条 9039 属于早已写回的历史批。词表治理的「已修」结论
> 在全库门禁 PASS 之前不成立。
>
> **另一个坑**：同一批两行小写只报一行是正常的。9038 源文是 `the whole serpent`，
> 压根不含 `the Serpent` 锚点。别把「只报一条」误当成漏检。

`_cross_term_target_covered` 的**覆盖方**锚点同样按覆盖方词条自己的标志判定，
避免「不区分大小写地豁免」——只认大写的词条被小写源文豁免掉。

`test_case_sensitive.py` 共 **29 项**（原 20 + 新增 `TestStandaloneForbiddenIssuesHonorsFlag`
6 项复现 9039 事故、`TestCrossTermCoverageHonorsFlag` 3 项），含新旧行为对照护栏。

This prevents auto-bind from flagging units where the English term only appears as a
substring of a longer word or inside markup attributes. These fixes are covered by
the incident-derived synthetic corpus selftest.

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
5. **跨条豁免的锚点容错（R17, v0.1.8）**：跨条 target/forbidden 交叉豁免
   （cross_target_covered）依赖「覆盖方」英文锚点出现在 source；锚点检查原先
   不识别连字符变体写法（作者手写 "High-elf" / "alt-mer"），导致 Altmer/High
   Elf 互搏条的正确译文被误拦（INFO-035 三处误报：源文 High-elf's 译「高精灵」、
   alt-mer 译「傲特莫」均正确）。现在多词锚点允许词间 [\s-]+ 连接、单词锚点
   允许单点插连字符，并保留形容词派生（Altmeri）；仅豁免侧生效，主检查仍为
   严格整词。回归验证：selftest_corpus 64 项 + 正反案例双向测试。

每处 TERM004 命中都带该条目的 `reason`，方便 Agent 判断是误报还是真回归。
收录/维护边界见 GLOSSARY.md §7 与 `global-forbidden-words.json` 顶部 doc。

**门禁是候选发现器，不是判决器**：命中后由编排者逐条核销，不得为了消警改动正确译文。

**已知误报形态：同句并置。** TERM004 按 source 整词锚点触发，无法区分「同一源句中两个不同英文词各有正确译名」。
核销方法：读源文确认 dest 中该中文形对应的英文词；若它对应的不是禁令锚点词本身，则放行并记理由，不改译文。

**亦不得反向依赖门禁**：只有 `global_bans` / term `forbidden` 内的形态会被拦。未收录的坏形态（自拟音译、现实宗教词、现代词）与 `FORBIDDEN_ONLY` 无绑定、未入词表的词，门禁扫不到，须靠独立审查与全库复核兜底。

### TERM002 子串豁免（R18, v0.1.9）

TERM002 路径（`find_forbidden_hits`）原先对 forbidden 只做简单子串检查：当 forbidden
是 target 子串（短形禁令 + 长形 target，如「琼」⊂「琼恩」）时，被长形包含的实例
同样被拦（Artaeum INFO-062，2026-09-10）。修复与 TERM004 的 R12 豁免对齐：forbidden
实例完整落在任一 target 出现区间内时不报；未被覆盖的独立实例仍报——按区间豁免
而非整单元豁免。回归用例见 term-contract-core.json jong-substr-exempt-001/002。

## 全库门禁复核（`scripts/export_full_result.py`）

**`--result` 是批次级的，`units_checked` 不等于全库行数。** 流水线产出的 result 只覆盖该批次的 idx，因此既有 `*-gate-report.json` 的覆盖数通常远小于 canonical 行数（Artaeum 实测 74 / 14968）。**不能据此声明全库通过门禁。**

真实事故（2026-09-12）：批次级 gate 全 PASS 的 canonical，在全库复核时抓出 `圣母`（现实宗教禁令 TERM004）违规——该行属于早已写回的批次，此后从未再经过任何 gate。

收口或声明收敛前，用导出器把 canonical 转成全库 result 再跑一次：

```text
py -3 .agents/skills/translation-quality-gate/scripts/export_full_result.py \
  --xml mods/<plugin>/<plugin>_english_chinese.xml \
  --canonical mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --out _tmp/data/full-result.json

py -3 .agents/skills/translation-quality-gate/scripts/quality_gate.py \
  --result _tmp/data/full-result.json \
  --contract .work/<plugin>/contracts/<plugin>.compiled.json \
  --keep-list .work/<plugin>/contracts/<plugin>.keep.json \
  --xml mods/<plugin>/<plugin>_english_chinese.xml \
  --report .work/<plugin>/reports/<plugin>-gate-report.json
```

口径：`Source == Dest` 的行导出为 `KEEP`（gate 不计入翻译单元），其余为 `TRANSLATED`；`original_dest` 取 Source（本 result 用于校验现状，不参与增量写回）。导出的 result 是审计中间产物，放 `_tmp/`，不入 `mods/` 或 `.work/` 资产目录。

**已知误报（中文无词边界导致的子串命中，需人工核销，不是缺陷）**：禁用词恰好是译文另一词的子串时必然命中。实例：禁用词 `上传`（现代计算机词）命中「她的腿**上传**来一股剧痛」；禁用变体 `游牧民` 命中「灰地诸**游牧民**族」（源文 `Ashlander nomads` 为普通描述）。核销时看命中的上下文词，不只看词表。

## TRUNC001 截断检测（WARNING，需人审）

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

All cases must pass before the gate may be considered safe. The gate path in
`selftest_corpus.py` calls the real gate functions (check_unit,
standalone_forbidden_issues) so selftest and gate cannot drift; current total
69 cases (v0.2.0).

## Semantic gate（`scripts/semantic_gate.py`，v0.3.0）

机械层（quality_gate.py）只管字面匹配：禁形、必备目标、占位符、字符集。它判不了
语境适用性（alias 条目该语境是否成立）、语义错译、语域硬伤。语义层由 TypeSafe
System One 模型承担这三类判断，两层互补：机械层仍全权负责字面检查，语义门不重复。

**判定契约**（阈值常量在脚本内，改动需记录实测理由）：

| 级别        | 条件                                         | 去向           |
| ----------- | -------------------------------------------- | -------------- |
| FAIL        | term_violation ≥ 0.5 或 semantic_error ≥ 0.5 | 阻断写回       |
| WARN 升级   | 0.3 ≤ 上述两项 < 0.5                         | 人工复核队列   |
| WARN 软提示 | issue_type=register 且置信 ≥ 0.5             | 仅供参考，不拦 |

阈值依据：禁用词级违反实测召回 19/19（0.5 门限）；high 级漏判的实测分布 0.23~0.49
落入升级带，由人工兜底，符合成本不对称原则（漏标进人眼，多标无害）。

**判定指令（2026-09-22 扩充）**：`semantic_error` 的 instructions 显式声明以下不算
语义错误——字面隐喻/语类双关/非常规动宾搭配的**忠实直译**（即使读来新奇）；
合理显化扩充至被动句补施事、省略句补出与上文一致的回指、排比复现词的统一；
并附判定方法指令（先逐个在源文定位关键成分的对应表达，弱化否定先还原逻辑，
只有确实找不到对应才判是）。扩充动因：豁免清单暴露的系统性误报（忠实直译非常规
结构被反复拦）。回放实测：26499 类硬拦消除（0.51~0.73 → 0.39），真错召回无损
（旧译 26400=0.71、26501=0.50 仍 FAIL），分数普降；但显化类（26486/26193/26031）
仍越线，且同一译文双跑可跨线抖动（0.60/0.49，判定非确定性），此二者继续由豁免
通道人工裁决。`MOD_REGISTER` 同步声明角色的诙谐/调侃变体属正常语域。

**语境适用性**：注入契约条目时连 `note` 一并注入，模型先判断 alias 注明的适用语境
是否成立，不成立则该条目不适用。alias 条目的 REQUIRED 级绑定由此在语义层生效，
机械层对它们仍只做 R19 锚点禁形拦截。

**匹配语义（必注入）**：契约的 `match.kind` 是 `contains_phrase`——译文包含 required_zh
形式即合规，不要求逐字相等。字段名 `required_zh` 本身易被读成逐字要求，故注入时
必须同时给 `match_semantics` 说明。漏掉它会系统性误报：实测 `Whiterun`→「白漫城」
（官方全称，机械层 PASS）被判 TERM_VIOLATION，term=0.49~0.61 跨过 HARD=0.5。

| 注入方式             | term 三次实测      | 判定               |
| -------------------- | ------------------ | ------------------ |
| 只给 required_zh     | 0.51 / 0.49 / 0.51 | FAIL（贴边界抖动） |
| 加 match_semantics   | 0.17 / 0.16 / 0.15 | ok                 |
| 只改字段名 target_zh | 0.56 / 0.57 / 0.56 | FAIL（无关变量）   |

修复后误报消除且不漏真违规（同一句）：正确全称 0.04 ok、正确省称 0.03 ok、
未译 0.96 FAIL、错译「雪漫」0.41 FAIL、音译「怀特伦」0.95 FAIL。

**四态判定（状态必须如实；2026-09-23 起整体为参考层）**：

| verdict | 含义 | rc | 后续 |
| --- | --- | --- | --- |
| `PASS` | 本批全部条目已判定且无 FAIL | 0 | 通过 |
| `FAIL` | 存在硬拦截项 | 1 | 参考信号：明细照出，verify 不传导阻塞 |
| `PARTIAL` | 有条目未判定（调用失败）——本批语义层**未完成** | 1 | 参考信号：未判定项交复核，不阻塞 |
| `UNCHECKED` | 无 key 或未配置连接，本批语义层**未检查** | 0 | 参考缺失，不阻主线，不得当作已过语义门 |

**参考层定位（2026-09-23 用户裁决）**：语义门的 verdict、分数与 FAIL/UNJUDGED
明细全部照出并入验收报告，但 `verify_subagent_batch` 对 `FAIL`/`PARTIAL`/
`UNKNOWN` 一律只记参考 warning，**不再阻塞 consume/写回主线**；语义把关由
主会话通读 + 独立审查线承担（机械层 gate 的 FAIL 仍是硬拦截，不受此影响）。

`PARTIAL` 与 `UNCHECKED` 是如实上报，不是缺陷：语义门不追求 100% 自动判定，
未判定项交复核即可。网络抖动在**条目级**做有限退避重试（`ask()` 内 `RETRIES=3`，
捕获族覆盖 URLError/Timeout/JSONDecodeError/ConnectionError/OSError——断连族
RemoteDisconnected 不被 URLError 包装，漏捕时零重试直接穿透，实测导致每批稳定
数条 UNJUDGED 卡死验收链；条目级重试远轻于整批全量重跑），重试后仍失败的条目
如实记 PARTIAL 交复核；关键是**不得把「没查到」记成「查过没问题」**。

**连接与凭据配置（`semantic-gate.config.json`，项目根）**：URL、模型与密钥统一
由配置文件管理，不硬编码：

```json
{"api_url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest",
 "api_key": "<secret>",
 "mod_register": "<本项目/角色的语域语境描述（对话题材、正常语域边界、口语节奏判定规则、专名约束）>"}
```

- `mod_register` 是注入 payload 的语域语境（原硬编码于脚本的项目级提示词，
  2026-09-23 抽出）：随项目与角色变化（如本仓库的西格瓦格言式语域约定），
  不硬编码在工具内；缺失时回退通用中性句「英译中翻译质量审查（通用文本，
  无特定角色语域约束；专名按术语契约）」。

- 加载优先级：配置文件 > 环境变量（`api_key` 缺省回退 `TYPESAFE_API_KEY`；
  配置文件路径可用 `SEMANTIC_GATE_CONFIG` 环境变量覆盖）。
- 文件含活密钥，已加入 `.gitignore`，**永不提交**；任务卡仍不携带任何凭据。
- 缺 `api_url`/`model`（或无配置且无 key）时输出 `verdict=UNCHECKED`、rc=0，
  verify 报告中该批 `checked=false`——参考层缺失同样不阻主线。

**豁免通道（人工复核裁决的落盘，v0.4.0）**：概率模型对部分句式存在稳定误报
（实测 INFO-481 的 23580/23602 两轮改写分数钉在 0.57 不动，均为语境正确的边界
句），逐句改写对抗没有收敛；整批 `--no-semantic` 又等于放弃其余条目的语义检查。
豁免通道把人工裁决变成机器可读的状态：

| 参数 | 语义 |
| --- | --- |
| `--waive "<idx>:<理由>"` | 登记豁免（可重复）。从 `--result` 取该 idx 当前译文算 sha256（前 16 位）落盘，登记后直接退出不调 API |
| `--revoke "<idx>"` | 撤销豁免 |
| `--waivers <path>` | 豁免文件路径；缺省自动探测 `.work/<plugin>/contracts/semgate-waivers.json` |

判定语义（防豁免变成永久白名单）：

- FAIL 项命中**有效豁免**（idx 存在且译文哈希与登记时一致）→ 降为 `WAIVED`，不拦截，
  原始判定保留在 record `verdicts`（基线哨兵比对不受影响），豁免理由与登记时间留痕
  入报告的 `waived` 数组和 stdout。
- **译文一改，豁免自动失效**（`WAIVER_STALE`），该条重新被拦，需人工重裁。旧裁决
  不会掩盖新编辑引入的新错误。
- 豁免只作用于语义层 FAIL；机械 gate 的 TERM/KEEP 拦截不受豁免影响（字面违反走
  词表裁决链修正，不走语义豁免）。

登记时机：人工复核确认某条为误报之后（不是改写失败之前）；理由必填且须写清语境
证据（指代链/双关拆解等），豁免文件是审计对象。

**用法**：

```text
python .agents/skills/translation-quality-gate/scripts/semantic_gate.py \
  --result <translation.json> --xml <source.xml> --contract <compiled.json> \
  [--batch <BID>] [--report <report.json>] \
  [--waivers <path>] [--waive "<idx>:<理由>"] [--revoke "<idx>"]
```

`verify_subagent_batch.py` 默认挂接语义门（`--no-semantic` 关闭）；其 verdict/warnings
并入验收报告的 `gate.semantic_gate` 段，且 FAIL/PARTIAL/UNKNOWN 只记参考 warning、
不阻塞验收主线（参考层定位见上）。result 支持 dict 平铺与 `translations[]`
两种形态，条目自带 source 时优先使用，缺失才回查 --xml。
`verify_subagent_batch.py` 与 `consume_batch.py` 均有 `--waivers <path>` 透传；缺省
沿用自动探测路径，同 MOD 内常规验收无需显式传参。

**回归验证**：豁免通道的端到端桩测试脚本（合成 result + 桩 judge，两场景：命中降级
/ 改译失效）保留在 `.agents/skills/translation-quality-gate/scripts/test_waiver_channel.py`，
改语义门判定链后必跑。

## Semantic gate 基线（`scripts/semgate_baseline.py`，哨兵层）

语义门判定来自概率模型，上游模型版本或阈值一改，判定行为可能静默位移；机械 gate
有 corpus 回归兜底（gate 与 selftest 不可漂移），语义层此前没有对应机制。本脚本补
这一层：**不判译文对错，只判同一输入的判定轨迹是否与上次一致**。

三个动作：

| 动作         | 输入                                         | 输出                                                                            |
| ------------ | -------------------------------------------- | ------------------------------------------------------------------------------- |
| `--build`    | semgate report + translation.json + contract | 基线 JSON（每条含 source/translation/完整判定向量/契约 hits/级别）              |
| `--check`    | 基线 + contract                              | FLIP（跨级）/ DRIFT（超容差未跨级）/ OK 三类事件 + diff report                  |
| `--repeat N` | 同上                                         | 每条重复跑 N 次记 spread；spread 超容差标记 `fragile`（该判定落在模型能力边界） |

**归因闸门**：基线记录建库时的 `contract_sha256`。`--check` 时契约与基线不一致会
在输出顶部打 `CONTRACT_CHANGED`，因为此时位移可能来自契约变更而非模型漂移——
先用契约差异解释，再怀疑模型。

**版本指针与哨兵的分工**：`MODEL = 'jev-latest'` 是服务标准指针，上游改进自动生效，
无需追版本号。配套代价是判定行为可能随上游变化，所以用 `--check` 做定期哨兵：
指针给灵活性，哨兵给可见性，两者配对使用。

（`--check` 的 diff 报告里记录当次实测的判定向量；若要逐次对比版本行为，
可在 `--note` 里自行标注。）

**fragile 的生产用途**：重复跑自身抖动超容差的 case，说明模型对该判断本就没把握。
生产判定若落在同类区域，应升级人工而非信任硬判。

**用法**：

```text
# 建库
python .agents/skills/translation-quality-gate/scripts/semgate_baseline.py --build \
  --report <batch-semgate-report.json> --translations <batch/translation.json> \
  --contract <compiled.json> --out <baseline.json> [--note "..."]

# 比对（需 TYPESAFE_API_KEY）
python .agents/skills/translation-quality-gate/scripts/semgate_baseline.py --check \
  --baseline <baseline.json> --contract <compiled.json> \
  [--tolerance 0.15] [--repeat 3] [--out <diff-report.json>]
```

退出码：0 全 OK，1 存在 FLIP/DRIFT，2 用法错误，3 无 API key。

## Safety boundaries

- Do not re-derive entity identity from source text. If a term lacks a binding for a unit, that unit is not forced — even if the source mentions the term.
- Do not auto-fix. If the gate finds a FAIL, return it to the translation JSON layer.
- Do not run on raw XML as a substitute for the writer's own post-write validation; this gate is pre-writeback.
- Terms with `risk_flags` (alias / knowledge_boundary / spoiler) must never receive automatic or global bindings; only explicit per-unit bindings. Their `forbidden` lists are still enforced per R19 (anchor-gated, independent of bindings) — target checks stay binding-only.
