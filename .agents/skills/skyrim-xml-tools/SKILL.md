---
name: skyrim-xml-tools
description: Inspect xTranslator XML, list untranslated Skyrim mod strings, query every official English-Chinese XML dictionary placed in the project's dictionary directory, and scan a translated XML's Dest against an xTranslator term-conversion rule file for old/non-official translation suggestions (read-only). Use when analyzing a mod translation XML, identifying untranslated entries, checking record-type statistics, looking up established terminology before translation, or auditing finished translations against a term-rule corpus.
compatibility: Requires Python 3 and the runed-lexicon project layout with dictionary/ and mods/ directories. No network access or third-party packages required.
metadata:
  version: "0.2.0"
---

# Skyrim XML Tools

## 用途

使用本 Skill 自带的只读 CLI `scripts/skyrim_xml_tools.py` 检查 xTranslator XML、列出尚未翻译的字符串，以及查询项目官方英中词典。

当前版本只读取文件，不修改任何 XML。

## ⚠️ index 基准（重要）

本工具所有命令输出的 `index` / 条目序号为 **0-based**（XML 中 `<Content/String>` 节点从 0 起枚举的顺序位置，非 `List` 属性），与 `translation-context-builder` / `translation-executor` / `xtranslator-xml-writer` 的 `xml_index` 基准一致，跨工具对照无需换算。拿不准时用 `Source` 文本核对，不要依赖来源不明的旧序号。

## 适用场景

- 首次检查一个 xTranslator 导出的 MOD XML。
- 统计 `REC` 类型、文本数量、`Source == Dest` 数量和重复源文本。
- 提取仍需要翻译的条目。
- 在决定角色名、地名、物品名、法术名、UI 文本等译法前查询官方译文。
- 对已译 XML 做术语规则扫描（旧译/非官方译名 → 建议新译，只报告不改）。
- 为后续 Agent 翻译流程提供结构化、可复查的数据。

## 程序位置

- `scripts/skyrim_xml_tools.py` —— `inspect` / `untranslated` / `lookup` / `termrules`
- `scripts/lookup_batch.py` —— 批量官方查证（复用上面的词典加载与匹配，不重复实现）
- `scripts/test_lookup_batch.py` —— 上者的 unittest（临时词典目录，不依赖真实语料）

仅使用 Python 标准库，无第三方依赖。

## 输入

### `inspect`

输入一个 xTranslator XML 文件，或者一个只包含一个 XML 文件的 MOD 目录。

### `untranslated`

输入一个 xTranslator XML 文件或 MOD 目录。当前使用 `Source == Dest` 且 `Source` 非空作为“尚未翻译”的机械判定条件。

注意：这只是候选集合，不保证每一条都应该翻译。例如技术名称、作者名或故意保留的英文仍可能出现在结果里。

### `lookup`

输入英文查询字符串。默认递归搜索项目 `dictionary/` 下所有 XML 的 `<Source>`。

`dictionary/` 目录本身定义“官方词典”集合：其目录树下每一个 `*.xml` 都视为官方来源。不要假设文件名、文件数量、子目录结构或 DLC 集合，也不要根据文件名赋予特殊优先级；以后新增的 XML 必须无需改代码即可自动参与查询。

## 输出

- 默认输出适合 Agent / 人类快速查看的文本。
- 三个子命令均支持 `--json`，用于后续程序或 Agent 结构化消费。
- 所有操作都保留并输出可追溯信息，如 XML 条目序号、`EDID`、`REC`、`Source`、`Dest` 或词典文件名。

## 主要命令

在项目根目录执行：

```text
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py inspect mods/<plugin>
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py inspect mods/<plugin> --json

python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py untranslated mods/<plugin>
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py untranslated mods/<plugin> --rec INFO:NAM1 --limit 20
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py untranslated mods/<plugin> --json --limit 0

python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "Soul Gem"
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "Sovngarde" --ignore-case
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "soul" --contains --ignore-case --limit 20

python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py termrules mods/<plugin>/<plugin>_english_chinese_translated.xml --rules <term-rules.txt>
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py termrules mods/<plugin> --rules <term-rules.txt> --rec INFO:NAM1 --json
```

