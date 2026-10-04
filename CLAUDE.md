# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`runed-lexicon` 是《上古卷轴 V：天际》及 MOD 的 Agent-assisted localization 工作流：Agent 做语义理解、翻译与语义审校，确定性 Python 程序做提取、绑定、门禁、写回与结构验证。仓库本身是流程与工具，不含游戏文本（见下「本地数据」）。对外介绍见 `README.md`，贡献与提交边界见 `CONTRIBUTING.md`，公开发布清单见 `PUBLISHING.md`。

## 权威契约

**`AGENTS.md` 是项目级操作契约，优先于本文件。** 每次会话开始先读它。

`AGENTS.md` §A 有一张强制加载路由表：命中触发条件时，必须完整读取对应 `.agents/skills/<skill>/SKILL.md` 再执行，禁止用摘要或记忆代行。常用映射：

| 场景                                              | 必读 Skill                            |
| ------------------------------------------------- | ------------------------------------- |
| 开始 / 审校翻译批次前                             | `skyrim-translation-craft`            |
| 新 MOD 启动、写回前、发布收敛声明前               | `skyrim-term-contract-workflow`       |
| 新 MOD 开工前清点官方名、裁决官方名词前           | `proper-noun-index`                   |
| 验收批次产出、核对覆盖率、声明收敛前、记录进度时  | `translation-batch-ops`               |
| 派任何子代理单之前、验收子代理产出时              | `subagent-ops`                        |
| 任何程序化操作之前（先检索复用）                  | `skyrim-tool-dev-rules`               |
| 批量改 XML 前后、出验证报告前                     | `skyrim-xml-verification`             |
| 长文本抗幻觉审查、收口前长文复核                  | `longtext-hallucination-review`       |
| 创建 / 修改 Skill                                 | `skill-creator`（完整读取后才能动手） |
| 创建 / 修改项目或 MOD 文档                        | `skyrim-doc-system`                   |

## 常用命令

Python 3.10+，绝大多数脚本只用标准库。解释器以 `skill-creator` preflight 确认的为准（本机为 `py -3`；CI 与本文命令用 `python`）。CI 会额外安装 `pyyaml`、`zhconv`。

```bash
# 推送前必跑：复刻 CI 五步（约 30 秒，失败 exit 1）
python tools/pre-push-check.py
python tools/pre-push-check.py --syntax-only      # 只跑第 4 步（CI 步骤入口）
python tools/pre-push-check.py --hygiene-only     # 只跑第 5 步（CI 步骤入口）
python tools/pre-push-check.py --install-hook   # 装 .git/hooks/pre-push，每台机器一次

# 单个 skill 的 unittest 套件 / 跑单个测试文件
python -m unittest discover -s .agents/skills/translation-executor/scripts -p "test_*.py"
python -m unittest discover -s .agents/skills/translation-executor/scripts -p "test_fill_set.py"

# CI 第 2 步的七个 standalone 回归脚本（与 pre-push-check.py 的 STANDALONE_SCRIPTS 两边必须同步改）
python .agents/skills/xtranslator-xml-writer/scripts/test_patch_mode.py
python .agents/skills/xtranslator-xml-writer/scripts/test_incremental_mode.py
python .agents/skills/noninfo-batch-planner/scripts/test_plan_noninfo.py
python .agents/skills/translation-review-tools/scripts/test_review_tools.py
python .agents/skills/translation-quality-gate/scripts/selftest_corpus.py
python .agents/skills/translation-fidelity-scan/scripts/test_fidelity_scan.py
python corpus/translation-regression/scripts/validate_corpus.py

# 改对应逻辑时必须跑，但不进 CI
python .agents/skills/translation-executor/scripts/test_workflow_regressions.py
python .agents/skills/local-model-translator/scripts/test_import_map.py
python .agents/skills/xtranslator-xml-writer/scripts/test_keep_semantics.py

# 收口链：一轮「验收→charset→预检→写回→段核对→快照」的唯一串行入口（pipeline.lock 独占）
python .agents/skills/translation-batch-ops/scripts/round_pipeline.py \
  --stem <stem> --batches <BATCH-ID...> \
  --xml mods/<stem>/<stem>_english_chinese.xml \
  --contract .work/<stem>/contracts/<stem>.compiled.json
# 已写回批的审查修正一条龙（fixes 分组 → patch → 写回 → 段核对 → readout 重生成）
python .agents/skills/translation-batch-ops/scripts/close_round.py \
  --stem <stem> --batches <BATCH-ID...> --fixes <fixes.json> \
  --xml mods/<stem>/<stem>_english_chinese.xml \
  --contract .work/<stem>/contracts/<stem>.compiled.json

# 新建 / 修改 Skill 后的最低验证
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/<skill-name>

# 词表机械 lint（引号配对/不可见字符/全半角/繁简；编译时自动执行，可独立跑）
python .agents/skills/term-contract-compiler/scripts/lint_terms.py \
  --terms mods/<plugin>/terms.json --bans global-forbidden-words.json
```

