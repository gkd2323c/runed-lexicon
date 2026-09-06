---
name: skyrim-kanban-orchestration
description: skyrim-mod-translator 项目的 kanban 编排规范（⚠️ Hermes Agent 独有功能，非 Hermes 的 agent 读到请直接忽略、勿参照执行）：把 MOD 翻译目标拆成可执行卡片、按角色路由给 skyrim-terminologist / skyrim-translator / skyrim-reviewer profile、编写自包含的卡片 body、管理依赖与失败恢复。Use when orchestrating MOD 翻译工作 via kanban（拆卡、建卡、派卡、看进度、处理 worker timed_out/blocked）、给某个 MOD 的翻译流程规划 kanban 任务图、或需要标准化的接卡/审校/翻译批卡模板。
compatibility: Reference rules only; no scripts. 依赖 kanban CLI（hermes kanban ...）与项目 .agents/skills/ 各 pipeline skill。
metadata:
  version: "0.1.1"
---

# Kanban 编排规范（skyrim-mod-translator）

> ## ⚠️ 适用范围：仅 Hermes Agent
>
> **本 skill 描述的是 Hermes Agent 的独有功能**（`hermes kanban` CLI、Hermes profile 系统、gateway 内建 dispatcher、`dir:` 工作区 worker）。它只对运行在 Hermes Agent 中的会话有效。
>
> **非 Hermes Agent（Claude Code、Codex、Cursor、其他 AI 助手或编码代理）读到本 skill 时：立即停止，不要试图参照执行。** 你的环境里没有 `hermes kanban` 命令、没有 `skyrim-terminologist` 等 Hermes profile、没有 kanban dispatcher——按本文建卡、派卡、用 `--goal`/`--parent` 等任何操作都会失败或产生无意义动作。正确的做法是：忽略本 skill，改用你能执行的普通文件/命令工具完成翻译任务，并遵守项目 `AGENTS.md` 与其它 pipeline skill。

用 Hermes kanban 把 MOD 翻译工作拆成卡片、派给专职 profile 执行。kanban 的价值：卡片状态存 SQLite、由独立 worker 进程执行、主会话被掐断/超限不丢进度、失败自动重试/可恢复。

## 1. 团队 profile（assignee 路由）

| profile | 职责 | 接卡类型 |
|---|---|---|
| `skyrim-orchestrator` | 拆卡、建卡、汇总；不亲自翻译 | 规划/汇总类 |
| `skyrim-prep-engineer` | xEdit 导出、context 拼装、批次切割、本地首翻驱动、工具维修；不做语义翻译与术语裁决 | 预处理/工程类 |
| `skyrim-terminologist` | 官方词典证据、名词契约、术语消解、DICTIONARY/GLOSSARY 维护；附带新 MOD 的 CONTEXT.md 首建 | 审计/术语类 |
| `skyrim-translator` | INFO/DIAL/QUST/BOOK 文本翻译，产出结构化 JSON | 翻译批 |
| `skyrim-reviewer` | quality-gate、复扫、抽审语义、写回前验证；写回卡独立可重跑 | 审校门禁 |

分工原则：工程预处理归 prep-engineer；术语问题归 terminologist；译文归 translator；质量关归 reviewer。orchestrator 只在建卡与汇总时用 default 兼任也可。

**orchestrator 工具集限制（已落地）**：`skyrim-orchestrator` profile 的 config.yaml 设 `toolsets: [kanban, memory, file, skills, session_search, todo]`——有 kanban（建卡/派卡/看板）+ file/skills（读项目状态与规则）+ memory，但**没有 terminal / execute_code / delegate_task / web / browser**，物理上无法执行翻译/审计活，只能编排。文档建议的 kanban/gateway/memory 三件套里没有独立 `gateway` 工具集，实际按上面的最小集配置即可（需要读项目文档时 file/skills 必须保留，否则 orchestrator 连看状态都做不到）。

## 2. 标准任务图（MOD 翻译）

```
T0 准备/文档初稿（四文档前置，可 default 或 orchestrator）
 └→ T1 文档审阅修订（terminologist；DICTIONARY/CONTEXT 收敛，附带 CONTEXT 首建）
     └→ T2 官方名词契约审计（terminologist；noun-audit → 消解）
         └→ T2.5 工程预处理（prep-engineer；xEdit 导出 → context 拼装 → 切批 → 可选本地首翻）
             └→ T3.x 翻译批（translator；按批次并行，互不依赖）
                 └→ T4 质量门禁 + 复扫（reviewer；gate + audit 复扫）
                     ├─ FAIL → T4.x 修复卡（派回原 implementer；同卡 request-changes 或新建修复卡）→ 回 T4 复扫
                     └─ PASS → T5 确定性写回（独立卡；reviewer 验证，可重跑）
```

