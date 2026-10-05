---
name: translation-review-tools
description: "Review and revise translation batch artefacts without hand-writing one-off scripts: read a batch's source/translation pairs (read_batch.py), compile a batch context into a per-idx terminology digest (term_digest.py), search the translated XML with REC/EDID/batch attribution (query.py), generate fix lists from review reports (make_fixes_from_report.py), apply correction lists to a batch's map.json + translation.json with an optional canonical patch (apply_fixes.py), normalize non-simplified characters (normalize_charset.py), run anti-hallucination probes (hallucination_probe.py), and slice source-vs-destination readouts (longtext_readout.py). Use during translation acceptance (三查验收通读), terminology adjudication, cross-batch consistency checks, dispatch preparation (pre-chewing context.json), anti-hallucination review of long texts, review-report reconciliation (报告→修正集), and post-review corrections. The standard toolkit replacing ad-hoc throwaway scripts. Read-only except apply_fixes.py."
compatibility: Requires Python 3.10+. Uses only the Python standard library.
metadata:
  version: "0.13.0"
---

# Translation Review Tools

翻译评审与修订环节的标准工具集，替代四类反复手写的一次性脚本：

| 脚本 | 职责 | 替代的临时代码 |
| --- | --- | --- |
| `read_batch.py` | 读一个批次的源译对照（验收通读标配） | 各种 `readout_*.py` |
| `check_batch_evidence.py` | 重跑 map.json 里**若存在**的 `evidence` 字段（可选字段，派单不再索取） | 人工逐条重跑 `lookup_batch` |
| `term_digest.py` | 把批次 context.json 编译成一屏派单摘要（每 idx 的 MOD/官方术语命中 + 语境锚点） | 各种 `dump_*terms*.py` |
| `query.py` | 全库搜词（源/译两侧）、idx 定位、批次归属 | 各种 `check_*.py` |
| `make_fixes_from_report.py` | 审查报告 → 修正集（fix map）：schema 解析、expected_current 回填、驳回/特裁/同源副本对齐 | 各种 `make-review*-fixes.py` |
| `apply_fixes.py` | 修正清单联动同步 map + result + canonical patch（支持跨批仅出 patch 模式、`--subs-file` 多组替换声明） | 各种 `apply_fixes_*.py` / `fix_*.py` |
| `normalize_charset.py` | 非简体字符检测与规范化（CHAR001 同源表），输出 apply_fixes 兼容的修正清单 | 各种手工「」→“”替换脚本 |
| `hallucination_probe.py` | 长文本抗幻觉机械探针（段落/长度比/数字语义/性别代词） | 各种 `probe*.py` |
| `longtext_readout.py` | 生成长文本源译对照分片读本（供语义精读） | 各种 `readout*.py` / `slice_*.py` |
| `adjudication_pack.py` | 裁决包生成（扫描候选 + XML/对话主题/terms 证据预切）与 verdict 机械校验、修正计划生成 | 各种 `build_*pack*.py` / 裁决对账脚本 |
| `canon_hints.py` | 从 canonical 提取目标批次中已定形的整句/句级翻译，输出派单提示 | 各种临时提取 hints 脚本 |
| `form_census.py` | 同锚词形普查：全库分布 vs 批内分布并排 + **背离告警**（批内主形 ≠ 全库主形时告警），裁决收敛方向前必跑 | 各种 `form_probe*.py` / `probe_*corpus*.py` / 手数词频 |
| `artifact_scan.py` | 审查工件与流程元语言泄漏扫描（HIT/WEAK 两级机制） | 各种一次性泄漏排查脚本 |
| `formula_scan.py` | 公式句多译法扫描与修正集生成（按标点切句，对位比对首尾句分裂） | 各种公式句对账脚本 |
| `check_review_proposed.py` | 收口前核 review-record 的 `proposed` 是否真是可落盘译文（防「改法只写在 rationale、字段留原样」的静默空转）；坏 JSON 记为「视为未审」 | 每次收口手写 `proposed == 现译` 比对 |
| `repair_json_escapes.py` | 机械修 review-record 里「正则片段反斜杠只转义了一半」造成的非法 JSON | 坏记录直接整份重派，白扔已写的 findings |
| `dump_review_findings.py` | 审查记录 → 裁决视图（每条 finding 连同源文 / canonical 现译 / proposed / rationale 一屏打出，`--full` 附未报项与风险注） | 每轮手拼「读报告 → 查 canonical 取现值」转录 |
| `append_adjudication_note.py` | 总档定点追加 notes + 刷新 `canonical_at_adjudication` / `totals.verification`（同 topic 替换不堆重复），落盘后回读断言 | 几十万字符的总档手工 Edit |

## make_fixes_from_report.py

审查报告 → 修正集（fix map）一键生成，替代每轮核销都手写一遍的
「读报告 → 读 canonical 取现值 → 拼 fix map」转录脚本：

```text
py -3 .../make_fixes_from_report.py \
  --report .work/<plugin>/notes/review-INFO-XXX.json \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --out .work/<plugin>/maps/<plugin>-fix-map-review-XXX.json \
  [--drop <idx,...>] [--override ov.json] [--align-dups] [--include <idx,...>]

# 多批修正：直接产出 close_round 需要的分组形态
py -3 .../make_fixes_from_report.py --report <review.json> --xml <translated.xml> \
  --batches <BID-A> <BID-B> --batches-dir .work/<plugin>/batches \
  --out .work/<plugin>/maps/<plugin>-fix-map.json
```

- 兼容两种报告 schema：`findings[]`（新）与 `issues[]`（历史）；`xml_index`/`idx` 均可。
- `expected_current` 从 canonical 当场读取（CAS 基准）；现值已等于裁决值的条目列为 no-op 剔除。
- `--drop`：驳回项（如官方实证现状正确）；`--override`：`{idx: 新值}` 特裁（部分采纳/语境重造）。
- `--override` **可引入报告 findings 之外的 idx**（`introduce_overrides`）。跨批同锚词收敛的常态是
  「reviewer 只点名了一侧，另一侧由主会话按全库多数裁定」：例 `institution` 全库 81:4 悬殊，
  reviewer 按批内少数形建议统一，主会话反向裁定要改的正是 reviewer 没提到的那几行。
  显式 `--drop` 仍优先于 override（驳回不被特裁复活）；`--include` 同样生效；越界记
  `WARN override idx 越界`；现值已同记 no-op。stdout 单列「特裁新增（报告外，reviewer 未点名）」便于复核。