**测试发现的坑**：CI 的 `unittest discover` 步骤会跳过不含字面量 `unittest` 的测试文件（怕 `exit 5: NO TESTS RAN`）。用 standalone 风格（自己写 `main()`）新写的 `test_*.py` 不会被 CI 执行——要么加 `unittest`，要么登记进 `tools/pre-push-check.py` 的 `STANDALONE_SCRIPTS` 与 `ci.yml`（两边必须同步改）。

## 流水线架构

```text
ESP/ESM ──xTranslator──> mods/<plugin>/<plugin>_english_chinese.xml   （源 XML，严禁就地覆盖）
        └──mutagen────> .work/<plugin>/context/<plugin>-dialogue-context.json  （DIAL→INFO 结构证据）
                                   │
  translation-context-builder ─────┤  合 XML + dialogue + terms.json + dictionary/
                                   │  → context.json（batches，0-based xml_index）
                                   v
  translation-executor             │  Agent 逐 unit 翻译 → translation JSON
                                   │  status ∈ TRANSLATED / REVIEW / KEEP，confidence
                                   v
  translation-quality-gate         │  契约 + unit_bindings + global_bans → PASS / FAIL
                                   v
  xtranslator-xml-writer           │  只改目标 <Dest>，按 span 拼接、字节保真
                                   v
              mods/<plugin>/<plugin>_english_chinese_translated.xml   （唯一 canonical）
```

跨文件才能看出的架构要点：

- **对话结构两段式**：mutagen 导出的原始 JSON 必须经 `convert-dialogue-context.py` 适配成 `<stem>-dialogue-context.json` 才能进 builder；直接传原始 JSON 会 0 连接、静默 unmatched。逐批索引用 `make-batch-index.py` 从批次计划抽 `idx`。
- **批次计划**：INFO 批次用 `plan-info-batches.py`；非 INFO 记录族（QUST 日志、BOOK、CELL/NPC_ 名等）用 `noninfo-batch-planner`。两者产出 pipeline-ready 批次，供 context-builder / verify / writer 共同消费。
- **门禁两层**：机械层 `quality_gate.py`（TERM/KEEP/占位符/字符集）FAIL 是硬拦截、阻断写回；语义层 `semantic_gate.py` 是参考层，FAIL/PARTIAL 只记明细不阻塞主线，无 key 时记 `UNCHECKED`（口径详见 AGENTS.md §4）。
- **收口链已工具链化**：验收→写回→快照唯一入口 `round_pipeline.py`（单进程串行 + `pipeline.lock` 独占锁，持锁期间外部命令 rc=2 拒绝）；已写回批的审查修正走 `close_round.py`。禁止手动并行编排，详见 `translation-batch-ops` SKILL。

Skill 分四层 + 元 skill：