依赖用 `--parent` 表达：child 在 parents 全部 done 前保持 todo，自动转 ready。
**能并行的批（T3.x 之间）不要互相 link。** 每批范围用 xml_index 区间或 Source 清单严格界定，不重叠。

**试点卡硬性前置**：T2.5 之后先建 2 张试点翻译卡（每卡 2 batch，约 40 条），跑通 init → 翻译 → validate → 结构化 handoff 全链路。试点都 done 且门禁抽查无系统性问题，才允许滚动建剩余批次卡。试点暴露批次质量问题（context 缺字段、切分错位、术语未绑定）→ 打回 T2.5 修，不建新卡掩盖。

**批次粒度**：默认每卡 2 batch（约 40 条）。rest 线实测 80–100 条/卡可走通（R3 系列），新 MOD 允许试点通过后放宽到 3 batch/卡，但单卡不得超过 goal 预算内能完成并自验的量。

使用 `translation-context-builder` 产物时，以 context batch 作为最小可追踪单元。新卡建议使用 `--goal --goal-max-turns 60`（试点与粒度执行 §2 硬性条款，不重复）。

## 2.5 分板规则（一 MOD 一板）

- 新 MOD 开工先建板：`hermes kanban boards create <mod-slug>`（如 `dg04bjornfollower`），之后该 MOD 全部卡建在新板上（`--board` 或先 `boards switch`）。
- Board 是独立 DB + 独立 dispatcher，旧板已完成卡迁不过去——MOD 收口（R8/PROGRESS 哈希链）完成后，`boards export` 打包到 `.work/board-archives/` 留档；default 常驻板不删，新 MOD 直接建新板切过去，日常 `list` 只看当前板。
- 跨 MOD 事项（工具维修 B3、全局词库更新）建在当前活跃 MOD 板，或单独 `infra` 板，不散落 default 板。

## 3. 建卡要点（worker 是全新进程，body 必须自包含）

每张卡 body 按此结构写（模板见 `references/card-templates.md`）：

1. **任务一句话**：做什么、产出什么。
2. **先读规则（read_file 路径）**：项目根 `AGENTS.md`、`GLOBAL.md`、`GLOSSARY.md`、相关 `.agents/skills/<名>/SKILL.md`、MOD 的 `CONTEXT.md`/`DICTIONARY.md`/`PROGRESS.md`。⚠️ 不要用 `--skill` 强制加载——项目 skill 不在 Hermes 技能库，`--skill` 会让 worker 启动即崩（Unknown skill）。worker 的 cwd 就是 `dir:` 工作区，能直接 read_file。
3. **项目背景**：项目根路径、MOD 目录、源 XML、规模、当前阶段。
4. **任务范围**：明确到文件/批次/xml_index 区间/Source 清单，不模糊。
5. **工具命令**：精确到 `python .agents/skills/.../xxx.py <args>`，产物路径写死（`.work/` 下）。
6. **产物与格式**：写什么文件、什么结构（遵循对应 pipeline skill，不自创格式）。
7. **完成标准 + 验证方式**：可检查（文件存在、计数、PASS 报告等）。
8. **边界**：不改什么（XML/其它 MOD/文档之外）、不越权做什么。