- `--align-dups`：把裁决值广播到全库同源句的所有副本（同源分裂统一）；不传时只检测并打印分歧清单供人工判断。
- 只读生成，不写批次文件；输出直接喂 `apply_fixes.py --fixes`。

**`--batches` / `--batches-dir`（多批修正的分组通道）**：默认输出扁平 `{idx: {new}}`，
而 `close_round.py` 的多批修正要的是分组 `{batch: {idx: {new}}}`（扁平形态只配一个 `--batch`）。
分组以往靠手转，而失效点全在「idx 归属哪个批次」——映射写错时分组顶层键会退化成 idx 本身，
`close_round` 随后报「裸格式 fixes 只能配一个 --batch」，报错点离病因隔了两步。
传 `--batches` 后归属由各批 `index.txt` 机械判定：

- 归属判不出（idx 不属任何声明批次、批次缺 `index.txt`、两批共享同一 idx）一律**失败退出且不落盘**——
  漏声明批次的修正会静默消失，机械拦截优于事后对账发现。
- 输出顶层键顺序跟随 `--batches` 声明顺序，并打印 `close_round --batches <键>` 供直接复制。
- 越界 idx 仍按既有口径记 `WARN idx 越界` 跳过，不算孤儿。
- 修正集条目保持 `expected_current`；`close_round` 的 `apply_fixes` 以此做 CAS 校验。

核销标准链：`make_fixes_from_report.py`（生成）→ `apply_fixes.py --fixes ... --translated-xml ... --patch-out ...`（联动批次 + 出 patch）→ `xtranslator-xml-writer` 的 `write_translations.py --patch`（写回 canonical）。
已写回批的修正直接走 `translation-batch-ops` 的 `close_round.py`（它自带分组消费），无需手串后半程。

## check_review_proposed.py

**收口前的强制闸门**，紧接在 `make_fixes_from_report.py` 之前跑：

```text
py -3 .../check_review_proposed.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --reports .work/<plugin>/reports/<plugin>-<BID>-review-record.json [...]

# 确认某条确实「本无缺陷、proposed 有意留空」时才放宽
py -3 .../check_review_proposed.py --xml <canonical.xml> --reports <rec.json> --allow-same
```

存在不可用 finding 时退出码 1，阻断收口。

### 为什么需要它

**失效形态**：审查记录中的多条 finding 把改法写进 `rationale` 散文，`proposed` 却逐字保留
canonical 现译。这类记录喂给 `make_fixes_from_report.py` 会**静默空转**：不报错、不改字节，
`close_round` 仍打印 `PASS`，而本该修的缺陷完全未改。随后 `prune_close_patch` 还会把它们
报成「已就位」，进一步掩盖空转。

### 判定的三种不可用形态

| kind | 含义 | 处置 |
| --- | --- | --- |
| `EMPTY` | `proposed` 为空 / 非字符串 / 全空白 | 主会话按 rationale 重写 |
| `SAME_AS_NOW` | 与 canonical 现译逐字相同（含仅差首尾空白） | 改法只写在散文里 → 重写；或确认本无缺陷则从 findings 剔除 |
| `NOT_IN_BATCH` | `xml_index` 不是标量 int（字符串 / 浮点 / 列表） | 修字段类型，否则收口时归属与 CAS 都会失效 |

`--allow-same` 只放宽 `SAME_AS_NOW`，**不放宽** `EMPTY` 与 `NOT_IN_BATCH`——后两者是记录
本身的缺陷，不是裁决选择。

### 它是**收口前**闸门，收口后会误报（这是设计如此）

收口应用之后重跑本脚本，**已修好的 finding 会重新报成 `SAME_AS_NOW`**，因为 canonical
此时正好等于它的 `proposed`。同一组 finding 在 `close_round` 应用前全部可用，应用后会全部变为 `SAME_AS_NOW`。

这不是误报，是「已就位」，判据在别处：

| 现象 | 含义 | 处置 |
| --- | --- | --- |
| 收口**前**报 `SAME_AS_NOW` | 改法只写在散文里，字段仍为现译 | 必须处置，阻断收口 |
| 收口**后**报 `SAME_AS_NOW` | 该条已被应用，canonical == proposed | 已就位，无需处置 |

收口后要确认「哪些是已就位」，用 `translation-batch-ops` 的 `prune_close_patch.py`
（它按批报「已就位 N 条 / 老值 M 条」），不要用本脚本的退出码。

### 边界

- 只读输入（canonical + review-record），不写盘。
- 判据是「`proposed` 能否直接落盘」，**不判 proposed 的语义对错**——语义裁决归主会话。
  一份 `proposed` 全可用的记录仍可能整条方向错，必须照常逐条回源文判读。
- 回归用例 `test_check_review_proposed.py`（13 例 unittest，覆盖上述三形态 + `--allow-same` 边界 +
  **记录无法解析**的报告路径）。
- 记录本身坏掉时（`json.loads` 失败、文件不存在）**不算「本批没有缺陷」**，闸门会把该批标成
  「视为未审」并退出 1。理由：坏 JSON 的批次如果被当成已审完，欠账统计就会凭空少一批。

### 坏 JSON 的机械修复：`repair_json_escapes.py`

子代理写 JSON 的高频坏点是**正则片段里的反斜杠只转义了一半**：

```json
"checks_run": ["正则 ^\\[\\d+\\] 命中 46"]
```

前两处 `\\` 合法，末尾 `\]` 只有**一个**反斜杠——JSON 里 `\]` 非法，`json.loads` 直接抛
`Invalid \escape`，整份记录作废。失效可使一批数条 findings 全部无法读取。

```text
py -3 .../repair_json_escapes.py --path <record.json> --dry-run
py -3 .../repair_json_escapes.py --path <record.json>
```

- **逐字符扫描，不用正则替换**：正则会把 `\\` 里的第二个反斜杠误判成非法转义（已踩过）。
- 只把「后面不跟合法转义字符」的反斜杠补成 `\\`，其余字节一律不动。
- 修完**必须能 `json.loads` 才落盘**，并回读断言内容一致；仍失败则直接抛、不写。
- 修完仍要用 `check_review_proposed.py --repair-hint` 复检 `proposed` 可用性——
  JSON 合法不等于 findings 合格。


## adjudication_pack.py

裁决层并行化的确定性底座：把「候选 → 裁决 → 修正」从主会话串行流程拆成可 fan-out
的三段。与 noun-consistency-scan（发现）和 apply_fixes.py（写回）衔接，本身只做
证据预切与机械校验，不做任何语义判断。

### build：裁决证据包生成

