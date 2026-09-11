# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`runed-lexicon` 是《上古卷轴 V：天际》及 MOD 的 Agent-assisted localization 工作流：Agent 做语义理解、翻译与语义审校，确定性 Python 程序做提取、绑定、门禁、写回与结构验证。仓库本身是流程与工具，不含游戏文本（见下「本地数据」）。

## 权威契约

**`AGENTS.md` 是项目级操作契约，优先于本文件。** 每次会话开始先读它。

`AGENTS.md` §A 有一张强制加载路由表：命中触发条件时，必须完整读取对应 `.agents/skills/<skill>/SKILL.md` 再执行，禁止用摘要或记忆代行。常用映射：

| 场景 | 必读 Skill |
| --- | --- |
| 开始 / 审校翻译批次前 | `skyrim-translation-craft` |
| 新 MOD 启动、写回前、发布收敛声明前 | `skyrim-term-contract-workflow` |
| 任何程序化操作之前（先检索复用） | `skyrim-tool-dev-rules` |
| 批量改 XML 前后、出验证报告前 | `skyrim-xml-verification` |
| 创建 / 修改 Skill | `skill-creator`（完整读取后才能动手） |
| 创建 / 修改项目或 MOD 文档 | `skyrim-doc-system` |

## 常用命令

Python 3.10+，绝大多数脚本只用标准库。解释器以 `skill-creator` preflight 确认的为准（本机为 `py -3`；CI 与本文命令用 `python`）。CI 会额外安装 `pyyaml`、`zhconv`。

