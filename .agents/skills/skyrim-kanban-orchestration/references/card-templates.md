# 接卡模板库

每类卡一张模板。使用时把 `<占位符>` 替换为实际值，按需增删小节。
所有模板遵循：worker 是全新进程、body 自包含、cwd=项目根（dir: 工作区）、**不用 `--skill` 加载项目 skill**（会让 worker 崩）、规则用 read_file 路径指路。

建卡命令骨架：

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
hermes kanban create "<标题>" \
  --assignee <profile> \
  --workspace "dir:<PROJECT_ROOT>" \
  [--parent <id>] \
  [--goal] [--goal-max-turns 60] \
  --body "$(cat <<'EOF'
...body...
EOF
)"
```

---

## 模板 A：文档审阅修订（T1 类）

> assignee: `skyrim-terminologist`（或 default）。大文档审阅建议 `--goal`，否则 50 轮易 timed_out。

```text
任务：审阅并修订 <MOD 目录> 的翻译准备文档初稿（CONTEXT.md / DICTIONARY.md），收敛不确定项，产出可开工的定稿文档。

## 第一步：先读规则（不要跳过，直接 read_file 这些路径）
1. AGENTS.md（项目根）——尤其 '2.1 四文档前置'、'A. 规则 skill 与强制加载'、'5. 默认边界'
2. GLOBAL.md、GLOSSARY.md（项目根）
3. .agents/skills/skyrim-doc-system/SKILL.md —— 本任务核心（五文档职责、术语状态体系 CONFIRMED/PROVISIONAL/REVIEW/KEEP）
4. .agents/skills/skyrim-translation-craft/SKILL.md（理解 MOD 语境）
5. .agents/skills/skyrim-xml-tools/SKILL.md（官方词典查询）

## 项目背景
- 项目根 = 你的工作区 = <项目根>
- MOD：<mods/xxx.esp/>
- 源 XML：<mods/xxx.esp/xxx_english_chinese.xml>（<N> String）
- 官方词典：项目 dictionary/ 目录

## 待审阅文件（直接读写）
- mods/<xxx>.esp/CONTEXT.md
- mods/<xxx>.esp/DICTIONARY.md

## 要做的事
A. 通读两份初稿，对照 XML 与官方词典检查错误/遗漏/自相矛盾。词典查询：
   python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup '<term>' --ignore-case
B. DICTIONARY.md 收敛：REVIEW 项逐条 lookup 定稿；修与官方冲突音译；统一译名；删重；保留 KEEP 清单。
C. CONTEXT.md 收敛：补漏关键剧情/称谓；修与 XML 不符描述；保留未验证声明。
D. 更新文件头部状态行，列修订要点。

## 完成标准
- 两文档无自相矛盾、无悬空 REVIEW（确需 REVIEW 的列明原因）
- 术语与官方词典一致（lookup 抽查 ≥5）
- 不修改 XML / BJORN.md；不新建翻译结果
- summary 列：修订要点、仍存疑项、确认术语数量

## 注意
- cwd 即项目根，相对路径直接可用；不要动其它 MOD 目录
- 产出是'可开工的定稿文档'，不是翻译本身
```

---

## 模板 B：官方名词契约审计（T2 类）

> assignee: `skyrim-terminologist`。依赖 T1（文档定稿）时加 `--parent <T1_id>`。

```text
任务：对 <MOD> 做官方名词契约候选审计（dictionary-noun-audit candidate discovery），为正式翻译铺路。依赖 <T1>（文档定稿）。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根）
2. .agents/skills/skyrim-term-contract-workflow/SKILL.md —— 本任务核心（八步流程、PASS 口径）
3. .agents/skills/dictionary-noun-audit/SKILL.md（候选发现器用法）
4. .agents/skills/skyrim-doc-system/SKILL.md（术语状态）
5. GLOBAL.md、GLOSSARY.md

## 项目背景
- 项目根 = 工作区 = <项目根>
- MOD：<mods/xxx.esp/>；源 XML：<路径>（<N> String，几乎全未译）
- 术语文档（T1 定稿，先读）：CONTEXT.md、DICTIONARY.md

## 要做的事（按 term-contract-workflow 开工前置第 2 步，不越级翻译）
1. 对源 XML 跑 noun-audit：
   py -3 .agents/skills/dictionary-noun-audit/scripts/dictionary_noun_audit.py <xml路径> --json .work/<mod>-noun-audit-baseline.json --limit 0
2. 候选按 DICTIONARY.md 逐条消解，给出判定（真误译需改/合法回指/语境别称/官方覆盖等）
3. 产物：baseline JSON（工具生成）+ .work/<mod>-noun-audit-review.json（你的逐条判定）
4. 不编译契约（留到正式翻译前）；如可提前，summary 说明理由