```text
py -3 .../adjudication_pack.py build \
  --pool _tmp/data/noun-scan/pool-a.json \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --terms mods/<plugin>/terms.json \
  --dialogue-context .work/<plugin>/context/<plugin>-dialogue-context.json \
  --out _tmp/data/adj-pack --cap 25
```

每候选一块（`pack-NNN.txt`，子代理读）+ 全量 JSON（`pack-NNN.json`，消费基准）：

- 每个形态附全部行的 idx/REC/EDID；>8 行的形态压缩尾部（idx 全量在 JSON 侧）。
- `--dialogue-context` 为 INFO/DIAL 行补**对话主题**（xTranslator 导出的 EDID 常为
  FormID，主题是同一场景判断的唯一可靠证据；FormID 无主题时显式标注无家族信息）。
- `--terms` 标注候选英文锚在 terms.json 的登记状态与目标形。
- `--cap` 每片候选数（默认 25），对齐子代理单实例体量上限；多片出 manifest.json。

### 裁决协议（子代理三选一）

| verdict | 含义 | 约束 |
| --- | --- | --- |
| `unify` | 真漂移，统一 | 必须给 `target`，且 target ∈ 该候选**已出现形态集合**（禁止发明第三形态） |
| `legitimate-split` | 语域/分层合法（守卫 vs 卫兵、湖 vs 堡） | 必须给非空 `reason`（分层依据） |
| `different-object` | 同源不同物 | 必须给非空 `reason`（EDID/主题归属依据） |

### consume：verdict 机械校验 + 修正计划

```text
py -3 .../adjudication_pack.py consume \
  --pack _tmp/data/adj-pack/<pack-id>.json \
  --verdicts _tmp/data/verdicts.json \
  --plan-out _tmp/data/adj-plan.json
```

机械校验（任一不过即 exit 1，零产出）：键集合 == pack 候选集合；verdict 枚举合法；
unify.target ∈ 已出现形态集合；split 类 reason 非空。通过后生成 plan：

- `plan[]`: `{kid, source, find, replace, idx[], reason}`，主会话终审后逐条调
  `apply_fixes.py --find/--replace/--idx-list`（跨批模式，从 canonical 读现值）。
- `splits[]`: legitimate-split / different-object 清单，**主会话必须全量复读**
  （唯一需要 lore 判断的部分），unify 抽审 10~20%。
- **子串重叠防御**：find 是其他形态的子串时（如「岩湖」命中「岩湖堡」），plan 项
  带 `warn: substring-overlap`，禁用 --find/--replace，改整句 new。

写回仍走既有链路（单一写者原则）：consume 不碰 map/result/canonical。

## hallucination_probe.py

长文本**抗幻觉**机械探针：报可疑信号，不判语义（候选发现器）。只读。

```text
py -3 .agents/skills/translation-review-tools/scripts/hallucination_probe.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml
py -3 .../hallucination_probe.py --xml <same> --json _tmp/data/probe.json
```

四类显性信号：

| 探针 | 含义 |
| --- | --- |
| P1 段落少于源文 | 多段文本被合段/吞段（合段属 LOW，未必丢内容） |
| P2 译文长度比 | 去标签后中文/英文 < 0.20 疑截断、> 0.62 疑增译 |
| P3 数字语义缺失 | 源文数字在译文中既无原形也无中文数词对应 |
| P4 性别代词强冲突 | 源文单一性别、译文相反性别 |

**已知误报（需人工核销，不是缺陷）**：

- **P2 只在长文本上成立**。短口语行（INFO）中文天然比英文短得多，`--min-len 120`
  时实测 40 条 P2 全为正常压缩（「Meanwhile I suggest you keep at your practicing.」
  →「平日多加练习。」）。**默认门槛 300 即为此故**；调低门槛时须核 P2 全部输出。
- **P3**：中文习惯表达已由 `cn_number_variants` 覆盖（`2 meters`→「两米」、
  `80%`→「八成」）；仍无法穷举的俗语会报。
- **P1**：合段排版会报，但内容通常完整。
- **P4**：作者笔误会报（源文 `He's been here` 实指女性角色）。探针报的是
  「不一致」，**归因需人判**；角色性别应有词表或正文自证支撑后再改。
- **不做专名编造检测**：实测信噪比极差，数百条候选可全部误报。词表连写形
  `soulgem` 与源文分词 `soul gem`、规则性补全 `the Eye`→「玛格纳斯之眼」
  都会触发。该维度交语义精读。

## longtext_readout.py

生成长文本源译对照分片，供抗幻觉语义精读（分派给子代理或自己读）。

```text
py -3 .agents/skills/translation-review-tools/scripts/longtext_readout.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --out-dir _tmp/data/longtext-shards --min-src 300 --cap 17000
```

分片规则：同一 EDID 不拆开（保持一本书/一条任务线的上下文完整）；组间按组内
最长行降序（高风险先审）；按源文字符量贪心装片。输出 `longtext-N.txt` 与
`manifest.json`。

**按 `[idx]` 头分块，不按空行**：BOOK:DESC / MESG:DESC 的 Dest 含 `<font>` 标记与
空行，按空行分块会把一条切成残片，阅读者看似整段漏译。失效可在千条规模分片中造成
数十条 Dst 缺失，且只有回查 canonical 才能确认是切片缺陷。

## check_batch_evidence.py

重跑并核验 `map.json` 里**若存在**的 `evidence` 字段。**`evidence` 是可选字段，派单卡不再索取它**——本脚本只用来重跑已经写在那里的结构化声明，不是验收要求。

```text
py -3 .agents/skills/translation-review-tools/scripts/check_batch_evidence.py \
  --batch-dir .work/<plugin>/batches/<BID> [--dictionary dictionary] [--json]
```

### 它现在的定位

它曾被当作派单要求，理由是「`notes` 纯中文零拉丁，物理上装不下证据，子代理要表现有据可依只剩散文一条路，而散文会被编出来」。实测确实抓出了真伪证（`神龛` 是编造：库内零命中、词表明文 forbidden），但那是在**给不可靠的举报人建核验机制**——为了防一个字段里的编造，先要求每个执行者都填这个字段，等于把编造的机会发给每一个子代理，然后靠事后抽查去抓。

现行做法是反过来：**让产物里根本没有可伪证的声明**。`notes` 只写决策与不确定，禁写世界事实断言（见 `subagent-ops` §4 契约）；世界事实由机械侧产出（契约、门禁、term-digest、词典查询、各类扫描）。执行者拿不准官方译形时按词表译、用 `confidence` 表达不确定，而不是补一句「库内已定形」。

