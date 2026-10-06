---
name: skyrim-mod-onboarding
description: >-
  新 MOD 初始化：把一个只有源 XML（加插件本体，可能还有作者 README）的裸 MOD 目录，
  推到「四文档齐备、术语底座可编译、首批次可派单」的可开工状态。
  覆盖现场诊断、材料归置、插件结构导出、官方专名清点、CONTEXT / DICTIONARY / PROGRESS / SOP 初建、
  terms.json 初版与契约编译、首批次计划与覆盖率核验。
  Use when a MOD directory is missing CONTEXT.md / DICTIONARY.md / PROGRESS.md / SOP.md or terms.json,
  when opening a new MOD for translation, when asked to 初始化 / 接入 / 开工 / 起一个新 MOD,
  or when translation-batch-preparer refuses to run because the MOD memory files are missing.
  Do NOT trigger for 已有四文档的 MOD 的日常批次推进（归 translation-batch-ops）、
  术语的语义裁决本身、单批次翻译与审校、契约编译之后的门禁与写回。
compatibility: Reference workflow only; no scripts of its own. Consumes skyrim-xml-tools, mutagen-dialogue-exporter, proper-noun-index, term-contract-compiler, translation-batch-ops, translation-review-tools and skyrim-doc-system.
metadata:
  version: "1.0.0"
---

# 新 MOD 初始化

## 0. 这个 skill 的位置

`mods/<plugin>/` 里出现源 XML 而四文档与 `terms.json` 缺失时，这个 MOD 处于**未初始化**状态：可以读 XML，但还不能开工。`translation-batch-preparer` 会在缺 `CONTEXT.md` / `DICTIONARY.md` 时直接拒绝运行，`translation-context-builder` 缺术语底册会漏挂官方名，没有批次计划就没有可派单位。

本 skill 负责把这段空窗补上。分工：

| 关心的事 | 归谁 |
| --- | --- |
| 四文档各写什么、收录标准、术语状态语义 | `skyrim-doc-system`（本 skill 只定顺序、取材与验收） |
| 从零到有文档、有术语底座、有批次计划 | 本 skill |
| 契约编译之后的门禁、写回、复扫 | `skyrim-term-contract-workflow` |
| 批次备料、派单、验收、收口、进度 | `translation-batch-ops` |

规则说「不得静默创建空模板」，指的正是这个阶段：**补建必须据实际材料**（源 XML 的 REC/EDID/对话结构、插件本体、作者公开说明、官方词典），材料不足以考证剧情事实或专名出处时停下确认，不靠编造把文档填满。

## 1. 现场诊断

先数清手上有什么，再决定动作。初始化要动的每一处，判据都是「文件在不在 + 内容是不是这一代」。

```text
py -3 .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py inspect mods/<plugin>
```

`inspect` 一次给出规模与 REC 分布（`INFO:NAM1` / `DIAL:FULL` / `QUST:FULL` / `BOOK:DESC` / `NPC_` / `CELL` / …），这份分布同时是后面选材与建批的依据——它告诉你这个 MOD 是对话为主、书页为主还是名录为主。

| 对象 | 必需性 | 缺失后果 |
| --- | --- | --- |
| `mods/<plugin>/<plugin>_english_chinese.xml` | 必需 | 无输入，停止 |
| `mods/<plugin>/<plugin>.esp/.esm/.esl` | 强需 | 无法导出说话人 / 任务线结构，CONTEXT 只能靠 XML 硬读 |
| `CONTEXT.md` | 必需 | 语义无据，`translation-batch-preparer` 拒绝运行 |
| `DICTIONARY.md` | 必需 | 同上 |
| `PROGRESS.md` | 必需 | 后续会话无法恢复状态 |
| `SOP.md` | 必需 | 后续会话现场拼流程 |
| `terms.json` | 必需 | 契约无从编译 |
| `.work/<plugin>/{batches,archive,reports,context,contracts}` | 必需 | 产物无处落 |

`Source != Dest` 的行不是「已翻译进度」，是导出流程的词典自动匹配产物（工具结果，非人工决策）。初始化阶段不核验它们，随正常批次工作流处置；它们也不计入 `PROGRESS.md` 的完成量。