`--limit 0` 表示不限制输出数量。

### lookup 匹配模式：exact 与 contains

`lookup` **默认 exact 匹配整个 `<Source>`**：仅当某词典条目的 Source 与输入字符串完全相同时才命中。

专名（势力/种族/物品/地名等）在官方词典中常嵌于整句对话或整本书籍的 Source，而非独立词条。查询这类词必须加 `--contains`（建议同时 `--ignore-case`）：

```text
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "Dominion" --contains --ignore-case --limit 10
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "Aldmeri" --contains --ignore-case --limit 10
```

- 反例：`lookup "Dominion"`（exact）返回 0 命中，但 Dawnguard/Update/CC 词典中该词均嵌于整句源文本（如 Update 萨蒂亚线“萨蒂亚向先祖神洲出卖自己的人民”）。exact 空结果不能作为“词典无此词”的证据。
- 禁止在未做 `--contains --ignore-case` 复查前声明“词典无词条/无官方证据”；必要时直接 grep `dictionary/` 原始 XML 交叉验证。
- 词典命中为参考证据，不要求 MOD 中所有同形英文一律采用该译法。

### `lookup_batch`（批量查证，一次调用覆盖整批词）

```text
python .agents/skills/skyrim-xml-tools/scripts/lookup_batch.py <词> [<词> ...] [--contains] [--ignore-case] [--max-forms N] [--out <单份报告>] [--json] [--terms <词表文件>]
```

词直接写在命令行上；`--terms <文件>` 只在你确实有一份想留着复跑的长名单时才用（一行一词，`#` 为注释）。默认 **exact**。

**为什么不逐词 `lookup`**：逐词调用有两个问题——每次调用都重新解析 `dictionary/`
下全部词典 XML（实测 11 词批量一次 1.7s，逐词跑是同样次数的全量重解析），以及每词
一份重定向输出会在 `_tmp/` 里堆出几十个一次性文件。`lookup_batch` 只读一次词典、
输出一份汇总到 stdout，**整个过程不产生任何临时文件**（除非你显式 `--out`）。
库内侧的对应工具是 `translation-review-tools/scripts/query.py --anchors`，
同样是「一个输入、一次输出」。

**它内建了四条本项目反复踩到的判据：**

1. **零命中自动复验拼写**。`lookup` 大小写敏感，零命中常是假阴性。零命中时会自动重试
   首字母大写、全小写、全大写、去空格、连字符↔空格等形态并列出结果。因此
   **「本工具报零」比「`lookup` 报零」可信**，但仍不等于词典里真没有这个词。
2. **长文本提及不计入译形**。书籍正文（`BOOK:DESC` 等）里提到某个词属于顺带提及，
   往往与该词的译法无关——`Sheza` 命中的是 `Shezarrine`（舍扎因）、`Aldmeris` 命中
   的是「奥德莫**文**」这个语言名、`Carnage` 命中的是普通词。Dest 超过 40 字的命中
   单独计为「长文本提及」，不当成官方译形。
3. **exact 与 contains 的结论强度不同**。exact 命中 = 官方词典确有该词条，其 Dest
   才是官方译形；`--contains` 命中只说明该词出现在某些 Source 里，输出里的
   **「语境」** 一栏可能是更长名称的子串、对话正文或星辰法术名，**不构成译形实证**。
   要断言官方译形就用 exact。
4. **`status` 的强度要跟证据强度对齐**。实测 `Anu` exact 零命中（此前 contains 命中的
   全是 `Anuriel` 安努瑞尔的子串），而 `Thalmor` exact 命中 1 条即「梭默」。**词表把
   某条标成 CONFIRMED 之前，先用本工具确认它真能 exact 命中。**