因此本脚本降级为**遗留字段的抽查器**：老批次或子代理自愿填写时，重跑一遍确认没穿；`evidence` 缺失是正常状态，不是缺陷。

判据三条：`lookup` 必填（否则无法重跑，该条跳过并报）；声明命中数与实测差 > 2 报错（词典会更新，个位数差异不算伪证）；`zh_forms` 与官方实际译形**完全无重合**报错（子串/单复数差异不算）。回归 `test_check_batch_evidence.py`（8 项，用假词典不依赖真实词典内容）。

**只报不一致，不定罪。** 字典更新导致的漂移也会报出来，让人看一眼即可。

## read_batch.py

读单个批次的源文/译文对照。数据源**按新鲜度**取（`map.json` 与 `translation.json` 都在时比 mtime），退化到另一份 + `--xml`；实际用了哪份打在 stderr。

**为什么不只判存在**：`translation.json` 是上一轮 `consume_batch` 的产物，子代理随后写的 `map.json` 更新。只判「存在」会让陈旧的那份遮蔽刚交卷的那份——失效形态是读到一堆 `PENDING` 空占位，当成交卷读，据此做的判断全错。取 `map.json` 时若没给 `--xml`，报错会说明是「translation.json 比它旧，不采信」而不是含糊的「缺源文」。回归见 `test_read_batch_freshness.py`（6 项）。

```text
py -3 .agents/skills/translation-review-tools/scripts/read_batch.py \
  --stem <plugin> --batch <BID>

# 只看待决条目 / 输出到文件
py -3 .../read_batch.py --stem <plugin> --batch <BID> --status WAITING,KEEP
py -3 .../read_batch.py --stem <plugin> --batch <BID> --out _tmp/data/readout.txt

### term-digest 新鲜度门（默认开启）

产出读本**之前**先检查同批 `term-digest.md` 是否带 `== 读法 ==` 图例；缺图例就从
`context.json` 自动重生成，并在 stdout 打一行 `digest: 重生成 …`。派审查实例**必须先跑
本脚本**，就是为了拿到新鲜料——手工「记得先重生成」记一次漏一次。

```text
# 只想要读本、不想触发重生成（例如做固定输入对照）
py -3 .../read_batch.py --stem <plugin> --batch <BID> --no-digest-check
```

为什么必须有这道门：全库普查可发现**数百批** digest 缺少图例。缺图例的摘要会把
`machinery` / `virtue` / `cause` / `curse` / `vessel` 等词条的分域判据藏回词表，导致整轮术语漂移。
检查按需自愈，不做全库批量重写：暂不推进的批次无需产生文件 churn，复审时再自动补上。

判定只看「图例在不在」，不比对正文：正文会随词表变动而变，逐字比对会让这道门退化成
「永远陈旧」的噪声源。边缘情况：

| 情况 | 行为 |
| --- | --- |
| digest 新鲜 | 不动文件 |
| digest 缺图例 / 不存在，`context.json` 在 | 重生成并覆盖，打印提示行 |
| digest 缺图例但无 `context.json` | 警告到 stderr，**不覆盖原文件**（旧料比没料强），读本照常产出 |
| `context.json` 损坏 | 警告到 stderr（`digest WARN [failed]`），不抛异常、不阻断读本 |
| 重生成结果仍无图例 | 判 `failed` 且不写回，避免制造下一轮「不新鲜」 |

读本产出**永不**因 digest 不新鲜而失败：审查者宁可看带警告的读本，也不要拿不到读本。

## term_digest.py

把批次 `context.json` 编译成紧凑派单摘要。**派单前必跑**：原始 context.json 单批约 95–105 KB，子代理一次 `read` 读不下，会绕道 shell 查词典树，把预算烧在机械查证上。

```text
py -3 .../term_digest.py --context .work/<plugin>/batches/<BID>/context.json \
  --out .work/<plugin>/batches/<BID>/term-digest.txt
```

每 idx 三行（含空命中标注 `-`）：

```text
[idx] REC | source | dup=N
    MOD: English→中文 (STATUS); ...
    OFF: English=中文[REC]; ...
    CTX: quest=<edid>; cat=<category>; topic=<topic>   # 仅 dialogue_context.status=matched
```

MOD = 项目契约命中（`mod_terms_hits`）；OFF = 官方词典命中（`official_dictionary_hits`）；CTX = 对话语境锚点（仅对话条目）。空命中照列 `-`，让执行者知道「没有」是真的，不是漏看。派单任务卡的输入段指向本摘要，不指向原始 context.json。纯只读，除 `--out` 指定的文件外不碰任何文件。

### 大小写警示（专名判别的第一信号）

命中项若**词条首字母大写、而本行源文里出现的是全小写形态**，会在该词条后标注 `⚠源文小写，疑普通名词`：

```text
[<xml_index>] INFO:NAM1 | …the companions whose behavior remains consistent…
    MOD: The Companions→战友团 (CONFIRMED) ⚠源文小写，疑普通名词
    OFF: Familiar=使魔[NPC_:FULL] ⚠源文小写，疑普通名词
```

判据只看大小写（词条去冠词后取词干，两侧都加词边界），**不猜语义**。失效形态是译者看到
「官方词条 `Familiar=使魔[NPC_:FULL]`」后，把 `familiar shapes`（熟悉的形状）误绑成召唤法术；
`The Companions` 与 `the companions`、`Confidence` 与 `confidence` 也会产生同类误绑。
**大小写是专名判别的第一信号，必须在派单材料里显式给出**。

同源同批已收敛的形态：`engineering` 一律「工程」；`history` 主形「历史」，「来历」是既有的「出身」义形。

### 判据型 note（主形不是无条件替换指令）

MOD 命中项若其词条 `note` 带**语义判据**，会在该词条后附 `〔判据:…〕`：

```text
[<xml_index>] INFO:NAM1 | Someone wants the frame narrow here.
    MOD: frame→框子 (CONFIRMED)〔判据:影像语境指画面框；与抽象义「框架」（全库 6 处）分域。…〕
