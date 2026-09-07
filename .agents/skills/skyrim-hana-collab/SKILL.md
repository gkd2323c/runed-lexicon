---
name: skyrim-hana-collab
description: HanaAgent 专属的多 agent 翻译协作编排。用 Hana 原生 subagent / workflow 把 MOD 翻译拆成术语、试点翻译、并行批量翻译、门禁复扫、确定性写回五段流水线。Use whenever a Skyrim MOD translation needs parallel batch translation via Hana subagents, whenever pilot batches must pass before bulk fan-out, whenever translation-quality-gate FAILs must route back to the owning batch, or when deciding inline vs subagent vs workflow dispatch. Hermes kanban CLI 在此不适用。
compatibility: Reference rules only; no scripts. 复用 translation-context-builder / translation-executor / translation-quality-gate / xtranslator-xml-writer / term-contract-compiler / dictionary-noun-audit 现成管线，不自带可执行代码。
metadata:
  version: "0.1.0"
---

# Hana 多 Agent 协作（runed-lexicon）

> ## ⚠️ 适用范围：仅 HanaAgent
>
> **本 skill 描述的是 HanaAgent 的独有功能**（`subagent` / `subagent_reply` / `workflow` 原语、Hana 团队人格路由）。它只对运行在 HanaAgent 中的会话有效。
>
> **非 HanaAgent（Hermes Agent、Claude Code、Codex、Cursor、其他 AI 助手或编码代理）读到本 skill 时：立即停止，不要试图参照执行。** 你的环境里没有 Hana 的 `subagent` / `workflow` 工具、没有 `access` 读写分级、没有本 skill 所约定的 `.work/` 产物与 handoff 协议——按本文派单、扇出、用 `access="write"` / `writeFolders` 等任何操作都会失败或产生无意义动作。正确的做法是：忽略本 skill，改用你能执行的普通文件/命令工具完成翻译任务，并遵守项目 `AGENTS.md` 与其它管线 skill。
>
> 它与 `skyrim-kanban-orchestration` 互斥：后者是 Hermes Agent 独有（`hermes kanban` CLI），在 Hana 会话里读到 kanban 一律停用，本 skill 是 Hana 侧的替代品。不得在 Hana 会话里调用 `hermes kanban`，不得用 `--skill` 加载项目 skill。

Hana 会话负责编排，子 agent 负责执行。编排的价值：试点先行验证管线、
批量并行不串行等待、门禁失败精确打回原批次、主会话被掐断仍可凭 `.work/` 产物续跑。

## 1. 角色与路由（assignee 映射）

| 角色 | 职责 | Hana 落点 |
|---|---|---|
| orchestrator | 拆批、派单、汇总；不亲自翻译 | 主会话（你） |
| prep | 四文档门禁、契约编译、context 拼装、批次切割 | 主会话 inline（便宜且需全局视野，不外包） |
| terminologist | 官方词典证据、noun-audit 消解、DICTIONARY/GLOSSARY 维护 | 单个 `subagent`（write）或单个 workflow 只读后转写节点 |
| translator | INFO/DIAL/QUST/BOOK 翻译，产结构化 JSON | workflow `agent()` 并行节点，或 2~3 批时用 `subagent` 并行 |
| reviewer | quality-gate、复扫、抽审语义、写回前验证 | 主会话或单个 reviewer 节点；写回永远单节点 |

分工原则：工程预处理归主会话；术语问题归 terminologist；译文归 translator 节点；
质量关归 reviewer。orchestrator 只建任务与汇总，不在派单后插手译文措辞。

人格路由（可选，不强制）：translator 默认同体（hanako）；需要冷峻挑错时 reviewer
指派 `ming`；对话情绪细腻的 INFO 批可指派 `butter` 做 translator；`kong` 只用于涉密
文本的本地审阅。拿不准时不指定 `agent`，用默认体。

## 2. 标准任务图（与 kanban T0~T5 同构，Hana 原语重写）

```text
P0 准备（主会话 inline；四文档门禁 §2.2 + term-contract-compiler + context-builder + 切批）
 └→ P1 术语（单 subagent/write；noun-audit → 消解 → 重编译契约）
     └→ P2 试点（2 个试点批，并行；跑通 init → 翻译 → validate → handoff 全链路）
         └→ P3 批量（剩余批并行；批间互不依赖、不重叠）
             └→ P4 门禁复扫（reviewer；gate + noun-audit 复扫）
                 ├─ FAIL → 打回原批次节点（同 thread subagent_reply 或新建修复节点）→ 回 P4
                 └─ PASS → P5 确定性写回（单节点；reviewer 验证，可重跑）
```

