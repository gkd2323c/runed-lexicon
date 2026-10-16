---
name: long-book-translation
description: "长文本分片翻译工作流。把一条超长文本（BOOK:DESC 书籍页、MESG:DESC 长描述等）切成体积恒定的翻译单元，每个单元带稳定 ID、原文哈希与位置记录；用分层记忆（文本护照 + 实体台账）在不扩大上下文的前提下维持全局一致性；用占位符机制把 [pagebreak] 与排版标签交给程序保护，模型只译纯文本；拼回时机械校验标记守恒，格式错误在写回前即被拦截。当任务涉及超长书籍/巨著/长篇书页或消息描述的翻译、长文本分片、或需要让翻译 Agent 的上下文不随文本长度增长时使用，即使请求里没出现 '长书籍' 这个说法。触发条件是长度而非记录类型。Do NOT trigger for 已译长文本的事后审查（那是 longtext-hallucination-review）、短书籍与单封信件（按常规批次翻即可）、批次验收与 XML 写回（归 translation-batch-ops / xtranslator-xml-writer）、QUST:CNAM 任务日志（同 EDID 多条短段递进，不是单条长文本，见 SKILL.md §7）。"
compatibility: "Depends on runed-lexicon project layout (mods/<plugin>/ canonical XML) and Python 3.10+. Scripts use only the standard library. Read-only with respect to MOD XML; all intermediate artifacts go under _tmp/."
metadata:
  version: "1.0.0"
---

# 长文本分片翻译

## 1. 核心原则：局部预算恒定

**翻译 Agent 的上下文长度不随书籍长度增长。**

一条两千字符的文本和一条三十万字符的文本，每个翻译单元的原文都受同一个
`--budget` 约束。文本长度只增加单元数量，不增加单元体积。

真正随文本规模增长的是外部账本：单元数量、台账条目数、跨切片审校任务数。
这些都在架构外部，不进入单次模型调用。

这条原则决定了后面所有设计取舍。任何"为了把背景塞得更全而放开上下文"的提案都违反它：把全书设定灌进每个单元，体积就随书长增长了，长文本风险重新回到模型内部。正确做法是单元只取与自己相关的信息，全局一致性交给译后的全书术语索引统一收口。

### 为什么不用模型的长上下文能力

把整本书塞进去让模型一次译完看似省事，实际有三个已知失效：

- 中段注意力稀释，首尾清晰、中间开始糊，而漏译的中间段读起来仍然通顺
- 长任务下风格退化，文言词、生僻字、骈偶句随任务长度单调增加，前半白话后半拟古
- 上下文里的实体在前文出现、后文回收时，模型早已丢失关联，同一人物前后称谓分裂

分片不是退而求其次的妥协，是把上述风险关在固定范围内的唯一办法。

## 2. 工作流

三步：规划分片 → 逐单元翻译 → 拼回校验。全程只读 canonical，不写回 XML。

```text
1. plan_book_shards.py   切书 → 单元读本 + plan.json + ledger.json
2. build_shard_pack.py   逐单元组装工作包（护照 + 相关实体 + 待译正文）
3. stitch_translation.py 译文拼回全书 + 五条不变量校验
```

### 2.1 规划分片

```bash
py -3 .agents/skills/long-book-translation/scripts/plan_book_shards.py \
  --xml mods/<plugin>/<plugin>_english_chinese.xml \
  --edid <BOOK:DESC的EDID> \
  --out-dir _tmp/data/book-<edid> --budget 8000
```

`--budget` 默认 8000。这是架构约束，不是模型上限；项目实测 3923 个章节块中最大 12941 字符、超 6000 的仅 1.8%，该值下绝大多数章节一章一单元。

分片边界优先级：

1. `[pagebreak]`（章节/分页边界，最强）
2. 章内自然段落边界（章本身超预算时）
3. 硬切（章内无段落边界时兜底，读本带警告，需人工复核衔接）

**守恒是硬断言**：各单元区间在原文上连续、不重叠、并集等于全文。任一不满足脚本直接拒绝产出。原因是切分一旦不可逆（例如按 `split` 丢掉分隔符），拼回结果与原文不符，哈希链断裂，后面所有校验都建立在错误的地基上。

相邻短章不合并。章节是叙事单元，合并会模糊边界。

### 2.2 逐单元翻译

先建**书籍护照**（一次性，全书共用）：

```json
{
  "title": "...", "author": "...",
  "narrator": "叙事视角与限知范围",
  "genre": "体裁：小说/日记/论文/信笺",
  "register": "语域基准：叙事段与对白各自的口吻，明确不用文言或过度拟古",
  "spoiler_boundary": "哪些信息在本文本位置不得提前揭示",
  "notes": "其他约束"
}
```

护照由主会话通读前几个单元后提炼，控制在几百字符。它是每个单元的固定成本，因此体积必须与全书规模无关。提炼时对抗两个先验：模型倾向于把一切文本都译成"通用书面汉语"，以及倾向于补注原文没有的种族/世界观说明。

