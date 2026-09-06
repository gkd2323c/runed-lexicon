# Translation Regression Corpus

本目录保存 skyrim-mod-translator 翻译流水线的**事故驱动回归语料**。每个核心失败模式都来自项目中真实发生并被确认的事故，但公开 fixture 默认经过**语义等价改写**：保留致错条件、上下文结构、机械断言与修复原则，不分发原 MOD 的逐字台词、角色名、插件名、xml_index 或私有审计路径。

这套 corpus 的目的不是保存事故现场，而是保存事故的**可复现机制**。真实原始证据留在本地私有工作区；公开仓库只携带足以验证工作流行为的 synthetic fixture。

## 改写原则

- 保留失败机制：原事故依赖多句上下文、别名层级、残缺 Source、逻辑方向或占位符时，公开用例必须继续包含同类难点。
- 不把题目改简单：不能把“必须结合上下文才知道”的问题改成单句直接给答案。
- MOD 原创内容匿名化：真实角色名、剧情事件、插件名、EDID、xml_index、审计报告路径全部移除或替换。
- TES 官方术语可以保留：`Argonian`、`Helgen`、`sweetroll` 等用例测试的正是官方译名契约；周围句子仍使用自写 synthetic 文本。
- `origin` 只记录 `real_project_incident_rewritten` 等 provenance 类别，不公开事故来自哪个 MOD/哪一条记录。
- 如改写会破坏测试本身（例如测试某个官方专名的 canonical 译法），保留最小必要术语，不保留不必要的原句上下文。

## 双层结构（test_layer 强制）

任何用例必须声明 `test_layer`，禁止无层混入。两种层不可互算，防止出现"回归 98% 通过但 90% 是正则测试"的假象。

| test_layer | 是否调用模型 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- | --- |
| `gate` | 否（程序单元测试） | source + translation + contract | PASS / FAIL + 违规码（TERM001/002/003、KEEP001、PLACEHOLDER001、CHAR001、XML001） | 测试 Translation Quality Gate 自身。bad_translation 必须 FAIL，golden 必须 PASS |
| `translator` | 是（模型能力评测） | semantic unit + CONTEXT + DICTIONARY + contract evidence | 整段译文，黑名单 + 必现 + golden 抽样人工复核 | 回答"这个模型 / prompt / 本地翻译流程适不适合本项目" |

## 目录布局

```
corpus/translation-regression/
  README.md              本文件
  schema/
    gate-case.schema.json        gate 层用例 schema
    translator-case.schema.json  translator 层用例 schema
  cases/
    gate/
      global-bans.json            全局禁用词机制
      official-term-core.json     官方 / CC 专名契约
      term-contract-core.json     术语 binding / 省略 / 多义词
      source-fidelity-core.json   Source fidelity / KEEP / placeholder / charset
      qa-core.json                短叹词与高频术语回归
    translator/
      semantic-core.json          上下文、昵称、残缺句、词义误判
      …
```

## 用例分类（category）

`term_exact` / `term_forbidden` / `term_variant` / `keep` / `placeholder` / `charset` / `xml_structure` / `interjection` / `source_fidelity` / `spoiler_alias` / `semantic`。

- `source_fidelity`、`spoiler_alias`、`semantic` 类归 translator 层或人工抽样，**不得混入 gate 自动门禁假装能自证**。
- 机械可断言类别（term / keep / placeholder / charset / xml / interjection 中可正则断言者）可进入 gate 层。

## 维护规则

- 结构验收脚本：`scripts/validate_corpus.py`（改 schema / cases 后先跑它）。
- 新事故确认后先在私有工作区保留完整证据，再补一条语义等价的公开 fixture。
- 每条公开记录只保留 provenance 类别，不保存可反查真实 MOD 文本的路径或节点编号。
- Gate 或 Compiler 行为变更后，先跑全量 corpus 再宣称通过。
- `golden` 必须保持与真实修复原则等价；公开改写后重新跑 gate / 结构验收确认断言仍成立。
- 本目录是**验收资产**，不是中间产物；不要把它放进会被清理的 `.work/` 语义中。