## 2. 材料与归置

四类材料，来源与落点各不相同：

- **决定性材料**：源 XML、插件本体。已在 `mods/<plugin>/`。
- **结构材料**：Mutagen 从插件导出的 DIAL→INFO 结构、说话人、任务线归属。落 `.work/<plugin>/context/`。
- **语境材料**：作者 README、Nexus 描述页、安装说明、兼容性清单。这是剧情前提与角色定位最省力的来源。落 `.work/<plugin>/notes/<plugin>-sources.md`（可原样转存 + 标注来源 URL）。
- **证据材料**：`dictionary/` 官方英中语料、`GLOBAL.md`、`GLOSSARY.md`、`global-forbidden-words.json`。只读。

`mods/<plugin>/` 保持契约内文件（源 XML、canonical 写回产物、插件本体、四文档与 `terms.json`）。作者 README 这类外部材料归 `.work/<plugin>/notes/`，不让 `mods/` 变成资料堆。

## 3. 初始化序列

六步，按序执行。步与步之间有依赖（专名清点要等结构导出，术语编译要等 `terms.json`），不要跳序。

### 步 1 建骨架

```powershell
'batches','archive','reports','context','contracts','maps','notes','gates' |
  ForEach-Object { New-Item -ItemType Directory -Force -Path ".work/<stem>/$_" | Out-Null }
```

`<stem>` 是 `.work/` 下与批次上下文里使用的短标识。新 MOD 直接取插件主干（去掉 `.esp/.esm/.esl`）；主干含空格、过长或与既有目录不易区分时，收敛成一个可读短名，并把它写进 `SOP.md` §0 的路径常量表——后续所有命令都以那张表为准。

### 步 2 结构导出（有插件本体才做）

```text
dotnet build .agents/skills/mutagen-dialogue-exporter/scripts/DialogueExport/DialogueExport.csproj
dotnet run --project .agents/skills/mutagen-dialogue-exporter/scripts/DialogueExport -- `
  mods/<plugin>/<plugin>.esp .work/<stem>/context/<stem>-mutagen-dialogue.json "<SkyrimSE Data 目录>"