实测：11 词 / 99090 条目 / 1.7s（exact），零临时文件。测试：`test_lookup_batch.py`
（14 例，用临时词典目录，不依赖真实 `dictionary/` 语料）。

### `termrules`（启发式术语规则扫描）

输入一个已译 xTranslator XML（或目录）加一个 xTranslator 术语转换规则文件
（`StartRule` / `Search=` / `Replace=` 块格式）。工具在每条 `Dest` 里查找
Search 为中文的规则，命中则报告“位置 + 命中的旧译 + 规则建议的新译”。

项目内规则文件位于 `tools/term-rules/术语转换规则.txt`（外部译者校准集，
非官方资产，使用前先读其 README）；也可用 `--rules` 指向任意规则文件。

性质与边界：

- **只读启发式，给建议不改翻译**。它不知道规则集本身的正确性——规则文件
  是外部译者的积累，可能含个人偏好甚至错译（如把官方 Tu'whacca 图瓦卡误写成
  图尔瓦）。每条命中必须由 Agent 对照官方词典/语境判断后再决定是否修改。
- 仅扫描 Search 含中文的规则（旧中文译名 → 新中文译名的修正），纯英文规则
  （多为 Source 重写规则）被忽略。
- 去噪：Dest 已含 Replace（规则已生效或 search 是更长正确词的子串，如
  “扎克之塔”命中“姆扎克之塔”）自动跳过；同一条目同词只报一次。
- 建议配合 `lookup` 使用：命中先用官方词典验证，官方证据确认后再改。
- 典型误报来源：规则 Search 是正确译名的一部分（如“图瓦”命中“图瓦卡”）。
  这类需人工判断，工具不试图消除。
- 性能基线（约 3600 节点 × 约 2.7 万条规则）：**0.18s**，远低于
  30s 缺陷阈值；后续如规则集或 XML 大幅增长导致超 5s，先 cProfile 拆账再优化。

## 安全边界

- 当前程序禁止写回 XML；不要把它当作翻译写入工具。
- 不修改 `<Source>`、`<Dest>`、`<EDID>`、`<REC>` 或任何 XML 元数据。
- 不格式化、不重排、不重新序列化源 XML。
- 当目录中存在多个 XML 时，程序会拒绝猜测目标文件，必须显式指定文件。
- `Source == Dest` 只是机械筛选条件，不能直接等同于“必须翻译”。
- 词典命中是参考证据，不代表所有同形英文在 MOD 当前语境中都必须采用该译法。

## 验证方式

修改本程序后至少运行：

```text
python -m py_compile .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py inspect mods/<plugin>
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py untranslated mods/<plugin> --rec INFO:NAM1 --limit 3
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py lookup "Soul Gem"
```

用固定样本记录以下机械基准：总 `<String>` 数、`Source == Dest`、`Source != Dest`、`INFO:NAM1` 与 `DIAL:FULL` 数量。原 XML 未变化而任一基准突变时，先调查解析逻辑，不要继续翻译。

## 与项目文件的关系

- `dictionary/*.xml`：官方 Skyrim / DLC 英中参考语料，只读。
- `mods/<plugin>/*.xml`：待翻译 MOD 的 xTranslator XML，只读。
- `mods/<plugin>/CONTEXT.md`：MOD 长期上下文；实际翻译前必须全量读取。
- `mods/<plugin>/DICTIONARY.md`：MOD 局部术语表；实际翻译前必须全量读取。
- `AGENTS.md`：项目总规则，优先遵守其中的 XML 安全、翻译前置和验证要求。

## 当前非目标

- 不写回 `<Dest>`。
- 不自动翻译。
- 不恢复 ESP / ESM 内部记录引用关系。
- 不自动修改 `CONTEXT.md` 或 `DICTIONARY.md`。
- 不把词典查询结果自动固化为 MOD 术语。