再为每个单元组装工作包：

```bash
py -3 .agents/skills/long-book-translation/scripts/build_shard_pack.py \
  --plan-dir _tmp/data/book-<edid> --shard <edid>-S003 \
  --passport _tmp/data/book-<edid>/passport.json --out pack-S003.md
```

工作包含四部分：单元定位与原文哈希、书籍护照、**仅在本文出现过的**实体、待译正文指引。

派单时按 `subagent-ops` 走，注意：本流水线的子代理需要读工作包、读对应 `shards/shard-NNN.md`、并**把译文写成 `<shard_id>.txt` 落盘**到约定目录，因此必须派 `access="write"`。任务卡写明输出目录与文件名（`<shard_id>.txt`），不得让子代理把中文正文塞进回传消息。

### 2.3 拼回与校验

子代理交付后，把译文文件集中到 `translations/`，然后：

```bash
py -3 .agents/skills/long-book-translation/scripts/stitch_translation.py \
  --plan-dir _tmp/data/book-<edid> \
  --translations _tmp/data/book-<edid>/translations \
  --out _tmp/data/book-<edid>/stitched.json
```

五条不变量，任一硬错误即拒绝产出：

| 不变量       | 拦什么                                                       |
| ------------ | ------------------------------------------------------------ |
| 占位符守恒   | 编号集合与顺序必须与原文一致，禁止重排（标签顺序即版式顺序） |
| 标记原形未改 | 还原后每个位置的标记与分片时记录逐字节一致                   |
| 区间覆盖     | 无洞、无重叠、并集等于全文                                   |
| 无英文残留   | 连续 4 词以上的英文长串，意味着整段漏译                      |
| 无空译文     | 空文件、纯空白                                               |

缺失译文是硬错误而非警告。静默用原文填充会让一份全未译的产出拼出正常字符数并成功退出，下游拿到原文却看不出异常。

## 3. 格式标记：程序保护，不交给模型

Skyrim 书籍文本不是纯文本。受保护标记两类，权重相同：

- 尖括号标签：`<p align="center">`、`</p>`、`<img src='...'>`、`<font face='...'>`、`<b>`、`<br>`
- 分页符：`[pagebreak]`

分片时它们被抽成 `⟪F数字⟫` 占位符，模型视野里只有纯中文。拼回时按编号还原为原形。

**为什么不让模型直接输出标记**：模型会漏标、改标、重排标签，而这些错误在译文读起来通顺时完全不可见。把标记移出翻译视野，格式正确性就从"模型的自觉"变成"程序的断言"。

`[pagebreak]` 曾一度不被保护（理由是它已作为分片边界用完），结果某轮拼合后它全数丢失而校验未报。漏掉它游戏内书籍分页即乱，与标签同级，因此纳入同一套守恒校验。

`[sic]` `[x]` `[redacted]` `[untranslatable]` 不占位符化。它们是作者标给自己的元信息，量在数十次量级且语义上属于文本内容，留在正文里译。

未解码问题：脚本经 ElementTree 取得的是解码后文本（`<p align="center">`），XML 原文里是实体形态（`&lt;p align=&quot;center&quot;&gt;`）。分片器产物是读本文件而非 XML，在解码文本上工作是正确的；写回 canonical 时的逐字节保真由 `xtranslator-xml-writer` 的 span 定位负责，两处不重叠。

## 4. 分层记忆

三层，各取所需：

| 层     | 内容                             | 体积                   | 谁维护                     |
| ------ | -------------------------------- | ---------------------- | -------------------------- |
| 书籍层 | 护照：视角、体裁、语域、剧透边界 | 数百字符，全书恒定     | 主会话一次提炼             |
| 章节层 | 情节与伏笔、实体身份状态变化     | 按相关条目取，不取全书 | 主会话从**原文**提炼后追加 |
| 切片层 | 本文本                           | 受 budget 约束         | 子代理                     |

**原文永远是权威来源，护照与台账只是索引。** 台账只增不改：人物首次出现时登记，身份状态变化时追加新条目而非修改旧条目，历史译法保持可追溯。

**摘要不层层转抄。** 章节摘要由主会话回到原文提炼后落盘，不由上一个摘要总结而来。摘要总结摘要会指数放大偏差，转几手之后就与原著无关了。

实体台账初始形态：

```json
{
  "edid": "<EDID>",
  "entities": [
    {"en": "Szolus", "zh": "索卢斯", "status": "已登场",
     "note": "身份、年龄、能力、语气特征"}
  ],
  "terms": {}
}
```

`build_shard_pack.py --relevant` 按实体名在本文出现与否机械筛选，不猜相关性。没出现的实体不进包，译者不需要为不存在于本文的人物分心。筛选用子串匹配，人物名恰好是常见英文子串时需人工确认（如实体名 `An` 会命中任何含 `an` 的句子）。