## 4. 关键 CLI

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
hermes kanban create "<标题>" --assignee <profile> --workspace "dir:<PROJECT_ROOT>" --parent <parent_id> --body "<自包含 body>" --json
hermes kanban list          # 看板
hermes kanban show <id>     # 单卡状态+事件
hermes kanban runs <id>     # 执行历史（timed_out/crashed/...）
hermes kanban log <id>      # worker 日志（排障）
hermes kanban archive <id>  # 归档（重建前）
hermes kanban reclaim <id>  # 释放卡（worker 卡死时）
hermes kanban comment <id> --body "..."   # 追加说明
```

## 4.5 结构化 handoff（worker 完成卡时的硬性交接协议）

**为什么**：orchestrator 不该读懂你的工作成果才能验收。它只核协议。你完成卡（`kanban_complete`，或同卡 `kanban_request_review`）时，`metadata` 必须返回统一结构，让下游（orchestrator / 下一张卡 / 人）不用猜：

```json
{
  "artifacts": ["mods/xxx.esp/xxx_translated.xml", ".work/xxx-batch1-result.json"],
  "verification": {"command": "py -3 .agents/skills/translation-quality-gate/scripts/quality_gate.py ...", "result": "PASS"},
  "changed_scope": ["xml_index 120-180", "mods/xxx.esp/DICTIONARY.md（+2 术语）"],
  "residual_risks": ["未做 xTranslator 导入验证", "none"]
}
```

字段约定（来自 Hermes kanban 文档推荐的 handoff 形状，按本项目调整）：
- `artifacts`: 本卡产出的文件路径（相对项目根）。orchestrator 会 stat 确认存在。
- `verification`: 你实际跑过的验证命令 + 结果（PASS/FAIL/N/A）。结果必须与卡 body 的完成标准一致；body 要求 PASS 就别交 FAIL 或省略。
- `changed_scope`: 你改动的范围（xml_index 区间 / 文件 / 记录类型），便于查越界。
- `residual_risks`: 未验证/存疑/影响下游的事项；没有就写 `["none"]`。有风险必须明示，不许吞。

禁止只写自然语言 summary 就 complete（如"已完成，整体看起来没问题"）——summary 给人读，metadata 给机器/下游核。缺 metadata 或结构不全 = 交接不完整，orchestrator 会打回补交。summary 与 metadata 都写：summary 一句话说人话，metadata 给结构化证据。

## 5. 已知陷阱（实测 + 官方文档核对）

- **`--skill <项目skill>` 会崩**：`--skill` 只能加载 **assignee profile 已安装的 skill**（`~/.hermes/skills/` 或该 profile 自带 skills），项目 `.agents/skills/` 下的不算已安装 → worker 按名解析失败 → `Unknown skill(s)` → spawn 即 crash → 连续失败自动 blocked。正确做法：body 里让 worker read_file 项目 skill。若确需 `--skill` 加载项目 skill，得先把它装进目标 profile 的技能库（会脱离项目版本管理，通常不值）。
- **普通 worker 50 轮迭代上限**：大任务（通读 XML+审阅长文档、全量名词审计）会 `timed_out (50/50)` 然后**重头再来**，浪费已读上下文。⚠️ 但 worker 重试时会读到**前一次 run 的产物**（runs 表），聪明的 worker 会接着验证而非重做——所以让每张卡把中间产物写盘（`.work/`）很重要，重试可续。
- **`--goal` 模式可续跑**：goal worker 每轮由 aux judge 判完成与否，未完成同一 session 继续；预算 `--goal-max-turns`（默认 20，可调大）。预算耗尽会 **sticky block**（转 blocked 等人工 unblock），不是静默退出。适合"一次做不完但要连贯"的活（文档审阅、全量审计、大批翻译）。⚠️ 只能新建卡时指定，不能对已有卡 edit 追加。judge 质量取决于 body 写得是否像验收标准。
- **任务太大就拆小**：与其让 worker 盲目续跑，不如把卡拆到"单 worker 一轮能完成并自验"的粒度（按 REC 类型/任务分支/xml_index 区间分批）。
- **同卡 review 优先于新建审校卡**：worker 完成实现后可 `kanban_request_review`（task → review，不走 block）；reviewer 用 `kanban_request_changes` 把卡退回原 implementer（不走 block-loop 计数）。只有需要不同 profile 深度审校/复扫时才新建独立审校卡。
- **新 profile 即建即用**：dispatcher 用 `profile_exists()` 实时认 assignee，无需重启。
- **新 profile 要配齐**：`config.yaml` 需含 `custom_providers`（从 default 复制）与 `skills.trusted_project_dirs`，否则 worker 无凭证/看不到项目 skill。
- **完成事件会唤醒创建者**：`kanban.auto_subscribe_on_create` 默认 true——卡完成/blocked 时会 resume 创建它的会话（合成状态轮），所以建卡后注意可能收到完成通知。
- **worker 撞 50 轮 vs goal 预算**：普通 50 轮 timed_out 会 retry（重头，但能读上次产物）；goal 预算耗尽会 sticky block（需人工 unblock 续），两者语义不同。
- **worker 用 `kanban_*` 工具而非 CLI**：dispatcher 注入 `HERMES_KANBAN_TASK` 环境变量激活 worker 的 kanban 工具集（kanban_show/complete/block/heartbeat/...）。orchestrator profile 可在 toolsets 里开 `kanban` 工具集；文档建议 orchestrator 只留 kanban/gateway/memory 工具集，物理上防止它手痒执行实现活。

## 6. 汇报口径

- 汇总时区分"已完成并验证"与"已派发未回"；引用 `kanban runs`/`log` 事实。
- PASS 口径遵循 `skyrim-term-contract-workflow`；不把"卡派出去"当"翻译完成"。
- 卡片失败先看 log 归类（配置/模型/范围/预算），修因不重试表象。

## 7. 演进

本 skill 随实测更新：新陷阱、新模板、profile 职责变化都记到这里（或 references/）。与 `skyrim-doc-system`、`skyrim-term-contract-workflow` 等 pipeline skill 互补：它们管"翻译怎么做"，本 skill 管"怎么用 kanban 组织这些活"。