## 完成标准
- baseline 已生成；候选逐条有可追溯判定
- 发现 DICTIONARY 漏项/冲突→小幅修订 DICTIONARY.md 并在 summary 说明
- 不改 XML；不生成翻译结果
- summary：候选总数、判定分布、翻译时需注意的高风险名词

## 注意
- cwd 即项目根；产物写 .work/ 下
- 你是审计/消解，不是翻译器：不产出中文译文 JSON
```

---

## 模板 B2：工程预处理（T2.5 类）

> assignee: `skyrim-prep-engineer`。依赖 T2（术语定稿）时加 `--parent <T2_id>`。翻译批（T3.x）依赖本卡的批次产物。

```text
任务：对 <MOD> 做工程预处理——xEdit 对话结构导出 → translation-context 拼装 → 批次切割（→ 可选本地模型首翻），产出译者可直接开工的批次。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根，尤其 '2.0 输出契约'、'2.1 执行衛生'、'3. XML 安全约束'）
2. .agents/skills/skyrim-doc-system/SKILL.md（目录落点与命名契约）
3. .agents/skills/xedit-context-exporter/SKILL.md（对话结构导出，本任务按需）
4. .agents/skills/translation-context-builder/SKILL.md（context 拼装，本任务核心）
5. .agents/skills/translation-batch-preparer/SKILL.md（批次切割，本任务核心）
6. .agents/skills/local-model-translator/SKILL.md（仅当卡片要求本地首翻时）
7. mods/<xxx>.esp/CONTEXT.md、DICTIONARY.md（T2 定稿，只读作约束，不修改）

## 项目背景
- 项目根 = 工作区 = <项目根>
- MOD：<mods/xxx.esp/>；源 XML：<路径>（<N> String）
- 术语已定稿（DICTIONARY.md）；对话上下文 JSON：<有则给路径，无则本卡导出>

## 要做的事
1. （对话密集型 MOD）跑 xEdit 导出：
   py -3 .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py <plugin路径> [--output <路径>]
   只读解析，不改源插件。
2. 跑 context-builder 拼装译者 context JSON（产物 .work/<mod>-*-context.json）：
   py -3 .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/<xxx>.esp --rec <REC类型> --limit <N> --output .work/<mod>-context.json
   注意 xml_index 基准：builder 产出 0-based，skyrim-xml-tools 查询 1-based，跨工具对照先确认基准。
3. 按 translation-batch-preparer 切分翻译批，批次落 .work/ 下，命名守确定性输出契约（无日期戳、无 vN、固定角色名）。
4. （仅卡片明确要求时）按 local-model-translator 驱动 Ollama 首翻：喂紧凑约束、校验 ID/顺序/占位符/术语绑定。首翻是工人输出，不当定稿。
5. 在 PROGRESS.md 登记本卡产出的 context/批次路径（只登记，不改事实内容）。

## 完成标准
- context JSON 与批次文件存在，条数与源范围一致
- 批次命名符合输出契约；历史产物进 .work/<plugin>-archive/
- 不改 DICTIONARY.md / CONTEXT.md / XML；不定术语；不做语义翻译
- `kanban_complete` 的 `metadata` 含 artifacts、verification、changed_scope、residual_risks 四键；首翻未经语义审校必须写进 residual_risks
- summary：context 条数、切批数量与粒度、首翻情况（如有）、遗留风险
```

---

## 模板 B3：工具维修（按需）

> assignee: `skyrim-prep-engineer`。流水线脚本报错、门禁工具行为异常时建卡。

```text
任务：修复 <工具/脚本路径> 的 <故障现象>，恢复流水线可用。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根，尤其 '2.1 执行衛生'、'5. 默认边界'、'A. 规则技能'）
2. .agents/skills/skyrim-tool-dev-rules/SKILL.md（本任务核心：工具复用检索、性能门禁、禁止临时代码）
3. 故障工具自己的 SKILL.md 与脚本源码

## 要做的事
1. 先检索 .agents/skills/ 内现成工具，确认不是重复造轮子
2. 复现故障，定位根因（贴报错命令与输出）
3. 修复，跑回归验证（性能门禁：30s 缺陷阈值 / 5s 回归线）
4. 不落盘一次性脚本；修完更新对应 SKILL.md 的陷阱记录（如有）