```

摘要**开头**固定带一段 `== 读法 ==` 图例，明说 `en→zh` 只是主形记录、不是无条件替换指令——译者不必回查本脚本源码才知道标记含义。

**为什么必须带出**：`_fmt_mod()` 若只输出 `en→zh (STATUS)` 而丢掉 note，译者就看不到分域判据。
例如 `machinery→机械` 会盖掉「实物装置＝机械／抽象体系＝机制」，`vessel`／`frame`／`change`
也会出现少量漂移，“神龛”“教团”这类坏形只能等门禁 FAIL 才发现。
**判据不到译者眼前就是缺陷，不是可选增强。**

**为什么不全量带出**：上千词条都有 note，但绝大多数是取证记录，与「这一行该怎么判」无关。
全量带出会把摘要撑成 context.json 的复刻，重新制造「一次读不完」的问题。所以只挑带判据标记的：
`分域`／`两义`／`判据`／`绑定`／`勿强并`／`不作同锚合并`／`分立`／`不是无条件`／`勿混`／`不是同一`／`勿作`。
判据**内联**在 MOD 行尾，不另起行，因此只增加少量图例行。

**⚠ 截断的例外：note 点名本行 `xml_index` 时不截断**。note 压掉换行后默认截断到 180 字并加省略号，
但这可能切掉**针对本行的逐行预裁**，让译者看不到既有裁决并自行选择错误义项。
**判据：note 里出现本行 `xml_index`（独立数字），说明它含针对本行的裁决；这类 note 全文带出。**
实现是 `_judge_note(h, idx)` 收到本行 index 时把上限提到 `_JUDGE_MAX_ROW_CITED`（4000，实际等于不截）；
未点名的行仍按 180 字截断，防摘要膨胀回 context 复刻。数字匹配用 `(?<!\d)<idx>(?!\d)`，
避免把更长或更短数字误判成本行索引。

`--no-judge-notes` 可关掉判据与图例（默认开）。**关掉等于把分域判据藏回词表**，只在固定输入对照实验中使用。

`global_bans` 不由本工具带出：它属于编译契约（`global-forbidden-words.json`）而非词条，不在 `context.json` 的 `terminology` 块里，且由门禁以 TERM004 机械执行；把全局禁词表复制进每份摘要只会制造噪声。**本工具的职责边界是「让译者看见词条级语义判据」，不是「复述门禁」。**

### 法术名统一表附带（`--spell-registry`，默认自动探测）

摘要末尾附 MOD 专属法术名统一表（`.work/<plugin>/notes/*-spell-name-registry.md`，自动探测；也可 `--spell-registry` 显式指定）：

```text
== 法术名统一表（83 项；* = 本批源文命中） ==
  * Throwdoll = 击倒术
    Charge = 充能术
```

**为何必须带**：此表的法术名已写回 canonical，但不进 `terms.json`（普通词作 REQUIRED 会过度绑定，如 Charge/Ghost/Diamond）。摘要不带时，子代理看不到已定形，只能凭音感自造。失效形态是同批多个法术名与 canonical 既有译形偏离，直到验收才发现。`*` 标出本批源文命中项，便于优先对齐。

## query.py

四个模式（互斥）：

```text
# 按源文搜（大小写不敏感，字面匹配）
py -3 .../query.py --xml mods/<plugin>/<plugin>_english_chinese_translated.xml --src Thrall

# 按译文搜
py -3 .../query.py --xml <same> --dst 斯罗尔

# 单行 + 邻域（--context K）
py -3 .../query.py --xml <same> --idx <xml_index> --context 4

# 批次归属（扫批次计划）
py -3 .../query.py --locate <xml_index>

# 正则 / 大小写敏感 / 提高上限
py -3 .../query.py --xml <same> --src "Thrall|斯罗尔" --regex --limit 0

# 批量锚点核查：一个英文锚一行，报每个锚的全库中文 Dest 形态分布
py -3 .../query.py --xml <same> --anchors anchors.txt
```

`--anchors` 是**验收时核专名的标配**（取代手写 scan_review_terms.py 类脚本）：每行一个英文锚，以整词（含复数 s）匹配，报该锚在全库已译行里出现的每个中文 Dest 及其行号；≥2 种形态标 `<<< 分裂`。与 `noun-consistency-scan` A pool 互补：本例不排除对话行（INFO/DIAL），且以词为锚而非整句 Source，因此能抓到 A pool 因按整句分组而漏掉的**句内专名分裂**（真实例：Elise 在 QUST 作「伊莉丝」、INFO 作「艾莉丝」；Pål 在 NPC/DIAL 作「波尔」、INFO 作「保尔」）。形态按整句 Dest 计，故同锚不同句会算作不同形态，分裂计数为**候选**偏高，需人工按语境裁决。

输出每行：`[idx] REC EDID 状态` + SRC/DST 两行；`--locate` 输出 `[idx] -> <batch-id> (<plan 路径>)`。

**同源句核对用 `--src` 而非 `--anchors`**：查「同一英文句在不同批次是否同译」时，用 `--src "<源文片段>"` 直接列出全部出现位置与各自译文；`--anchors` 是按词锚统计形态分布，两者用途不同。

## lookup_terms.py

在 `dictionary/` 官方词典中查证一批英文术语的既有中文译名，输出「源文 -> 译文」对照并标注来源文件。

```text
py -3 .agents/skills/translation-review-tools/scripts/lookup_terms.py Vyrthur "Ice Wraith" Dremora

# 区分同形词：只保留源文同时匹配该正则的命中
py -3 .../lookup_terms.py Seeker --context "Apocrypha|Mora|Black Book"
# 放宽/收紧源文长度上限（默认 170），每词列出条数（默认 4）
py -3 .../lookup_terms.py Dwarven --max-len 70 --per-term 6
```

用途：子代理把原版既有名词标 MEDIUM / REVIEW 悬置时，编排者批量查证后入词表（见 `skyrim-translation-craft` §8）。**无命中时显式输出「(无官方见证)」**，以便区分「官方没有这个词」与「脚本没查到」——前者需要编排者自行定名并标 PROVISIONAL。

只读工具，不修改任何文件。

## form_census.py

裁决「同锚分裂该往哪个形收敛」时，**方向必须按全库多数，不能按批内多数**。这是硬纪律：审查实例只看得到自己批内的分布，方向判错会连带成百行返工。本工具把全库分布与批内分布并排打出，并在两者背离时直接告警，让「批内多数 ≠ 全库多数」成为可见事实而不是需要靠人记得的纪律。

```text
# 基本普查（形态从契约词条自动取；无词条则全部计入未归类）
py -3 .../form_census.py --stem <S> --anchor institution --anchor rescue

# 显式形态 + 批内分布 + 背离告警
py -3 .../form_census.py --stem <S> --anchor hierarchy --forms 等级 等级体系 层级 \
  --batches-dir .work/<S>/batches

# 落盘 JSON 供后续程序消费
py -3 .../form_census.py --stem <S> --anchor rescue --out .work/<S>/reports/<S>-form-census.json
```

每个锚输出四段：① 各形态计数 + **全库主形** + 次形与占比；② `未归类` 桶（词表未登记的新形，或副词/形容词变体，`--show-sites` 逐条打 idx 与译形）；③ 批内分布（给了 `--batches-dir` 才有）；④ **背离告警**——某批次（≥2 行）主形与全库主形不同即告警。

- 形态匹配一律**长度降序**，「等级体系」不会被短形「等级」吃掉；`--forms` 直传与契约自动取两条路径行为一致。
- **词表无该锚时自动切开集发现**（`--auto-forms N`，默认 8，`0` 关闭）：要裁决的词往往还没进词表——这恰恰是最需要查分布的情形，若一律落「未归类」工具等于不可用。本模式统计该锚各行译文的 CJK n-gram（长度 2–4）频次，同一锚的行高度平行（同一说话人、同一语域），概念自身的形态通常排在前列。**去重作用域是整行**（同一行里「同意」出现三次只计一次），避免长句刷榜。**只产候选不下结论**——高频项混着「变成」「一个」这类通用词，由主会话挑选后用 `--forms` 复跑拿准确分布。实测 `consent`→同意 92%、`custody`→看管 100%、`rescue`→营救 68%、`purpose`→目的 74%、`obvious`→显而易见 71%，均与人工裁决一致。
- 锚按**正则**匹配、大小写不敏感，不做词形还原：查某个词请把变体用 `|` 串起来（`confiden(ce|t)`）。
- 源 XML 与 canonical 条数不一致直接退出 2，不给半截统计。
- 批内分布依赖 `batches/*/index.txt`；缺失则该段整体跳过并说明，**不猜归属**。
- **本工具不下结论**。「哪个形该保留」是语义裁决，归主会话；工具只负责把分布与背离如实摆出来。实跑 4.3 万行 / 单锚约 1.5s。

## apply_fixes.py

把修正清单一次应用到 `map.json` + `translation.json`，并可生成带 CAS 守卫的 canonical patch。

fixes JSON：

```json
{
  "<idx-a>": "你不该来这儿",
  "<idx-b>": {
    "new": "……你在耍我吧？",
    "expected_current": "……你在耍我把？",
    "notes": "错字修正",
    "status": "TRANSLATED"
  },
  "<idx-c>": {
    "status": "TRANSLATED",
    "notes": "仅改 status（无 new）：REVIEW → TRANSLATED，译文保留现值"
  }
}
```

**`new` 可选**：缺省时只改 status/notes/confidence（常见需求：把 REVIEW/KEEP 状态转成 TRANSLATED 而译文不动），避免为此手写一次性脚本。**改译文的字段名恒为 `new`**：传 `translation` 会在入口直接报错拒绝。若该字段被静默忽略，patch 会全部显示“已就位”、表面成功却不改译文。已写回批的修正收口优先走 `translation-batch-ops` 的 `close_round.py` 一条龙（自动 patch 写回 + readout 重生成 + 同源对账 + new 断言）。

**整句覆盖守卫**：`new` 是**整句替换**语义，不是词形替换。失效形态：词形修正意图（offshoot 分支→旁支）把裸词当 `new` 传入，数处完整译文会被整句覆盖为裸词并写回 canonical，只能靠下游 semgate 0.92+ FAIL 发现。入口机械拦截两型：**G1** 现值为完整句（句末标点、≥12字）而 `new` 无句末标点且不足现值一半；**G2** ≥2 键 `new` 同值且无句末标点（同一裸词批量替换多条完整句）。词形/子串修正一律用 `--find/--replace` 或 `--subs-file` 通道；确认是真短句替换时在 fix 加 `"allow_collapse": true` 显式放行。守卫覆盖手工 `--fixes` 路径（批次模式取批次文件现值，跨批模式取 XML Dest）。

**`--find/--replace`（同型多行替换）**：不值得为 5 条同型修正写全量 new JSON 时用；从各 idx 现值做子串替换并生成 fixes，走同一条写盘链路。`--idx-list` 限定行；无可替换时 no-op 且 exit 0。多行文本用 `--find-file` / `--replace-file`（见下「跨批模式」）。

**`--subs-file`（多组替换声明，替代手写 fix-*.py）**：一个批次涉及多组词对、或多批次各需替换时，用声明式 JSON 一次执行（之前每轮手写 `fix-XXX.py` 的主要原因）：

```text
py -3 .../apply_fixes.py --stem <plugin> --subs-file _tmp/data/subs.json
```

```json
[
  {"find": "鬼婆乌鸦", "replace": "乌鸦鬼婆", "batches": ["<BID-A>", "<BID-B>"]},
  {"find": "路径", "replace": "路线", "batches": ["<BID-B>"], "idx_list": "<idx-a>,<idx-b>"},
  {"find": "旧形", "replace": "新形", "canonical": true, "idx_list": "<idx-c>"}
]
```

- 批内项（`batches`）读 map.json 当场生成 fix（含 CAS 基准）；**同批次多组替换自动叠加**（第二轮基于第一轮结果继续），`status`/`notes` 可选覆盖。
- canonical 项（`canonical: true`）从译文 XML 读现值并生成 patch（需 `--translated-xml` / `--patch-out`），默认同步批次文件（`--no-sync-batches` 关闭）。
- 多批次先全部 dry-run 校验、后统一写盘（近似整体原子）；任一错误则零写盘。
- 这类替换统一使用声明式 JSON，不写一次性 `fix-XXX.py` 脚本。

**跨批模式自动同步批次文件**：省略 `--batch` 时（跨批修正/patch 生成），默认把新值
同步进批次文件（map.json / translation.json）——canonical 修正不回写批次会致审查视图
从旧值生成、再 fill 时旧 map 覆盖修正。`--no-sync-batches` 可关闭。同步为“先全量计算、
后统一写盘”，不会产生半成品。

**CAS 双处校验（重要）**：expected_current 同时校验 `map.json` 与 `translation.json`，任何一个不匹配即中止且**两处都不写盘**。因此手工改过 map 数值后须同步 translation.json（正常流程由 `fill_translations.py` 保证两者一致）。

### 跨批模式（省略 `--batch`）

抗幻觉审查这类修订常跨多个批次，逐个绑定 batch 目录很繁琐。**patch 生成本就不依赖 batch**（它只读 canonical 并对 idx 做 CAS），故 `--batch` 可省略：

```text
py -3 .../apply_fixes.py --stem <plugin> \
  --fixes _tmp/data/longtext-fix.json \
  --translated-xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --patch-out .work/<plugin>/maps/<plugin>-fix-patch.json