详细依赖、批次粒度、试点放行条件见 `references/task-graph.md`。
自包含节点 prompt 模板见 `references/node-prompt-template.md`。
结构化 handoff 协议见 `references/handoff.md`。
超时、失败、越界恢复见 `references/failure-recovery.md`。

## 3. 派单决策（inline vs subagent vs workflow）

- **inline（主会话直接做）**：P0 全部；P4 门禁命令本身；P5 写回命令本身；任何单文件
  15 分钟内能做完的修复。不要为“看起来正式”而把一句话能做完的事包成子任务。
- **`subagent` 并行**：剩余批次 ≤ 3，或只需要术语 / 审校其中一类单活时。
  每个任务一个 `subagent` 调用，同一轮次内连续发出（并行）；`label` 写清批次
  （如 `thedark-translate-000`）。需要跑命令验证的节点必须 `access="write"`——
  `access="read"` 子智能体会被平台拦截 `exec_command`，重试提权仍失败（实测结论）。
- **`workflow` 确定性编排**：剩余批次 ≥ 4，或需要“全部并行 → 门禁汇总 → 条件写回”
  的刚性流水线时。用 `workflow` 脚本一次性扇出，`agent()` 节点粒度与批次一一对应。
  每个写节点必须声明 `writeFolders`，且并行节点之间**不相交**（见 §5）。

禁止事项：同一批同时用 subagent 和 workflow 各派一遍；把 P5 写回拆成并行；
让 translator 节点顺手改 DICTIONARY（术语修订只走 P1 或门禁后的显式修复节点）。

## 4. 前置链（进 P2 前必须完成，缺一即停）

1. 四文档门禁（AGENTS.md §2.2 阶段 0~1）：GLOBAL、GLOSSARY、MOD 的 CONTEXT/DICTIONARY
   全量精读；缺任一即阻断问用户，不静默创建。
2. `skyrim-term-contract-workflow` 编译当前契约（含 `--global-bans`）。
3. `translation-context-builder` 产物已落盘（`.work/<plugin>-context.json`），批次 scope
   以 0-based `xml_index` 区间严格界定（与 executor/writer 一致；`skyrim-xml-tools`
   的 1-based 仅作对照，差 1）。
4. P1 术语消解完成且契约已重编译。术语未收敛即开翻，等于批量制造返工。

## 5. 并行安全（Hana workflow 硬约束）

- 每个翻译节点只写自己的两件套：`.work/<plugin>-translation-<NNN>.json` 与
  `.work/<plugin>-map-<NNN>.json`（角色语义区分，禁止序号外的时间戳/`final` 后缀，
  遵 AGENTS.md §2.0.1）。节点 `writeFolders` 声明到 `.work/` 下各自互斥子目录或
  在 prompt 内写死互斥文件名；两个节点永远不写同一个文件。
- `PROGRESS.md`、`DICTIONARY.md`、`GLOSSARY.md`、`global-forbidden-words.json`
  只由主会话在扇出前后写，并行节点内只读。节点内发现术语问题，记进 handoff 的
  `residual_risks` 与 `terminology_decisions`，不直接改表。
- 源 XML 与已生成 translated XML 共存时，节点 prompt 必须写死 `--xml` 指向的源文件
  路径（从 PROGRESS.md 抄），不许节点自行“挑一个像的”。
- 翻译 draft 只经 `fill_translations.py` / `fill_translation_set.py` 落盘；
  禁止 shell 重定向覆盖正式译文；禁止节点跑未经登记的新脚本（命中
  `skyrim-tool-dev-rules` §1.0 即违规）。

## 6. 完成与汇报口径

- 节点完成以 `references/handoff.md` 的结构化交接为准：`artifacts` + `verification`
  + `changed_scope` + `residual_risks` 四字段缺一即视为未交接，打回补交。
  自然语言 summary 只给人读，不作验收依据。
- 汇总时区分“已完成并验证”与“已派发未回”；引用节点返回的实际命令与 PASS/FAIL 事实。
  PASS 口径遵循 `skyrim-term-contract-workflow`；不把“任务派出去了”当“翻译完成了”。
- 未经 xTranslator 导入或实机运行的批次，一律声明“未作游戏实机验证”。

## 7. 演进

本 skill 随实测更新：新的派单陷阱、prompt 模板修订、角色职责变化记到
`references/`。它管“怎么用 Hana 原语组织这些活”；“翻译怎么做”仍归
`skyrim-translation-craft` 等管线 skill。
