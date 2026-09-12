---
name: longtext-hallucination-review
description: "长文本抗幻觉审查工作流：对已译 MOD 的长叙事文本（BOOK 书页、QUST 长日志、MESG 长描述）做两层审查——机械探针排除灾难性截断、子代理逐段精读找语义幻觉（编造/漏译/漂移/反转）。Use when 需要审查长文本译文是否忠实于源文、怀疑长段落里事实被改写、要批量核查书页类译文的质量、或在收口前做长文本语义复核。Triggers: 抗幻觉, 幻觉审查, 长文本审查, 长文翻译质量, 事实保真, 编造漏译漂移反转, hallucination review, long-text fidelity. Do NOT trigger for: 单条短文本的译法judgment（用 translation-review-tools 的 query/read_batch）、名词一致性扫描（用 noun-consistency-scan）、机械忠实度扫描（用 translation-fidelity-scan）、纯风格评审（用 prose-critique）。"
compatibility: "Depends on runed-lexicon project layout and the translation-review-tools skill's scripts (hallucination_probe.py, longtext_readout.py, query.py). Read-only except the final correction writeback via xtranslator-xml-writer. Requires subagents for the semantic pass."
metadata:
  version: "1.0.0"
---

# 长文本抗幻觉审查

长叙事文本（多段书页、长任务日志）是**幻觉性偏差**的高发区：句子读得通、字数也对，但事实被改了。这类错误逃过所有机械检查——名词扫描只查已登记词，忠实度扫描只查禁形与否定词形，字数/标点检查更看不到语义改写。

本 skill 定义一层专门针对长文本的审查：**先机械排除灾难，再语义精读抓幻觉**。两层缺一不可，因为二者抓的是完全不同的问题。

## 1. 两层口径（不要混用）

| 层 | 手段 | 能抓什么 | 抓不到什么 |
| --- | --- | --- | --- |
| 机械探针 | `hallucination_probe.py` | 段落数异常、长度比失衡、数字语义丢失、性别代词冲突 | **任何读得通的语义改写** |
| 语义精读 | 分片 + 子代理逐段对照 | 编造、漏译、漂移、反转 | 无（但需人工核验与裁决） |

**关键认知**：机械探针在长文本上通常只报个位数候选，而语义精读能报出数十处。这不是探针没用——它的价值是**用几秒钟排除「整段截断」这类灾难**（若某长页译文只有源文三成且以省略号收尾，那是结构性问题，优先级高于任何语义细节），从而把有限的重读预算全部投给语义层。

不要因为探针报得少就认为译文没问题；也不要因为精读报得多就认为探针该扔掉。

## 2. 流程

### 2.1 勘察长文本规模

先确定审查范围。以源文字符数设阈值（300 是长叙事与短对白的自然分界），按 `REC` 与 `EDID` 分类：

```bash
py -3 .agents/skills/translation-review-tools/scripts/longtext_readout.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --out-dir _tmp/data/longtext-shards --min-src 300 --cap 17000
```

该脚本按 `[idx]` 头分块、同 EDID 不拆开、组间按最长行降序（高风险先审）、按源文字符量贪心装片。同时产出 `manifest.json`。

**片大小上限**由子代理单次读取能力决定：单文件超过约 50KB 会读不进去，故 `--cap` 以「源文 + 译文 + 头」总字节反推，通常 15K–20K 源文字符安全。

### 2.2 跑机械探针

```bash
py -3 .agents/skills/translation-review-tools/scripts/hallucination_probe.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml --min-len 300
```

四类信号（P1 段落少于源文 / P2 长度比异常 / P3 数字语义缺失 / P4 性别代词强冲突）。**默认门槛 300 不要随意调低**：短口语行中文天然比英文短得多，门槛降到 120 会引入数十条纯噪音。探针自带的已知误报清单见其 SKILL 章节，核销时看命中的上下文词，不只看词表。

### 2.3 派单做语义精读

按片派子代理，`access="read"`（只读审查，产出以回复正文返回，不落盘）。任务卡见 §3。

并行度按实例位上限派满；已验收的实例**立即关闭**腾位，否则后续分片派不出去。

### 2.4 验收与写回

对每条报出的候选：**逐条回源文核验**（§4），成立则修订，不成立则记录驳回理由。修订走标准链路：

```bash
py -3 .agents/skills/xtranslator-xml-writer/scripts/write_translations.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --source-xml mods/<plugin>/<plugin>_english_chinese.xml \
  --in-place --archive-to .work/<plugin>/archive \
  --patch .work/<plugin>/maps/<plugin>-fix-patch.json \
  --report .work/<plugin>/reports/<plugin>-writeback-report.json --force
```

长文本常见方括号内容（页码标记、动作提示）；若某条含已中文化的方括号标记，patch 需带 `waived_tokens` 声明（见 `xtranslator-xml-writer` 的 R16 章节），否则写回被占位符校验拦下。

## 3. 任务卡模板

四类检查点必须写死，且明确**不报**什么——否则精读会退化成第二遍文笔评审。

```text
任务：抗幻觉语义审查（长文本分片 N）

输入：
- 读本：<shard 路径>（源译对照；每条以 `[idx] REC | EDID | src=N` 开头，下面依次是【源文】与【译文】）
- 词表：mods/<plugin>/DICTIONARY.md（专名以它为准）

任务：逐段对照源文与译文，找出译文相对源文的**幻觉性偏差**。

检查点（只报这四类，宁缺勿滥）：
1. 编造：译文出现源文没有的人物、事件、物品、地点或数字
2. 漏译：源文某段或某个关键信息在译文中完全没有对应
3. 漂移：动作主体、对象、因果或时序被改动
4. 反转：否定变肯定、条件变无条件、可能变确定、建议变命令

不报：用词选择、句子长短、语序调整、修辞风格、分段差异。

输出：
- schema：JSON 数组，每条为 {"idx": <行号>, "kind": "编造|漏译|漂移|反转", "severity": "HIGH|MEDIUM|LOW",
  "quote_src": "源文片段", "quote_dst": "译文片段", "why": "一句话说明"}
- 无发现则输出 []

完成条件：
文件内每个 idx 都已通读；回复内容只含该 JSON 数组与必要的简短说明。
```