```bash
# 推送前必跑：复刻 CI 三步（约 6 秒，失败 exit 1）
python tools/pre-push-check.py
python tools/pre-push-check.py --install-hook   # 装 .git/hooks/pre-push，每台机器一次

# 单个 skill 的 unittest 套件 / 跑单个测试文件
python -m unittest discover -s .agents/skills/translation-executor/scripts -p "test_*.py"
python -m unittest discover -s .agents/skills/translation-executor/scripts -p "test_fill_set.py"

# CI 第 2 步的三个 standalone 回归脚本
python .agents/skills/xtranslator-xml-writer/scripts/test_patch_mode.py
python .agents/skills/translation-quality-gate/scripts/selftest_corpus.py
python corpus/translation-regression/scripts/validate_corpus.py

# 改对应逻辑时必须跑，但不进 CI
python .agents/skills/translation-executor/scripts/test_workflow_regressions.py
python .agents/skills/local-model-translator/scripts/test_import_map.py
python .agents/skills/xtranslator-xml-writer/scripts/test_keep_semantics.py

# 新建 / 修改 Skill 后的最低验证
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/<skill-name>
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

Skill 分三层：

- **流水线核心**（有脚本）：`translation-context-builder`、`translation-executor`、`translation-quality-gate`、`xtranslator-xml-writer`
- **契约与名词**：`term-contract-compiler`（编译契约）、`proper-noun-index`（源侧官方专名清点）、`dictionary-noun-audit`（译文侧漏项候选）、`noun-consistency-scan`（同源多译分裂）
- **结构提取与扫描**：`mutagen-dialogue-exporter`（秒级解析插件，取代 xEdit；`xedit-context-exporter` 已退役）、`translation-fidelity-scan`、`fantasy-context-auditor`（本地 LLM 预筛）、`local-model-translator`
- **纯规则**（无脚本）：`skyrim-translation-craft`、`skyrim-doc-system`、`skyrim-term-contract-workflow`、`skyrim-tool-dev-rules`、`skyrim-xml-verification`、`subagent-ops`

## 必须知道的约束

以下几条是跨多个文件才能看出的规则，违反即事故：

1. **序号基准不统一（最容易误判）**：`skyrim-xml-tools` 的 `inspect` / `untranslated` 输出 **1-based** index；`translation-context-builder` / `translation-executor` / `xtranslator-xml-writer` 的 `xml_index`（`progress_snapshot` 里叫 `idx`）是 **0-based**（String 元素序号，非文件行号）。跨工具对照先减 1，拿不准用 `Source` 文本核对——历史上曾因此误删批次条目。

2. **确定性输出契约**（AGENTS.md §2.0.1）：文件名 = 角色 + 目标对象，**禁止**日期戳、序号、`final` / `vN` / `roundN` / `backup` 等版本标签。写回产物恒为 `<plugin>_english_chinese_translated.xml`（同名覆盖式演进，上一代移入 `.work/<plugin>/archive/<previous-sha256>/`）；报告恒为 `.work/<plugin>/reports/<plugin>-{gate,noun-audit,writeback}-report.json`。writer 会机械拒绝违规路径，**不得用 shell 复制/重命名伪造产出**。

3. **人类文档不进机器链路**：`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` 是给人（和 Agent 直接读）的，任何代码不得读取、解析、嵌入或哈希校验。机器侧数据一律走独立结构化文件（`mods/<plugin>/terms.json`、dialogue-context JSON）。编辑文档不得能破坏构建。

4. **canonical 与派生物**：`DICTIONARY.md` / `GLOSSARY.md` 是 canonical，编译出的 `.compiled.json` 契约与 unit bindings 是派生物。术语决策变更 → 重编译契约 → 重跑 gate。

5. **全局禁用词链路**：`global-forbidden-words.json` 经 `term-contract-compiler --global-bans` 嵌入契约的 `global_bans`，由 gate 以 **TERM004**（+ `global_keep` → **KEEP002**）机械执行。该词库只收跨 MOD 官方名词的系统性坏形态；MOD 专有词、普通词不入库。MOD 确需破例时走 `AGENTS.md` §2.3 的 `[OVERRIDE_GLOBAL_BAN: <源词>] -> <特例译名> | 理由: ...` 白名单协议，写入该 MOD 的 `DICTIONARY.md`。

6. **规则扫描是闭集，启发式搜是收敛默认方法**：gate / noun-audit / noun-consistency-scan 只覆盖已登记词与已定义模式，**规则零命中不等于收敛**。声明「名词收敛 / 实体收敛」必须同时完成规则扫描 + 启发式种子扩散（分块读译文找专名种子 → 同英文锚全文扩散 → 中文形态归组 → 主会话裁决），缺一不得声明。

7. **XML 是不可重序列化的字节流**：只允许改目标 `<Dest>` 的内文，其余字节（BOM、换行、实体风格、空白）原样保留。禁止用通用 XML serializer 或全局正则重写文件。`<Source>` / `<EDID>` / `<REC>` / `<Params>` 与 `<String>` 节点增删顺序一律不可动。占位符（`<Alias=...>`、`<Global=...>`、`%s`、`%d`、`{...}`、`[...]`）必须原样保留。

8. **先检索复用，禁止重复实现**（`skyrim-tool-dev-rules` §1.0）：需要任何程序化操作前先查 `.agents/skills/` 是否已有覆盖。一次性内联 `python -c`、shell 管道、临时脚本只要在做现成 skill 能做的事，同属违规。确需一次性的落 `_tmp/scripts/`（不进项目资产目录）；要成为复用能力的按 §A 沉淀为 skill。

9. **性能门禁**：单次完整调用 > 30s 视为缺陷，需先 `cProfile` 拆账再修；gate → writeback → 复扫关键路径须保持秒级基线（gate 0.89s @8555 单元 / 4.1s @20634 单元、writeback 0.43s、noun-audit 2.1s）。改动后写回类须证明输出**逐字节一致**。

10. **两种验证状态必须分开声明**：工程验证通过（结构完好、节点数一致、占位符完整、gate PASS）≠ 语义翻译正确。脚本退出码 0 不等于「翻译正确」或「已实机验证」。不因已知后续剧情提前译出真实身份、阵营或亲缘关系。

11. **工作区卫生**：`mods/<plugin>/` 零杂物（默认只允许四文档/`terms.json`、源 XML、canonical 写回产物、原插件）；`.work/<Plugin>/` 是过程资产唯一落点（`batches/` `archive/` `reports/` `context/` `contracts/`）；`_tmp/` 每次清理后递归文件数必须**净递减**。

12. **不擅自初始化或修改 Git 提交历史**，除非用户明确指令。提交时注意 `.gitignore` 已排除的内容（见下）不得强制加入。

## 本地数据（公开仓库不附带）

`dictionary/**/*.xml`、`mods/**`、`tools/xEdit/**`、`tools/term-rules/*.txt`、`tools/Mutagen/`、`.work/`、`_tmp/`、`.agents/skills/hana-subagent-ops/` 均由 `.gitignore` 排除。**全新 clone 下涉及 `mods/<plugin>/` 与 `dictionary/` 的命令无法直接运行**——需要用户自备官方英中 xTranslator XML（导出方法见 `dictionary/EXPORT_GUIDE.md`）与目标 MOD XML。

各 MOD 工作区典型内容：`<plugin>_english_chinese.xml`（源）、`<plugin>_english_chinese_translated.xml`（canonical 成品）、插件二进制（仅结构恢复需要时）、`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md`、`terms.json`。翻译前必须完整读取 `CONTEXT.md` 与 `DICTIONARY.md`；**任一缺失就停下询问用户是否补建，不得静默创建空模板、不得启动翻译**。

`corpus/` 与 `_tmp/` 的区别：`corpus/translation-regression/` 是随仓库发布的**交付资产**（事故驱动的语义等价 synthetic fixture，分 `gate` / `translator` 两层，两层不可互算）；`_tmp/` 是运行时产物，任务收尾即清理。

## 修改 Skill / 工具

实质修改前完整读取 `.agents/skills/skill-creator/SKILL.md` 与 `skyrim-tool-dev-rules/SKILL.md`。每个脚本、CLI、自动化工具都必须有配套 `SKILL.md`（缺 `SKILL.md` 不算交付完成），正文至少含操作步骤、主要命令、安全边界、边缘情况、验证方式。改 CI 时同步改 `tools/pre-push-check.py`，反之亦然——两边步骤必须一致。