## 完成标准
- 故障复现命令现在通过；回归验证有输出依据
- summary：根因一句话、改了什么、回归结论
```

---

## 模板 C：翻译批（T3.x 类）

> assignee: `skyrim-translator`。依赖预处理批次产出（T2.5）时加 `--parent <T2.5_id>`。每批独立卡、范围不重叠，批间不互 link。
>
> **实测默认粒度**：每卡 2 个 `translation-context-builder` context batch（通常约 40 条）；先建 2 张试点卡跑通 init → 翻译 → validate → 结构化 handoff，再按小批滚动建卡。建议新卡使用 `--goal --goal-max-turns 60`；若文本复杂度不同，以单 worker 能在预算内完成并自验为准，不强行固定条数。

```text
任务：翻译 <MOD> 的第 <N> 批文本（xml_index <a>-<b> / REC <类型> / 任务 <分支>），产出结构化翻译 JSON。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根）
2. GLOBAL.md、GLOSSARY.md
3. mods/<xxx>.esp/CONTEXT.md、DICTIONARY.md（四文档前置，全量精读）
4. mods/<xxx>.esp/PROGRESS.md（当前有效批次、产物与验证状态）
5. .agents/skills/skyrim-translation-craft/SKILL.md（翻译工艺）
6. .agents/skills/translation-executor/SKILL.md（结构化结果格式、fill_translations.py --by-source 用法）
7. .agents/skills/skyrim-xml-verification/SKILL.md（XML 安全、写回前验证）
8. .agents/skills/skyrim-xml-tools/SKILL.md（untranslated 提取、lookup）

## 项目背景
- 项目根 = 工作区 = <项目根>
- MOD：<mods/xxx.esp/>；源 XML：<路径>
- 术语已定稿（DICTIONARY.md）；本批范围：xml_index <a>-<b>（0-based，见 context JSON）/context batch <b0>-<b1>
- 若使用 context-builder JSON，先确认 `inputs`、context hash、批次索引和条目数量与本卡范围一致。

## 要做的事
1. 用现成工具取本批 Source（参考 translation-batch-preparer / skyrim_xml_tools untranslated），确认范围与计数；若已有 context JSON，按 `translation_unit_id` 对齐，不凭手抄序号。
2. 翻译前先通读本批所在的连续语义单元（任务/对话分支/书信全文），理解说话者与语境，再逐条落笔；结构证据缺失时保留"推测/未验证"边界。context 缺失或批次范围可疑时不要自己重跑 builder——那是 prep-engineer 的活，blocked 上报 orchestrator 改派 T2.5 卡。
3. 初始化并填写一个 batch 一个 result JSON，例如：
   py -3 .agents/skills/translation-executor/scripts/translation_result.py init <context-json> --batch <b0> --output .work/<mod>-translation-<b0:03d>.json [--force]
   py -3 .agents/skills/translation-executor/scripts/translation_result.py init <context-json> --batch <b1> --output .work/<mod>-translation-<b1:03d>.json [--force]
4. 按 `translation-executor` 规范逐条填写结果。初始化器复制的不可变字段必须保持不变；翻译字段为 `translation`、`status`、`confidence`、`notes`、`terminology_decisions`，每个 `translation_unit_id` 恰好一条。
5. 占位符/技术串按不可翻译处理并备注；专名查 DICTIONARY.md，未覆盖的给暂定译名并备注（交 terminologist 定稿）。
6. 对每个 result 执行 validate，再执行 summary；所有验证命令必须真实运行并保留输出依据。

## 完成标准
- result JSON 存在，条数与选定 context batch 完全一致，且无 `PENDING`；每条有译文或明确 `KEEP`/备注
- 占位符（<Alias=...>/<Global=...>/%s/<dur>/===XXX=== 等）原样保留
- `translation_result.py validate` 对本卡全部 result 返回 valid；warning 必须在 handoff 的 residual_risks 中说明
- 不改 XML（写回是后续 reviewer/writer 的事），不改 CONTEXT/DICTIONARY/PROGRESS 的事实内容
- `kanban_complete` 的 `metadata` 必须包含 `artifacts`、`verification`、`changed_scope`、`residual_risks` 四个键；`artifacts` 列出全部 result/map/report 文件，`verification` 给真实命令和 PASS/FAIL/N/A，`changed_scope` 给实际范围，`residual_risks` 明示未验证事项（没有就写 `["none"]`）
- summary：本批条数、TRANSLATED/KEEP/REVIEW 分布、新术语暂定清单、验证结论