```

该模式只产出 canonical patch，不同步 map.json / translation.json（那两个文件本身就是批次的）。控制台会显式标 `map.json: 0（不存在）`，不是错误。

**跨批字符串替换**：同样省略 `--batch`，`--find/--replace` 改从 canonical 读现值（而非 map.json）：

```text
py -3 .../apply_fixes.py --stem <plugin> \
  --find "旧词" --replace "新词" --idx-list "<idx-a>,<idx-b>" \
  --translated-xml <canonical> --patch-out <patch>
```

**多行替换用 `--find-file` / `--replace-file`**：诗节重写、段落改写这类含换行的替换，命令行传参不可靠，从 UTF-8 文件读最稳（文件末尾换行会自动剔除）。find 串不存在时 no-op 且 exit 0、不产出 patch（不得伪造）。

**同一 idx 的多次替换会串联**：对已存在 patch 的 idx，下次替换以 patch 中的译文为基准继续改（而不是从 canonical 旧值重新出发）。因此一条目需改两处不同文字时，分两次调用即可，不会互相覆盖；`expected_dest` 始终取 canonical 现值（CAS 基准不变）。

**方括号标记自动豁免**：译文把方括号内容中文化后（`[END OF SEASON 1]`→「[第一季终]」），writer 的占位符校验会报 `protected token mismatch`。patch 生成时**自动对比源译的方括号标记并写入 `waived_tokens`**，无需手工声明（手工补声明漏过两次）。两侧都保留的标记（如 `[pagebreak]`）不入豁免；显式提供 `waived_tokens` 时以调用方为准。

```text
py -3 .../apply_fixes.py --stem <plugin> --batch <BID> --fixes _tmp/data/fix.json