- **协作编排**：`subagent-ops`（委派决策、任务卡编译与红线、体量与送达纪律、故障归因；附轨迹读取与写盘冲突守卫三个可选脚本）
- **流水线核心**（有脚本）：`translation-context-builder`、`translation-executor`、`translation-quality-gate`（含 `semantic_gate.py` 参考层）、`xtranslator-xml-writer`、`translation-batch-ops`（批次运维：验收/覆盖/缺口/进度/分片/审查欠账 review_pending 与批次实况 batch_status/收口链 round_pipeline 与 close_round）、`translation-batch-preparer`（批次切分与未译清点）、`noninfo-batch-planner`（非 INFO 批次计划）、`translation-review-tools`（读批/术语摘要/修正集 apply/字符集归一/抗幻觉探针）、`review-sheet-export`（导出人工审校 xlsx）
- **契约与名词**：`term-contract-compiler`（编译契约）、`proper-noun-index`（源侧官方专名清点）、`dictionary-noun-audit`（译文侧漏项候选）、`noun-consistency-scan`（同源多译分裂）、`same-source-convergence`（同源译文收敛、分片对账与译形裁决）
- **结构提取与查询**：`mutagen-dialogue-exporter`（秒级解析插件，取代 xEdit；xEdit 导出路径已退役，保留在 `.agents/skills/xedit-context-exporter/` 仅作历史参考）、`skyrim-xml-tools`（XML 检视/未译清单/官方词典查询，只读）、`translation-fidelity-scan`、`fantasy-context-auditor`（本地 LLM 预筛）、`local-model-translator`
- **纯规则**（无脚本）：`skyrim-translation-craft`、`skyrim-doc-system`、`skyrim-term-contract-workflow`、`skyrim-tool-dev-rules`、`skyrim-xml-verification`、`longtext-hallucination-review`、`shuo-ren-hua`（中文文风，写/改中文文档默认应用）
- **元 skill**：`skill-creator`（创建/修改 skill 的规范与 `quick_validate.py` / `check_env.mjs` 验证脚本）

## 必须知道的约束

以下几条是跨多个文件才能看出的规则，违反即事故：

1. **序号基准已统一 0-based**：`skyrim-xml-tools` 的 `inspect` / `untranslated` 与 `translation-context-builder` / `translation-executor` / `xtranslator-xml-writer` 的 `xml_index`（`progress_snapshot` 里叫 `idx`）同为 **0-based**（String 元素序号，非文件行号）。若存量文档或会话记录按 1-based 记录过 inspect 序号，需减 1 对照；拿不准仍用 `Source` 文本核对。

2. **确定性输出契约**（AGENTS.md §2.0.1）：文件名 = 角色 + 目标对象，**禁止**日期戳、序号、`final` / `vN` / `roundN` / `backup` 等版本标签。写回产物恒为 `<plugin>_english_chinese_translated.xml`（同名覆盖式演进，上一代移入 `.work/<plugin>/archive/<previous-sha256>/`）；报告恒为 `.work/<plugin>/reports/<plugin>-{gate,noun-audit,writeback}-report.json`。writer 会机械拒绝违规路径，**不得用 shell 复制/重命名伪造产出**。

3. **人类文档不进机器链路**：`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` 是给人（和 Agent 直接读）的，任何代码不得读取、解析、嵌入或哈希校验。机器侧数据一律走独立结构化文件（`mods/<plugin>/terms.json`、dialogue-context JSON）。编辑文档不得能破坏构建。

4. **canonical 与派生物**：`DICTIONARY.md` / `GLOSSARY.md` 是 canonical，编译出的 `.compiled.json` 契约与 unit bindings 是派生物。术语决策变更 → 重编译契约 → 重跑 gate。

5. **全局禁用词链路**：`global-forbidden-words.json` 经 `term-contract-compiler --global-bans` 嵌入契约的 `global_bans`，由 gate 以 **TERM004**（+ `global_keep` → **KEEP002**）机械执行。该词库只收跨 MOD 官方名词的系统性坏形态；MOD 专有词、普通词不入库。**有带作用域的 MOD 级白名单通道（R21）**：`mods/<plugin>/global-ban-exemptions.json` 声明 `{english, forbidden, reason, scope}`，经 `--global-ban-exemptions` 嵌入契约，命中时 TERM004 降为 **WARNING 并保留 finding**（不丢弃、可审计）；`scope` 三键 `source_contains`/`dest_left`/`dest_right`，未知键直接报错。用于修中文无词边界导致的子串误报，**不是绕过项目级裁决的通道**——详见 AGENTS.md §2.3 的四条通路。

6. **规则扫描是闭集，启发式搜是收敛默认方法**：gate / noun-audit / noun-consistency-scan 只覆盖已登记词与已定义模式，**规则零命中不等于收敛**。声明「名词收敛 / 实体收敛」必须同时完成规则扫描 + 启发式种子扩散（分块读译文找专名种子 → 同英文锚全文扩散 → 中文形态归组 → 主会话裁决），缺一不得声明。