## 5. 进度可恢复

每个单元的产物是独立文件：`shards/shard-NNN.md`（输入）、`translations/<sid>.txt`（输出）。`plan.json` 记录每个单元的 `status` 与原文哈希。

崩溃恢复：重跑 `build_shard_pack.py` 只组装未完成的单元，`stitch_translation.py` 只校验已交付的部分。**已通过验收的单元不重跑，不重新翻译。** 崩溃前的异常译文不进后续上下文：`stitch` 的英文残留与空译文探针会在拼合时拦下它，而不是让它污染下一单元的参照。

单个单元返工时，只重跑该单元：改 `translations/<sid>.txt`，再跑一次 `stitch`。其他单元的译文不受影响。

## 6. 跨切片审校

分片让每个译者只看得到局部，因此某些缺陷只有全书视角能发现：某角色第一章称"索卢斯"、第十章变成"索拉斯"，单独审任一片都看不出问题。

收口前做一次全书级核查，依据是**全书拼合结果中的术语索引**而非逐个切片：

```bash
# 全书拼合后在 canonical 上跑，不是跑单个分片
py -3 .agents/skills/noun-consistency-scan/scripts/<scan> ...
```

机械层报候选，歧义交由审校子代理判断。审校发现的问题走 `apply_fixes` / `make_fixes_from_report` 修正，不要手改分片文件后重新拼合（那会让分片与 canonical 脱节）。

顺序上，跨切片审校在拼合之后、写回 canonical 之前。

## 7. 触发条件与不适用边界

**触发条件是长度，不是记录类型。** 判定只看单条 Source 的长度与结构：

| 形态                          | 怎么走                           |
| ----------------------------- | -------------------------------- |
| 单条 < 2000 字符，无标记      | 常规批次，不必分片               |
| 单条 ≥ 2000 字符              | 本 skill                         |
| BOOK:DESC 任意长度            | 标记密集，长则分片，短则常规批次 |
| 同 EDID 多条短段（QUST:CNAM） | 见下，不走分片                   |

**QUST:CNAM 任务日志不走本流程。** 实测它的真实结构是**同 EDID 多条递增短段**，
不是一条长字符串：`AuridonCouncilQ02` 有 16 条 CNAM，各段 48 → 99 → … → 356 字符
递增，合计近 8000。写法是任务推进到哪一步，日志就替换成更长的一版（含前文追加）。

这决定了两件事：

- 单条 CNAM 最长约 965 字符，本就在常规翻译单元容量内，分片无从下手
- 真正需要跨段一致的是**同 EDID 的整条任务线**，因为后段包含前段内容

正确处理是把它当作**一个单元组**：同一份护照与台账，各段单独翻译但术语、
称谓、语气继承前面的段。分段之间按 XML 顺序翻译（后段依赖前段的译法），
不要并行派单。这条任务线的收口靠 `noun-consistency-scan` 在 canonical 上扫，
而不是靠本 skill 的切片校验。

MESG:DESC 与本 skill 的关系：实测最长约 941 字符、纯文本无标记，通常也不触发
分片。超长个体出现时走本流程，边界降级到句子。

## 8. 与相邻 skill 的分工

| 要做                               | 用                                                |
| ---------------------------------- | ------------------------------------------------- |
| 切书、组装工作包、拼回校验         | 本 skill                                          |
| 已译长文本的事后幻觉审查           | `longtext-hallucination-review`                   |
| 普通书籍、信件、短文本             | 常规批次流程，不必走本 skill                      |
| QUST:CNAM 任务日志                 | 同 EDID 多条短段递进，按单元组处理，见 §7         |
| 批次验收、写回 canonical、进度快照 | `translation-batch-ops`、`xtranslator-xml-writer` |
| 翻译工艺、术语裁决、保真基准       | `skyrim-translation-craft`                        |

`longtext-hallucination-review` 的分片逻辑与本 skill 方向相反：它要求同一本书**不拆开**、整组进入审校视野（因为审校要对照源译全局判断语义），因此 3 万字的书它无法处理。本 skill 的切片是为了翻译，不是为了审校。两者不要互相替代。

## 9. 验证

交付前跑一遍回归（synthetic mini-XML，不碰真实项目文件）：

```bash
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/long-book-translation
py -3 scripts/test_book_shards.py
```

`test_book_shards.py` 覆盖：分片守恒、占位符编号一致、漏标被拦截、缺失译文被拦截、空译文被拦截、英文残留被拦截、标记逐字节还原。

改脚本后必须重跑该测试；改 `MARK_RE` 时两个脚本要同步（分片器的编号规则与校验器的还原规则必须一致，否则编号对不上）。

实测性能基线（详见 §2.1 的样本）：`plan_book_shards.py` 对 3.2 万字 / 7 单元的书籍为秒级；`stitch_translation.py` 同规模秒级。两者均单次解析 XML，无循环内重复解析。
