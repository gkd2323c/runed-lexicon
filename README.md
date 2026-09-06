# Runed Lexicon

Agent-assisted localization workflow for *Skyrim* & its mods.

面向《上古卷轴 V：天际》及 MOD 的 **Agent-assisted localization workflow**。

这个项目不把 LLM 当成逐句机器翻译器。它把语义理解、官方术语证据、剧情知识边界、结构上下文、确定性 XML 写回和工程验证拆开，让模型负责真正需要语言理解的部分，让程序负责不能出错的部分。

> 当前状态：正在真实 MOD 翻译中持续使用和回归验证。它是一套可工作的工程流程，还不是“一键自动汉化器”。

## 解决什么问题

长篇游戏文本的主要风险并不只是“句子翻得不顺”。更难处理的是：

- 同一角色、地点、派系和物品在数千条文本中保持一致；
- 区分官方译名、社区俗称、MOD 原创专名和有叙事意义的别称；
- 通读任务与对话后再翻译，而不是把每条 `<String>` 当成孤立句子；
- 不因为译者知道后续剧情，就提前暴露身份、关系或世界观真相；
- 保留 `<Alias=...>`、`%s`、`%d` 等运行时内容；
- 让 LLM 无权自由重写 xTranslator XML 结构；
- 把人工注意力留给真正的语义歧义，而不是重新逐条检查全部机器译文。

## 工作流

```text
ESP / ESM
   │
   ├─ xTranslator XML
   └─ xEdit structural context (when needed)
            │
            v
     semantic context building
       ├─ GLOBAL.md / GLOSSARY.md
       ├─ CONTEXT.md
       ├─ DICTIONARY.md
       └─ official terminology evidence
            │
            v
       Agent translation / review
            │
            v
      structured translation JSON
            │
            v
      deterministic quality gates
            │
            v
      deterministic XML writer
            │
            v
        translated xTranslator XML
```

项目的完整操作契约在 [`AGENTS.md`](AGENTS.md)。具体能力按 Agent Skill 拆在 [`.agents/skills/`](.agents/skills/) 中。

## 核心原则

1. **语义理解优先。** 先读完整任务、对话分支、书信或足够大的连续语义单元，再决定译法。
2. **禁止翻译剧透。** 后续剧情知识只能帮助避免误译，不能反向注入早期译文。
3. **官方词典是证据，不是字符串替换表。** 同一个实体的专名、通称、古称和伪装身份可能承载叙事信息。
4. **Agent 与确定性程序分工。** LLM 做理解、翻译和语义审校；程序做提取、绑定、写回、占位符保护和结构验证。
5. **XML 结构完整性是硬约束。** 默认只允许修改目标 `<Dest>`，并使用经过验证的 writer。
6. **工程 PASS 不等于语义正确。** 两种验证状态必须分别声明。

## 目录

```text
.agents/skills/          Agent Skills 与确定性脚本
corpus/                  回归事故与验证语料
dictionary/              本地官方英中 XML 证据目录（公开仓库不附带原始语料）
mods/                    本地 MOD 翻译工作目录（公开仓库不附带 MOD 文件）
tools/                   本地外部工具与辅助数据入口
.work/                   运行时中间产物（不进入版本管理）
AGENTS.md                项目级 Agent 操作契约
GLOBAL.md                Skyrim 全局语境
GLOSSARY.md              跨 MOD 译法决策
global-forbidden-words.json  可执行的全局禁用译法约束
```

## 本地数据不随仓库发布

为了避免把 Bethesda / Creation Club / MOD 作者文本、第三方工具二进制或个人工作产物混进代码仓库，以下内容默认由 `.gitignore` 排除：

- `dictionary/**/*.xml`：用户自己合法取得或导出的官方英中 xTranslator XML；
- `mods/**`：ESP / ESM / ESL、xTranslator XML、翻译成品与 MOD 局部工作文档；
- `tools/xEdit/**`：本地 xEdit 安装；
- `tools/term-rules/*.txt`：来源独立的第三方术语规则集；
- `.work/`、`_tmp/` 与 Skill eval workspace：运行时和评测产物。

这些目录保留 README 作为接口说明。详见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 开始使用

1. 准备 Python 3.10+。大多数核心脚本仅使用标准库；`translation-quality-gate` 的 CHAR001 繁简检测可选依赖 `zhconv`。
2. 将你自己合法取得的官方英中 xTranslator XML 放入 `dictionary/`。工具会递归读取整个目录，不依赖固定文件名。
3. 在 `mods/<plugin>/` 放入目标 MOD 的 xTranslator XML；需要恢复 DIAL/INFO、说话者和任务关系时，再提供原始插件并使用 `xedit-context-exporter`。
4. 从项目根目录启动支持 Agent Skills 的 Agent，并让它先读取 `AGENTS.md`。进入实际翻译前，目标 MOD 必须具备并完整读取 `CONTEXT.md` 与 `DICTIONARY.md`。
5. 按 Skill 的输入输出契约生成结构化翻译结果，通过 quality gate 后，再用 `xtranslator-xml-writer` 写回新的 translated XML。

目前没有为了“看起来完整”而额外包一层统一 CLI；`.agents/skills/` 中的 Skill 与脚本就是当前经过真实工作验证的接口。

## 贡献

请先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。尤其不要在 PR 中提交游戏原始文本、MOD 插件、未经授权的翻译文件、xEdit 二进制或个人运行目录。

## 开源发布状态

工作区已经按公开仓库与本地数据分离，根项目许可证为 **Apache-2.0**（见 [`LICENSE`](LICENSE)）。正式公开前请完成 [`PUBLISHING.md`](PUBLISHING.md) 中的第三方内容复核。第三方目录中已有独立许可证的文件继续遵循其原许可证。

本项目与 Bethesda、xTranslator、xEdit 及各 MOD 作者无隶属或官方关联。
