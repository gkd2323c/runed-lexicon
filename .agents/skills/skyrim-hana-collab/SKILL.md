---
name: skyrim-hana-collab
description: runed-lexicon 在 HanaAgent 中的多 agent 翻译协作编排：用 subagent 与 workflow 把 MOD 翻译拆成准备、术语、试点、批量、门禁、写回六段流水线，含派单决策、前置链、并行安全与 handoff 验收。
compatibility: HanaAgent subagent / subagent_reply / subagent_close / workflow / wait_for_tasks 原语；复用 translation-context-builder / translation-executor / translation-quality-gate / xtranslator-xml-writer / term-contract-compiler / dictionary-noun-audit 现成管线，不自带可执行代码。
metadata:
  version: "0.2.0"
---

# Hana 多 Agent 翻译协作（runed-lexicon）

主会话负责编排，子 agent 负责执行。编排的价值：试点先行验证管线、
批量并行不串行等待、门禁失败精确打回原批次、主会话被掐断仍可凭 `.work/` 产物续跑。

## 1. 谁干什么

| 谁                      | 干什么                                                               | 落点                                                                                                                                          |
| ----------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Hanako（主会话，你）    | 拆批、派单、汇总；四文档门禁、契约编译、context 拼装、门禁与写回命令 | 主会话 inline（便宜且需全局视野，不外包）；只建任务与汇总，不在派单后插手译文措辞                                                             |
| Ming（术语考据）        | 官方词典证据、noun-audit 消解、DICTIONARY/GLOSSARY 维护              | 单个 `subagent`（access=write）                                                                                                               |
| Hanako / Butter（翻译） | INFO/DIAL/QUST/BOOK 翻译，产结构化 JSON                              | `workflow` 并行节点；剩余批次 ≤ 3 时用多个 `subagent` 并行；文学性强、语义与语境理解困难的批次派 Butter，其余默认 Hanako 同体（不指定 agent） |
| Ming（审校）            | quality-gate、复扫、抽审语义、写回前验证                             | 主会话或单个审校节点；写回永远单节点                                                                                                          |

分工原则：工程预处理归 Hanako；术语问题归 Ming；译文归翻译节点；质量关归审校。
拿不准派谁时不指定 `agent`，用默认体。

子 agent 有两档权限：`access="read"` 的节点会被平台拦截 `exec_command`，提权重试也
失败（实测结论），所以凡是需要跑命令验证的节点一律 `access="write"`；纯审阅、
只读抽查的节点才用 `access="read"` 且不声明写目录。

## 2. 标准任务图

```text
P0 准备（主会话 inline；四文档门禁 §2.2 + term-contract-compiler + context-builder + 切批）
 └→ P1 术语（单 subagent/write；noun-audit → 消解 → 重编译契约）
     └→ P2 试点（2 个试点批，并行；跑通 init → 翻译 → validate → handoff 全链路）
         └→ P3 批量（剩余批并行；批间互不依赖、不重叠）
             └→ P4 门禁复扫（审校；gate + noun-audit 复扫）
                 ├─ FAIL → 打回原批次节点（同 thread subagent_reply 或新建修复节点）→ 回 P4
                 └─ PASS → P5 确定性写回（单节点；审校验证，可重跑）
```

各阶段的依赖、批次粒度、试点放行条件见 `references/task-graph.md`。
自包含节点 prompt 模板见 `references/node-prompt-template.md`。
结构化 handoff 协议见 `references/handoff.md`。
超时、失败、越界恢复见 `references/failure-recovery.md`。

## 3. 派单决策（inline vs subagent vs workflow）

- **inline（主会话直接做）**：P0 全部；P4 门禁命令本身；P5 写回命令本身；任何单文件
  15 分钟内能做完的修复。不要为“看起来正式”而把一句话能做完的事包成子任务。
- **`subagent` 并行**：剩余批次 ≤ 3，或只需要术语 / 审校其中一类单活时。
  每个任务调一次 `subagent`，同一轮次内连续发出即并行；同时在跑的 subagent
  不超过 5 个（实测：10 并行打满触发模型限流，整批失败；慢就是快）。`label` 写清批次
  （如 `druadach-qo-04`），`task` 必须自包含（见节点 prompt 模板）。派单后不要轮询：
  有后续步骤依赖结果时调 `wait_for_tasks` 登记等待，系统会在任务到终态后叫醒你；
  无依赖时先做手头别的工作，结果经后台通道自动回送。同一实例续跑时用 `subagent_reply`（如门禁 FAIL 打回原批次），空闲实例及时 `subagent_close`。