7. **XML 是不可重序列化的字节流**：只允许改目标 `<Dest>` 的内文，其余字节（BOM、换行、实体风格、空白）原样保留。禁止用通用 XML serializer 或全局正则重写文件。`<Source>` / `<EDID>` / `<REC>` / `<Params>` 与 `<String>` 节点增删顺序一律不可动。占位符（`<Alias=...>`、`<Global=...>`、`%s`、`%d`、`{...}`、`[...]`）必须原样保留。

8. **先检索复用，禁止重复实现**（`skyrim-tool-dev-rules` §1.0）：需要任何程序化操作前先查 `.agents/skills/` 是否已有覆盖。一次性内联 `python -c`、shell 管道、临时脚本只要在做现成 skill 能做的事，同属违规。确需一次性的落 `_tmp/scripts/`（不进项目资产目录）；要成为复用能力的按 §A 沉淀为 skill。

9. **性能门禁**：单次完整调用 > 30s 视为缺陷，需先 `cProfile` 拆账再修；gate → writeback → 复扫关键路径须保持秒级基线（gate 0.89s @8555 单元 / 4.1s @20634 单元、writeback 0.43s、noun-audit 2.1s）。改动后写回类须证明输出**逐字节一致**。

10. **两种验证状态必须分开声明**：工程验证通过（结构完好、节点数一致、占位符完整、gate PASS）≠ 语义翻译正确。脚本退出码 0 不等于「翻译正确」或「已实机验证」。不因已知后续剧情提前译出真实身份、阵营或亲缘关系。

11. **工作区卫生**：`mods/<plugin>/` 零杂物（默认只允许四文档/`terms.json`、源 XML、canonical 写回产物、原插件）；`.work/<Plugin>/` 是过程资产唯一落点（`batches/` `archive/` `reports/` `context/` `contracts/`）；`_tmp/` 每次清理后递归文件数必须**净递减**。

12. **不擅自初始化或修改 Git 提交历史**，除非用户明确指令。提交时注意 `.gitignore` 已排除的内容（见下）不得强制加入。

## 本地数据（公开仓库不附带）

`dictionary/**/*.xml`、`mods/**`、`tools/xEdit/**`、`tools/term-rules/*.txt`、`tools/Mutagen/`、`.work/`、`_tmp/` 均被 git 排除（不发布）。其中 `dictionary/**/*.xml` 的规则不在 `.gitignore`，而在本机 `.git/info/exclude-dictionary`（由 `core.excludesFile` 指向）：平台检索工具会跳过 `.gitignore` / `.git/info/exclude` 排除的文件、但不读 `core.excludesFile` 来源，规则置于该来源使 Agent 可检索词典且 git 仍不追踪。**新 clone 需重建**：将 `dictionary/**/*.xml` 写入 `.git/info/exclude-dictionary` 并执行 `git config --local core.excludesFile <repo>/.git/info/exclude-dictionary`。**全新 clone 下涉及 `mods/<plugin>/` 与 `dictionary/` 的命令无法直接运行**——需要用户自备官方英中 xTranslator XML（导出方法见 `dictionary/EXPORT_GUIDE.md`）与目标 MOD XML。

各 MOD 工作区典型内容：`<plugin>_english_chinese.xml`（源）、`<plugin>_english_chinese_translated.xml`（canonical 成品）、插件二进制（仅结构恢复需要时）、`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` / `SOP.md`、`terms.json`。翻译前必须完整读取 `CONTEXT.md` 与 `DICTIONARY.md`；**任一缺失就停下询问用户是否补建，不得静默创建空模板、不得启动翻译**。接手已有 MOD 先读 `SOP.md` 与 `PROGRESS.md` 恢复上下文。

`corpus/` 与 `_tmp/` 的区别：`corpus/translation-regression/` 是随仓库发布的**交付资产**（事故驱动的语义等价 synthetic fixture，分 `gate` / `translator` 两层，两层不可互算）；`_tmp/` 是运行时产物，任务收尾即清理。

## 修改 Skill / 工具

实质修改前完整读取 `.agents/skills/skill-creator/SKILL.md` 与 `skyrim-tool-dev-rules/SKILL.md`。每个脚本、CLI、自动化工具都必须有配套 `SKILL.md`（缺 `SKILL.md` 不算交付完成），正文至少含操作步骤、主要命令、安全边界、边缘情况、验证方式。改 CI 时同步改 `tools/pre-push-check.py`，反之亦然——两边步骤必须一致。