# 同型多行子串替换（不必写全量 new）：全批替换，或用 --idx-list 限定
py -3 .../apply_fixes.py --stem <plugin> --batch <BID> --find "旧词" --replace "新词"
py -3 .../apply_fixes.py --stem <plugin> --batch <BID> --find "「" --replace "“" --idx-list "<idx-a>,<idx-b>"

# 同时生成 canonical patch（expected_dest 自动取 XML 当前 Dest）
py -3 .../apply_fixes.py --stem <plugin> --batch <BID> --fixes fix.json \
  --translated-xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --patch-out .work/<plugin>/maps/<plugin>-fix-patch.json
```

行为细则：

- **先全量校验、后统一写盘**：任何一条 `expected_current` 不符即整体中止（exit 2），不写任何文件——这是防错批、防手滑的机械保证。
- **幂等**：译文已等于 new 时跳过（不报错）；只携带 notes/status/confidence 的修正仍会应用字段。
- **KEEP 自动转换**：把 KEEP 条目改译时，status 自动转 `TRANSLATED`（避免写回时被 KEEP 语义把译文还原成英文），并打印 WARN。
- **patch 生成**：`--patch-out` 已存在时默认合并（同 idx 以本次值为准并计数提示）；XML 当前 Dest 已等于 new 的条目跳过（已就位）。
- **修正单编制纪律（验收侧）**：单内必须覆盖该批全部 `REVIEW` 条目（裁决「保留」也用无 new 的 status 项转正，漏转会在写回时被 writer 拦截 `non-final status 'REVIEW'`）；`--batch` 为单批工具，idx 先对照该批 map/index 核实归属、按批拆单（凭记忆归档会整单被拒）。
- 不触 MOD XML 本身；patch 由 `xtranslator-xml-writer` 消费写回。

## canon_hints.py

批次派单前的既有定形提取工具：读取批次 `index.txt`，将待译源句与当前已写回的 canonical XML 对比，
提取已定形的整句与句级译文，写入 Markdown 提示供任务卡消费（使译者直接继承既有译文，防跨批同源分裂）：

```text
py -3 .../canon_hints.py --stem <stem> --batch <BID>
py -3 .../canon_hints.py --index <index.txt> --source-xml <src.xml> --canonical <canon.xml> --out <out.md>
```

- 默认落盘至 `.work/<stem>/batches/<BID>/canon-hints.md`（成为批次标准准备资产）。
- 按标点切分中英文单句并对位，支持整句与句级两级提示。

## artifact_scan.py

审查工件与流程元语言泄漏扫描器（只读）：在 canonical 译文 XML 的 `Dest` 中排查备注、裁决说明、
文件名、英文残留、流程术语等非正规译文内容：

```text
py -3 .../artifact_scan.py --stem <stem> [--strict] [--show-weak] [--limit N]
py -3 .../artifact_scan.py --xml <canonical.xml> [--strict]
```

- **HIT（硬伤）**：确定属于流程泄漏（如「主会话裁决」「统一为一形」「DICTIONARY.md」「直角引号」「括注编号」等），计入退出码（非零退出）。
- **WEAK（疑似）**：可能属于合法正文但带有元语言词，默认只做统计，`--strict` 时才计入失败。

## formula_scan.py

公式句多译法扫描与修正集生成器（只读 XML，生成 fix-map）：专门检测「固定收束句 + 变动评注」这一规则
扫描盲区。按标点严格切句，对位比对多句行的首句与尾句在全库是否出现多种不同译法：

```text
py -3 .../formula_scan.py --stem <stem> [--out <map.json>] [--dry-run]
py -3 .../formula_scan.py --xml <canonical.xml> [--dry-run]
```

- 组内中文形 >1 种即判定为分裂；多数形定形，平票取最早 idx 形。
- 产出扁平 fix-map，修正只替换对应首句/尾句位置，保留变动评注。

## normalize_charset.py

将译文中的非简体字符（繁体字形、台港标点如「」、台港用词）规范化为简体形式：与 `translation-quality-gate` 的 CHAR001 同源——运行期动态加载同一 vendored 表与转换算法（零漂移），并保留其 R11 误报过滤（已简化字符的上下文误报不替换，如「么」不得被转为「幺」）。

```text
# 检查批次（有差异 exit 1，无差异 exit 0）
py -3 .agents/skills/translation-review-tools/scripts/normalize_charset.py --result .work/<plugin>/batches/<BID>/translation.json

# 全库扫描 canonical（只读）
py -3 .agents/skills/translation-review-tools/scripts/normalize_charset.py --xml mods/<plugin>/<plugin>_english_chinese_translated.xml

# 生成修正清单 → apply_fixes 应用（未写回批次）
py -3 .../normalize_charset.py --result <translation.json> --fixes-out _tmp/data/charset-fixes.json
py -3 .../apply_fixes.py --stem <plugin> --batch <BID> --fixes _tmp/data/charset-fixes.json