## 注意
- 只处理本批范围，不越批；不改 DICTIONARY.md（新术语记备注即可）
- 口吻/一致性/剧透边界按 skyrim-translation-craft
- `TRANSLATED`/`validate` 通过不等于语义审校、xTranslator 导入或游戏内测试已完成；这些状态分别交给 T4/T5 记录
```

---

## 模板 D：质量门禁 + 复扫（T4 类）

> assignee: `skyrim-reviewer`。依赖对应翻译批（T3.x）done。

```text
任务：对 <MOD> 翻译批结果跑质量门禁（translation-quality-gate）+ 官方名词复扫（dictionary-noun-audit），输出审校报告。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根）
2. .agents/skills/skyrim-term-contract-workflow/SKILL.md（PASS 口径）
3. .agents/skills/translation-quality-gate/SKILL.md（TERM/PLACEHOLDER/CHAR/XML 检查）
4. .agents/skills/dictionary-noun-audit/SKILL.md（复扫）
5. .agents/skills/skyrim-xml-verification/SKILL.md
6. mods/<xxx>.esp/CONTEXT.md、DICTIONARY.md

## 要做的事
1. 对翻译批 result JSON 跑 quality-gate（命令见其 SKILL.md），得到 PASS/FAIL 报告
2. FAIL 项逐条判定：真错→给修改意见（哪条、为什么、怎么改）；误报→说明原因
3. 对新译文本跑 noun-audit 复扫，确认无官方名词漏译
4. 抽审语义：口吻一致性、剧透边界、指代（对照 CONTEXT.md）
5. 产物：.work/<mod>-gate-<batch>.json（工具报告）+ .work/<mod>-review-<batch>.md（审校意见，含修改清单）

## 完成标准
- gate 报告已生成；FAIL/CHECK 逐条有判定；修改清单可执行
- 不直接改译文 JSON 除非卡片明确授权；不改 XML
- summary：PASS/FAIL 统计、修改清单条数、高风险项

## 注意
- 工程验证与语义审校分开声明；没验证的不说已验证
```

---

## 模板 E：确定性写回（T5 类）

> assignee: `skyrim-reviewer`（或 default）。依赖门禁 PASS。写回前必读 xtranslator-xml-writer 与 skyrim-xml-verification。

```text
任务：把 <MOD> 已过门禁的翻译结果经 xtranslator-xml-writer 确定性写回为新的 translated XML。

## 第一步：先读规则（read_file）
1. AGENTS.md（项目根，尤其 '3. XML 安全约束'）
2. .agents/skills/xtranslator-xml-writer/SKILL.md（写回工具，必读）
3. .agents/skills/skyrim-xml-verification/SKILL.md（写回前后验证清单）

## 要做的事
1. 确认翻译结果已过 quality-gate（看 .work/<mod>-gate-*.json 报告）
2. 按 xtranslator-xml-writer SKILL.md 的命令与参数执行写回，生成 <mod>_english_chinese_translated.xml（新文件，不覆盖原 XML）
3. 写回后验证：工具自带验证 + 必要时对照哈希/审计，确认只改了目标 <Dest>

## 完成标准
- translated XML 已生成；写回报告（条数、落点、验证结果）写入 .work/<mod>-writeback-report.json
- 未做 xTranslator 实际导入/游戏内测试则明确声明未做
- summary：写回条数、验证结果、遗留风险
```

---

## 附：同卡 review 轻量流程（替代独立审校卡）

当审校只需"同一实现者返工"、不需要另一个 profile 深度复扫时，用同卡 review，不新建卡：

```text
# 实现 worker 完成时（而非 kanban_complete）：
kanban_request_review(summary="...", metadata={...})   # task → review 状态
# 或 CLI：
hermes kanban request-review <id> --summary "..." [--reviewer <profile>]

# reviewer（被指定的 profile，或人工）审完：
#   通过 → 由 reviewer / 人工 complete
#   需修改 → 退回原 implementer（不走 block-loop 计数）：
hermes kanban request-changes <id> "需要修改：<具体意见>"
#   或工具 kanban_request_changes(reason="...")
```

什么时候用同卡 review、什么时候新建审校卡（模板 D）：
- **同卡 review**：小范围、同一 profile 能改、审校意见可直接退回原 worker 返工。
- **独立审校卡（模板 D）**：需要不同 profile（skyrim-reviewer）独立跑 quality-gate + 复扫 + 抽审、且要保留独立审计产物的场景——翻译质量门禁通常走这个，因为 gate/复扫产物要落盘、且 reviewer 与 translator 职责分离是项目要求。

---

## 附：卡状态流转速查

- `ready` → dispatcher 自动 claim → `running` → worker `kanban_complete` → `done`
- 失败：`timed_out`（50 轮超限，自动 retry 重头）/ `crashed`（spawn 即崩，自动 retry）/ 连续失败 → `blocked`（需人工）
- `todo` = 有未 done 的 parent；parents 全 done 自动转 `ready`
- goal 卡预算耗尽 → sticky block（需 `hermes kanban unblock <id>` 续）
- 排障：`kanban runs <id>`（看 outcome）、`kanban log <id>`（看 worker 在干嘛/为何崩）