py -3 .agents/skills/mutagen-dialogue-exporter/scripts/convert-dialogue-context.py <stem> mods/<plugin>
py -3 .agents/skills/mutagen-dialogue-exporter/scripts/split-info-lines.py <stem> mods/<plugin>
py -3 .agents/skills/mutagen-dialogue-exporter/scripts/plan-info-batches.py <stem> mods/<plugin>
```

第一个 `.esp` 参数必须显式给目录（`.esm` 才有默认路径）。`convert-dialogue-context.py` 会把 Mutagen 原始结构映射成 `translation-context-builder` 能吃的 xEdit 风格——**原始 Mutagen JSON 不能直接喂 builder，会 0 连接且静默 unmatched**。

两个立即要看的验收数：

- `convert` 打印的 FormID 前缀推导命中率：数千条记录应近乎全中。无交叉命中说明 XML 与插件不是同一代次。
- `split-info-lines` 的未连接数：`全 XML 未译 INFO:NAM1 行数` 应当约等于 `各线未译之和 + unlinked`。差距大说明存在没接上的 EDID 形态（源文里 FormID 型与命名型可能混用），在接入别的 MOD 时要先解决通道问题。

### 步 3 批次计划

```text
py -3 .agents/skills/noninfo-batch-planner/scripts/plan_noninfo_batches.py --xml mods/<plugin>/<plugin>_english_chinese.xml --stem <stem>
```

INFO 批（`plan-info-batches.py`）与非 INFO 批（`plan_noninfo_batches.py`）**都要在开工前建好**：只建 INFO 计划，非对话行会整类留在计划外，后面「计划 100% 完成」却是假收敛。`INFO:RNAM` 这类玩家选项行既不在 INFO 对话链里，也被非 INFO 的默认规则排除，用 `--include-info-recs "INFO:RNAM"` 单独成计划。

建完当场核一次覆盖率，缺口不为 0 就是选材漏了族类：

```text
py -3 .agents/skills/translation-batch-ops/scripts/scan_plan_gaps.py --xml mods/<plugin>/<plugin>_english_chinese.xml --plan .work/<stem>/context/<stem>-info-batches.json
```

### 步 4 官方专名清点

```text
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py build
py -3 .agents/skills/proper-noun-index/scripts/proper_noun_index.py scan .work/_shared/proper-noun-index/index.json --target mods/<plugin>/<plugin>_english_chinese.xml --json .work/<stem>/reports/<stem>-noun-inventory.json
```

`build` 是词典的派生产物，`dictionary/` 变了才需要重建；索引已在就先 `scan`。清点结果按 `ENTITY` / `ENTITY_LOW` / `MULTIWORD` / `SEMANTIC` 分层，是 `DICTIONARY.md` 与原版词语条的**候选清单，不是判决**：单字名（`ENTITY_LOW`）和语义变体（同一地名对应城市与领地两种官方形）要按语境逐条裁决。

### 步 5 四文档初建

材料在步 2、步 4 已经齐了，这一步是写作。收录标准与状态语义见 `skyrim-doc-system`，这里只说取材与顺序。

**`CONTEXT.md`** — 顺序：先结构，再对话，最后情节。

| 段落 | 主要来源 |
| --- | --- |
| MOD 基本信息与主题 | 作者 README / Nexus 页；`TES4:CNAM` |
| 故事前提与阶段 | 作者 README；`QUST:FULL`（任务名）+ `QUST:NNAM`/`CNAM`（阶段日志） |
| 角色身份 / 关系 / 性格 / 说话方式 | `NPC_:FULL`；dialogue-context 的 speaker + quest 归属；`INFO:NAM1` 原文的用词层级与自称 |
| 任务结构与重要事件 | `plan-info-batches.py` 产出的任务线结构（线 → 主题 → INFO） |
| 地点 / 派系 / 自创设定 | `CELL:FULL`、`WRLD:FULL`、`LCTN:FULL`、`FACT:FULL` |
| 特殊文本风格 | `BOOK:FULL`/`BOOK:DESC` 的文体、碑文、谜语 |

写的时候把「作者明说的」与「从文本推出来的」分开标注：作者 README 提供的是设定事实，从 `QUST` 日志与对话推的是推测。推测要显式标为推测，供后续译者复核——文档里一条没标来源的断言，后面的人只能当事实用。

**`DICTIONARY.md`** — 只收值得跨文本保持一致的决定：MOD 名与副标题、自创角色 / 地点 / 派系 / 物品 / 法术 / 机制名、任务关键概念、明确 KEEP 的原文。每条带英文、中文、类型、状态、依据与备注。

初版的状态分布应当以 `PROVISIONAL` 为主：新 MOD 开工时，MOD 原创专名能定的是**可用方案**而非定论。`CONFIRMED` 只留给有官方语料实证的原版名。同一个英文锚词在库里多处复用时才值得收；只出现一次的自创名先写进 `DICTIONARY.md`，不必急着进机器词表。

**`PROGRESS.md`** — 初版写三件事：当前阶段（初始化完成 / 待开工）、基线统计、下一动作。基线统计以 `inspect` 的输出为准（总行数、`Source==Dest` 未译数、按 REC 的分布）；**canonical 生成之前不要跑 `progress_snapshot.py` 的 `--record`**，它统计的是已完成态，此时会把未开工报成异常口径。canonical 首次写回后立即改用它。

**`SOP.md`** — 初版是 MOD 实例化的操作序列，不是复述通用规则：

- §0 路径常量表：stem、moddir、源 XML、canonical（预计路径）、契约路径、批次目录。
- §1 每轮续推循环：照 `translation-batch-ops` §0 的六步，把本 MOD 的具体路径与参数填进去。
- 取批方向与占用筛：按 `AGENTS.md` §2.1 的并发竞态处置，写清本 MOD 采用的方向（倒序取最大编号的干净批）。
- 后续每轮收口有新的路径或命令变化时同步更新；事故教训沉淀进 `SOP.md` 与对应通用 skill，两处不写同一半。

### 步 6 terms.json 与契约

`DICTIONARY.md` 是人读文档，不进机器链路；机器侧术语源是 `mods/<plugin>/terms.json`。把步 4、步 5 里**有依据的**决定转成结构化条目（`english` / `zh` / `status` / 可选 `forbidden` / `note` / `risk_flags`），然后编译：

```text
py -3 .agents/skills/term-contract-compiler/scripts/compile_contract.py \
  --terms mods/<plugin>/terms.json \
  --output .work/<stem>/contracts/<stem>.compiled.json \
  --global-bans global-forbidden-words.json