# 已写回内容的修正走跨批 patch（`--fixes` + `--translated-xml` + `--patch-out`）
```

边界：长度不等的转换（罕见）不自动替换、列 manual review 输出；不修改任何输入文件，只写 `--fixes-out` 指定的路径。

## dump_review_findings.py

把一份 review-record 拉成**裁决视图**——每条 finding 一个块，把「源文 / canonical 现译 /
proposed / rationale / 置信度」并排打出。这是主会话逐条裁决时的实际阅读面：

```text
py -3 .../dump_review_findings.py --report .work/<plugin>/reports/<plugin>-<BID>-review-record.json \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml

py -3 .../dump_review_findings.py --report <rec.json> --xml <canonical.xml> --only <idx-a>,<idx-b>   # 只看几条
py -3 .../dump_review_findings.py --report <rec.json> --xml <canonical.xml> --full              # 附未报项 / 风险注 / validation
```

- 现译取自 `--xml` 指向的 **canonical**（不是批次 `map.json`）：裁决要对着真正生效的那份判。
- `--only` 用于已裁决一部分、只想复核剩余几条时省上下文。
- 只读，不写盘。

## append_adjudication_note.py

总档（`<plugin>-review-adjudication.json`，几十万字符）**禁止手工 Edit**——整份读改写。
本脚本只做三件事，不碰 `decisions` / `totals.close_rounds`：

```text
py -3 .../append_adjudication_note.py --stem <plugin> \
  --note-topic "待裁词族 authority：确立义位分立 + 裁断位二合一" \
  --note-body "<多行正文，\n 用字面 \n>" \
  --verification "canonical xxxxxxxx。门禁 PASS（...）。"
```

1. 追加 `notes`（**同 topic 已存在则替换**，不堆重复条目）
2. 刷新 `canonical_at_adjudication`（现读现算，不接受手填）
3. 刷新 `totals.verification`
4. 写盘后回读断言 note 存在、canonical 一致

- `notes` 数组可能**混有字符串条目**，遍历时必须用 `isinstance(n, dict)` 守卫，
  否则回读断言会在 `.get()` 上失败。
- 中文正文用 `--note-body` 传参时不要在 PowerShell 里手拼长串；Write 落盘或参数数组更稳。

## 安全边界

- `read_batch.py` / `query.py` 纯只读。
- `apply_fixes.py` 只写 `.work/` 下的批次文件与指定 patch 路径。
- 不修改 MOD XML、不替代写回工具、不自动重跑 gate。修正后的重验与写回照常走
  `verify_subagent_batch.py` → `write_translations.py`。

## 验证方式

```text
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/translation-review-tools
py -3 .agents/skills/translation-review-tools/scripts/test_review_tools.py
py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_make_fixes_grouping.py"
py -3 -m unittest discover -s .agents/skills/translation-review-tools/scripts -p "test_formula_scan_exempt.py"
```

`test_review_tools.py` 覆盖：read_batch 双数据源与状态过滤、query 四种模式、
apply_fixes 的更新/校验中止/KEEP 转换/幂等/patch 生成/跨批自动同步（含 --no-sync-batches）、
整句覆盖守卫（G1 裸词拦截 / G2 同值批拦截 / 正常修正放行 / allow_collapse 显式放行）、
term_digest 的 MOD/OFF/CTX 格式化与空命中标注、dup 阈值、`--out` 落盘、
make_review_view 一致性守卫（一致生成 / 漂移拒绝 / --allow-drift）。

`test_term_digest_case.py`（unittest 风格，CI 的 `unittest discover` 步骤自动收集）覆盖
大小写警示与判据型 note 两条派单判据：分域 note 被带出、纯取证 note 不带出（保持摘要紧凑）、
note 截断与换行压平、**note 点名本行 `xml_index` 时不截断（含未点名行仍截断，
更长或更短的数字子串不得误判为点名）**、
`note` 缺失时安全、`--no-judge-notes` 同时关掉判据与图例且不影响主形、
`== 读法 ==` 图例默认存在。

`test_digest_freshness.py`（unittest 风格，CI 自动收集）覆盖 `read_batch.py` 的
term-digest 新鲜度门：图例判定（有无图例、残缺图例、文件版与文本版一致）、
新鲜 digest 不被触碰、缺图例 digest 与缺失 digest 的重生成并确认判据真的回到材料里、
无 `context.json` 时只警告不覆盖、`context.json` 损坏时返回 `failed` 而非抛错，
以及 `term_digest.build_digest()` 与 `digest()` 输出一致、注册表发现显式路径优先。

`test_make_fixes_grouping.py`（unittest 风格，CI 的 `unittest discover` 步骤自动收集）
覆盖 `make_fixes_from_report.py` 的分组通道：idx→批次归属映射、缺 `index.txt` 失败、
两批 idx 重叠失败、孤儿 idx 失败且不落盘、越界 idx 按既有口径跳过、空批次省略、
声明顺序保持、CLI 默认扁平形态不变、`--batches` 与 `--batches-dir` 不同用即报用法错。
另覆盖 `introduce_overrides`（--override 引入报告外 idx）：报告外 idx 被补进修正集并回填
`expected_current`、报告内 idx 不重复产出、`--drop` 优先于 override、`--include` 过滤生效、
现值已同记 no-op、越界只报不写，以及 CLI 端到端（override 引入的 idx 落到正确批次分组）。

`test_formula_scan_exempt.py`（同为 unittest 风格）覆盖 `formula_scan.py` 的豁免认得与
分组输出：豁免文件两种写法（`{idxs, reason}` 与数组）、缺文件当空、豁免 idx 不被整句组
改回错值、无豁免时仍报整句分裂、豁免只作用于整句组（首句公式收敛不受影响）、
`--batches`/`--batches-dir` 同用校验、分组输出形态与 `close_round --batches` 提示。

`test_form_census.py`（24 项，CI 的 `unittest discover` 步骤自动收集）覆盖 `form_census.py`：
源/Dest 顺序读取、未译行排除（空 Dest 与 `Dest==Source`）、**长形优先**（`等级体系`
不被 `等级` 吃掉，`--forms` 直传与契约自动取两条路径一致）、未归类桶、无形态时全量入桶、
锚为正则且大小写不敏感、批内主形识别与背离构成、单行批次不制造噪声告警、未知 idx 跳过、
契约形态提取（长形排序在前）与缺文件/未知锚回退空、CLI 报全库主形与收敛提示、
`--out` 落盘且内部下划线键不外泄、缺 XML 与源/译条数不一致均退 2；
开集发现：主形排首位、**整行去重**（同行三次「同意」只计一次，标点切开也算一次）、
忽略非 CJK、空输入安全、`sites` 只含已译行。

