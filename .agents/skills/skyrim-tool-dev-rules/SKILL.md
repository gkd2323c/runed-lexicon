---
name: skyrim-tool-dev-rules
description: runed-lexicon 的工具开发规范：先检索复用 `.agents/skills/` 现成 skill、禁止重复实现现成能力，确实需要新工具时的开发方式，SKILL.md 配套强制规则、skill-creator 流程、工具性能门禁（30s 缺陷阈值 / 5s 基线回归线 / cProfile 先行 / 输出逐字节一致验收）。Use when about to write any script/CLI/tool or inline command that reads/statistics/queries/converts/validates/writes MOD data（先查现成 skill），or when creating a new skill, modifying existing pipeline tools, claiming tool work complete, optimizing tool performance, or declaring a tool deliverable without SKILL.md.
compatibility: Reference rules only; no scripts. References skill-creator workflow.
metadata:
  version: "1.1.0"
---

# 工具开发规范

## 1. 开发工作方式

### 1.0 先检索复用，再考虑新造

需要任何程序化操作（读取、统计、查询词典、转换、校验、写回 MOD 数据）时，默认动作是**先检索 `.agents/skills/` 下是否已有覆盖该能力的 skill**，命中即按其 SKILL.md 使用。以下情况都属于重复实现：

- 已有现成 skill/脚本，却另写一次性脚本或内联命令重复实现同一功能；
- 因为「数据量小 / 目录结构特殊 / 走完整管线觉得重」而自认为可以跳过现成工具——工具多为秒级，规模假设不成立时以实测为准，不以感觉为准；
- 把一次性的内联 `python -c`、shell 管道、临时脚本当作不违反本规则的「小工具」——只要它在做现成 skill 能做的事，就同属重复实现。

新增 skill 前必须能回答：现有 skill 各自不能完成我需要的哪一步？这一步为什么必须自动化？

### 1.1 确实需要新工具时的开发方式

编写脚本或工具前：先检查实际 XML 样本和项目结构，确认要解决的具体问题，优先写最小可验证实现，优先复用标准库和成熟 XML 解析能力，不为了"架构完整"提前引入不必要的框架、数据库或服务。

开发活动服务于实际翻译需求。除非现有工具确实阻碍了文本阅读、上下文恢复、可靠写回或验证，否则不因为"可以自动化"就中断翻译去扩展脚本、重构流水线或增加中间层。衡量推进是否有效，优先看是否读懂更多原文、是否解决真实语义歧义、是否提高译文质量与一致性、是否安全完成更多实际译文；脚本数量、中间文件数、哈希重算次数、流水线复杂度不是项目进展本身。

新工具交付时按 §3 配套 `SKILL.md`，并把「已有现成工具时禁止重复实现」这一点写清楚，避免后续 Agent 在新工具与旧工具之间另起炉灶。

## 2. 工具性能门禁

> 工具的性能问题也是门禁问题。验证工具慢到令人想跳过时，人就会跳过验证；"先跳过这次 gate"是比慢本身更严重的事故源头。

适用对象：`.agents/skills/` 下所有会被反复批量执行的确定性脚本（gate、writer、审计、编译器等）。一次性小工具不受此约束。

阈值（以 MVF1 规模为参照：约 9400 String 节点 / 8500 翻译单元）：

1. **单次完整调用超过 30 秒即视为缺陷**，先 profile 定位热点再修复；禁止凭猜测优化。
2. **流水线关键路径（gate → writeback → 复扫）合计保持秒级**。当前基线：gate 0.89s（MVF1 规模 8555 单元）、gate 4.1s（Druadach 规模 20634 单元 / 849 条全局禁词）、writeback 0.43s、noun-audit 2.1s、contract-compiler 0.15s；任何新工具或改动使某环节超过 5s 都应说明理由或优化。
3. 修复性能缺陷前先用 `cProfile` 拆账并记录热点；修复后证明**输出逐字节一致**（写回类）或**报告逐项一致**（检查类），否则视为行为变更，走完整回归。

已知反面模式（本项目实际踩过，写新代码时避免重犯）：

- 在循环内重建文档级索引或重复解析整份 XML（gate 的 XML001 曾对每个 unit 重扫全文档，8555 × 9402，实测 160s；索引应在循环外构建一次）。
- 在循环内用切片拼接重建整个文档字符串（writer 曾对每个替换点重建全文档，O(替换数 × 文档大小)，实测 21s；应按排序 span 单次 join 拼装）。
- **在 unit × term 双层循环内重复编译同一个正则**（gate 的 term_match 曾对每个 unit × 每个 term 重新 `re.compile`，Druadach 规模实测 2200 万次、总耗时 26.7s；编译结果应按 needle 缓存，并先用 `in` 字面预筛再进正则）。
- **在 unit × ban 双层循环内先跑英文锚点正则再查中文坏形态**（顺序反了：先用 `forbidden in dest` 的 C 级子串检查做预筛，能把 849 × 20634 的调用量降一个量级；生成器表达式 `any(...)` 在千万次量级上帧开销可观，改显式循环）。