关于「不报」那一行：它是任务卡里最容易被忽略、又最影响产出质量的一条。没有它，执行者会把用词、断句、风格全部报上来，把信噪比拉到无法验收。**但也要留出备注空间**——见 §4.3。

## 4. 验收纪律

### 4.1 每条必须回源文核验，不能只信报告

审查产出是**候选**，不是结论。核验时用源文片段定位，不用报告给的 idx 直接改。

**报告给的 idx 可能是错的**：把某条的问题挂在相邻 idx 名下、同时声明相邻条「无偏差」，是实际发生过的情形。按内容特征串回库定位（`query.py --src` 或脚本遍历），确认真实 idx 后再动笔。

### 4.2 不抄报告里的中文形

报告引述的译文片段可能带错别字（执行者转写产生）。**任何要写回的中文形，先在整个 canonical 里查一遍**，确认它是既有形态而非报告引入的新错误；若报告建议的中文形与全库既有形冲突，以全库既有形为准。

### 4.3 备注区也要抽核

执行者常把「不属四类但值得注意」的问题放在 JSON 之外。**这类备注的信息量不比正文小**——连续多片都能从备注里捞出真问题（比较级丢失、人名误作书名、词义误读）。

处置原则：备注里的候选同样逐条核源文，成立就改，不成立就记驳回理由。不要因为「它说不算」就跳过。

### 4.4 驳回报错

执行者会把自己不确定的东西按保守口径报上来。**核完确认无误的要明确驳回**，典型误报：

- 同一英文在不同搭配下对应不同中文（源文 `the Eight Givers` 与 `the Eight lent portions...` 本来就该两译），报成「同源多译分裂」
- 两个中文形其实各指不同对象（`表弟`/`表哥` 分指对话双方，各自自洽），报成「同一个人两名」
- 作者笔误被当成译文错误（源文 `He's` 实指女性角色，译文用「她」是对的）

驳回理由写进验收记录，避免下一轮重复排查同一处。

## 5. 已知不可译的处置

某些长文本的**机关依赖英文本身**，中文无法保留：

- 字母序对话（源文对白按 A/B/C… 开头排列）
- 逐词倒转句（呼应「逆转」主题）
- 英文首字母缩写、藏头、押韵结构

处置：采「叙述句说明设定 + 正文正常译」的务实路线，并**在 `CONTEXT.md` 的「特殊文本风格」节记为已知局限**。理由：中文句子不以英文字母开头，强行重构只会让文本不可读，且读者仍无法从译文观察到机关。

关键区分：**机关本身不可译属已知局限，但机关里的关键元素被误译仍是缺陷**。例：字母序机关中的关键字母 `Y` 是机关的组成部分，被译成「好」就破坏了逻辑链（角色后悔没能把对话拖到 Y），必须还原为字母。

## 6. 高频幻觉模式

以下六类是长文本审查中反复出现的形态，可在验收时优先留意，并作为 `skyrim-translation-craft` §6 的反例对照：

| 模式 | 表现 | 反例 |
| --- | --- | --- |
| 用背景知识覆盖文本 | 译者按自己的世界观常识改写了源文描述 | 源文 `smooth, wet skin` → 「光滑干燥」（译者知道该种族是爬行类） |
| 可能性硬化/软化 | 情态强度被改 | `might` → 「肯定」；`does not` → 「不一定」 |
| 让步/因果互换 | 连接词逻辑关系反转 | `though`（让步）→ 「鉴于」（因果）；`since`（原因）→ 「虽然…但…」 |
| 自行推算源文未给的数字 | 对源文数字做心算或模糊化 | 「五年前，再往前三年」→ 「八年前」；`thousand` → 「众多」 |
| 被动改施动并增动作 | 虚拟/被动式被改成施动并添加行为 | `what if one of us were injured, but not killed?` → 「一人重伤未死，却没杀害另一人」 |
| 同形异义取错义项 | 多义词取了与本语境不符的义项 | `shell`（船壳）→ 「贝壳」；`censer`（香炉）→ 「烟雾检测」；`will it to relent`（以意志迫其屈服）→ 「等待其转变」 |

## 7. 安全边界与验证

- 本 skill 的审查环节**只读**：探针、分片、精读均不修改任何文件。唯一的写入是验收后的 canonical patch，走 `xtranslator-xml-writer`。
- 修改前后必须复跑结构校验：`<String>` 数一致、`Source`/`EDID`/`REC` 零改动、空 `Dest` 为 0、保留原文行数不变。
- 每轮修改后复跑机械探针，确认未引入新的段落/长度/数字异常。
- 审查中间产物（分片读本、探针 JSON）落 `_tmp/`，任务收尾清理；审查结论（驳回理由、已知局限）落 `PROGRESS.md` 与 `CONTEXT.md`。
- 与既有工具的分工：本 skill 管**长文本的语义保真**；`translation-fidelity-scan` 管词形与禁形、`noun-consistency-scan` 管名词收敛、`translation-quality-gate` 管契约符合性。四者互为补集，收口前都应跑过。