- **`workflow` 确定性编排**：剩余批次 ≥ 4，或需要“全部并行 → 门禁汇总 → 条件写回”
  的刚性流水线时。调 `workflow` 工具，传 `script` 字符串（首行
  `export const meta = {...}`，用 `agent(prompt, opts)` 与 `parallel()` 组织扇出，
  详见节点模板的 workflow 侧写法）与 `limits`（`maxConcurrent` 不得超过 5，
  与 subagent 同限，超限触发模型限流；单节点超时默认 15 分钟）。运行失败会带回 `resumeFromRunId`，用它重派时已完成节点可从缓存直接复用。`workflow` 提交后同样用 `wait_for_tasks`
  等终态，不要轮询。

禁止事项：同一批同时用 subagent 和 workflow 各派一遍；把 P5 写回拆成并行；
让翻译节点顺手改 DICTIONARY（术语修订只走 P1 或门禁后的显式修复节点）。

## 4. 前置链（进 P2 前必须完成，缺一即停）

1. 四文档门禁（AGENTS.md §2.2 阶段 0~1）：GLOBAL、GLOSSARY、MOD 的 CONTEXT/DICTIONARY
   全量精读；缺任一即阻断问用户，不静默创建。
2. `skyrim-term-contract-workflow` 编译当前契约（含 `--global-bans`）。
3. `translation-context-builder` 产物已落盘，批次 scope 以 0-based `xml_index`
   区间严格界定（与 executor/writer 一致；`skyrim-xml-tools` 的 1-based 仅作对照，
   差 1）。同一批的 context 文件在整轮 P3 中保持不动：中途重建 context 会改变
   batch 划分，已派出的节点 scope 随即错位；必须重建时，新产物用新文件名，
   旧文件保留到本轮写回完成（result 的 provenance 指向它）。
4. P1 术语消解完成且契约已重编译。术语未收敛即开翻，等于批量制造返工。

## 5. 并行安全（workflow）

- 每个翻译节点只写自己的两件套：result JSON 与 map JSON。并行写节点必须声明
  互斥的 `writeFolders`（已存在的绝对目录；第一项同时成为节点 cwd，所以节点
  prompt 里的命令与文件路径一律用绝对路径写死，相对路径会解析到错误的 cwd）。
  最稳的是每批一子目录，例如 `.work/druadach-batches/qo-04/`，目录内固定放
  `translation.json` 与 `map.json`，两个节点永远不写同一个文件。
  文件名本身遵 AGENTS.md §2.0.1：无时间戳、无版本号后缀。
- `PROGRESS.md`、`DICTIONARY.md`、`GLOSSARY.md`、`global-forbidden-words.json`
  只由主会话在扇出前后写，并行节点内只读。节点内发现术语问题，记进 handoff 的
  `residual_risks` 与 `terminology_decisions`，不直接改表。
- 源 XML 与已生成 translated XML 共存时，节点 prompt 必须写死 `--xml` 指向的源文件
  路径（从 PROGRESS.md 抄），不许节点自行“挑一个像的”。
- 翻译 draft 只经 `fill_translations.py` / `fill_translation_set.py` 落盘；
  禁止 shell 重定向覆盖正式译文；禁止节点跑未经登记的新脚本（命中
  `skyrim-tool-dev-rules` §1.0 即违规）。
- P5 写回时 `--result` 用精确文件列表或收敛的 `--result-glob`；`.work/` 里若有
  同一批次的历史代次，先按 PROGRESS 登记的当前代次选定文件，不用宽泛通配符
  把过期代次卷进来。

## 6. 完成与汇报口径

- 节点完成以 `references/handoff.md` 的结构化交接为准：`artifacts` + `verification`
  + `changed_scope` + `residual_risks` 四字段缺一即视为未交接，打回补交。
自然语言 summary 只给人读，不作验收依据。
- 汇总时区分“已完成并验证”与“已派发未回”；引用节点返回的实际命令与 PASS/FAIL 事实。
  PASS 口径遵循 `skyrim-term-contract-workflow`；不把“任务派出去了”当“翻译完成了”。
- 未经 xTranslator 导入或实机运行的批次，汇总时在 PROGRESS 验证状态与写回报告里统一声明一次，不逐批复读。

## 7. 演进

本 skill 随实测更新：新的派单陷阱、prompt 模板修订、角色职责变化记到
`references/`。它管“怎么用 Hana 原语组织这些活”；“翻译怎么做”仍归
`skyrim-translation-craft` 等管线 skill。改动本 skill 后跑
`py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/skyrim-hana-collab`
确认结构有效。