改动性能敏感代码后的最低验证链：

```text
# 拆账
py -3 -m cProfile -s tottime <script.py> <args>
# 写回类：前后输出哈希必须一致
Get-FileHash <output-before> , <output-after> -Algorithm SHA256
# 检查类：报告逐项对比；语料自测必须全过
python .agents/skills/translation-quality-gate/scripts/selftest_corpus.py
```

这里的 `python` 表示满足对应 Skill `compatibility` 的 Python 解释器；不要把
某台开发机的绝对 Python 安装路径写进项目级规范。若检查项依赖可选包（例如
quality gate 的 `zhconv`），使用实际具备该依赖的解释器。

性能基线数字与修复记录写入对应 MOD 的 `PROGRESS.md`（AGENTS.md 不保存一次性任务状态）；新增工具交付时在 `SKILL.md` 的 compatibility 或验证小节注明实测耗时基线，供后续回归对照。

## 3. 脚本 / 程序与 SKILL.md 配套规则

项目中创建的任何脚本、CLI、程序、自动化工具或可重复执行的辅助工具，都必须同时提供配套的 `SKILL.md`，遵循 Agent Skills 规范（https://agentskills.io/specification），不能只创建一个普通 Markdown 说明文件冒充 Skill。

创建新 Skill 或对现有 Skill 做实质性修改前，先完整读取 `.agents/skills/skill-creator/SKILL.md`，按其中当前版本的流程执行。不凭记忆、通用 Agent Skills 规范或自行总结的简化流程代替项目内 `skill-creator` 的实际说明。

如果 `skill-creator` 要求在调用其 Python 脚本前运行环境 preflight，则先运行对应 capability 的 preflight，并用它确认通过的 Python 命令。preflight 失败时，不绕过失败继续调用对应脚本，不自行安装缺失依赖，除非用户明确同意。

创建或修改 Skill 后，至少运行 `skill-creator` 提供的快速验证流程：

```text
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/<skill-name>
```

preflight 返回的 Python 命令若不是 `py -3`，使用实际通过 preflight 的命令。适合客观验证的 Skill，按 `skill-creator` 的指导考虑建立 `evals/evals.json` 和测试用例；是否做完整 benchmark / description optimization 根据任务复杂度和用户要求决定，不为了形式强制执行无价值的评测。

统一放置位置：`.agents/skills/`。

规则：

- 每个独立 skill 一个独立文件夹，内含 `SKILL.md`。
- `SKILL.md` 以合法 YAML frontmatter 开头，至少包含规范要求的 `name` 与 `description`。
- `name` 符合 Agent Skills 命名规则，并与 skill 父目录名称完全一致。
- `description` 同时说明"这个 Skill 做什么"和"什么时候应该使用"，使 Agent 能仅依靠元数据正确发现它。
- 可执行代码放该 skill 的 `scripts/`；详细参考放 `references/`，静态资源放 `assets/`。除非有明确工程理由，不把实现散落到项目其他目录。
- `SKILL.md` 引用 skill 自身文件时用相对 skill 根目录的路径，如 `scripts/tool.py`。
- 主 `SKILL.md` 保持聚焦；较长技术参考按需拆到 `references/`，利用渐进式加载。
- 不允许多个无关工具共用一个含糊的 `SKILL.md`。
- 创建脚本 / 程序时，配套 `SKILL.md` 属于同一交付的一部分；缺少 `SKILL.md` 时该工具不算完成。
- `SKILL.md` 正文至少说明：操作步骤、主要命令 / 调用方式、安全边界、关键边缘情况、验证方式；输入输出或项目文件关系在相关时说明。
- 工具会读取或修改 MOD 翻译目录时，其 `SKILL.md` 只需说明工具自身的操作与安全边界；四文档前置门禁由 AGENTS.md §2.2 统一承担，不在各工具 `SKILL.md` 重复。
- 工具会修改 xTranslator XML 时，`SKILL.md` 明确其 XML 安全约束与验证要求。
- 工具发生稳定的接口、行为或约束变化时，同步更新对应 `SKILL.md`。
- 环境中存在 `skills-ref` 时，创建或修改 Skill 后运行 `skills-ref validate <skill目录>`；验证失败时不声称 Skill 已符合 Agent Skills 规范。

`SKILL.md` 不为了满足形式要求而空洞；它必须足够让后续 Agent 正确、安全地使用对应工具。