```

三条入表判据（不满足就别进机器词表，只登 `DICTIONARY.md`）：

- **锚词能不能唯一定位到那个专名**。常用虚词（here / there / this one / lord / school）与别的专名的子串（某单节音译名是更长名字的一部分）一旦以 `REQUIRED` 入表，会把源文里几十上百行判成「required target 未出现」，整批 FAIL。
- **有没有复用**。只出现一次的专名没有漂移风险，入表只是给门禁添噪声——判据是「源文别处复用」。
- **是不是官方名词的坏形态**。跨 MOD 的系统性坏形态归 `global-forbidden-words.json`，单 MOD 专有词的错误形态写本表的 `forbidden`。

编译通过后再进 `skyrim-term-contract-workflow` 的契约工作流（unit bindings、写回前 gate）。编译前先跑 `lint_terms.py` 做字符级体检（引号配对 / 方向 / 不可见字符 / 全半角 / 繁体）。

## 4. 初始化验收

六条全过才算可开工：

1. `.work/<stem>/` 骨架齐，路径常量已写进 `SOP.md` §0。
2. `dialogue-context.json` 与 `info-batches.json` 已生成，`unlinked` 已判读并归零或已归因。
3. INFO 与非 INFO 两套批次计划都已建，`scan_plan_gaps` 缺口为 0。
4. 四文档存在，且每一段断言都有来源（材料或标为推测）。
5. `terms.json` 已编译，`.work/<stem>/contracts/<stem>.compiled.json` 已生成。
6. 抽一个批次走完备料三件套，走通即证可开工：

```text
py -3 .agents/skills/mutagen-dialogue-exporter/scripts/make-batch-index.py <stem> <BID>
py -3 .agents/skills/translation-batch-ops/scripts/rebuild_context.py --stem <stem> --batch <BID>
py -3 .agents/skills/translation-review-tools/scripts/term_digest.py --context .work/<stem>/batches/<BID>/context.json --out .work/<stem>/batches/<BID>/term-digest.md
```

备料三件套可安全重跑。走通后按 `translation-batch-ops` §0 的循环正式开工，初始化阶段到此结束。

## 5. 何时停下问用户

只有三类要停：

1. **材料不足**。作者没给说明、插件结构也不足以还原剧情事实或专名出处（如一个只在物品描述里出现一次的名字，拼写与命名习惯都给不出可用方案）——报出哪一条卡住、缺什么材料，不要编。
2. **XML 与插件不同代次**。FormID 前缀推导无交叉命中，说明源 XML 不是这个插件导出的。先确认再动，别在错代次上建文档。
3. **master 缺失**。导出打印 WARNING 时 speaker / quest 会退化为 null；先补 SkyrimSE Data 目录，再下结论。

正常情况下的名称取舍、术语状态选择、批次规划都不需要问：能查到证据的自己查，能形成可用方案的就落 `PROVISIONAL` 继续推进。

## 6. 已知坑

- **不要在初始化阶段跑 `dictionary-noun-audit`**。它检查的是 Dest 侧漏项，此时全库都是未译行（`Dest == Source`），会给出成百上千条无效候选。它是收口阶段的工具，不是开工前的工具。
- **不要用 `translation-batch-preparer` 建全量批**。它是单批手工挑选的旧工具，全量切批用 `plan-info-batches.py` + `plan_noninfo_batches.py`。
- **不要手工写 `compiled.json`**。契约是派生产物，改术语一律改 `terms.json` 再重编译。
- **不要用空模板充当文档**。空 `CONTEXT.md` 会让后续译者以为「没有可查的设定」，比缺失更糟——缺失会触发停止，空模板会静默通过。
- `PROGRESS.md` 的状态数字只放能被脚本重算的量，且以最近一次 `--record` 快照为准；手填数字会在下一轮被覆盖成矛盾值。
