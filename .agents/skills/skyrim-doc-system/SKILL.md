---
name: skyrim-doc-system
description: skyrim-mod-translator 项目的文档体系规范：GLOBAL.md、GLOSSARY.md 与每个 MOD 的 CONTEXT.md / DICTIONARY.md / PROGRESS.md 五个文档的职责边界、收录标准、状态体系、术语优先级与维护规则。Use when creating or updating GLOBAL.md, GLOSSARY.md, or any MOD's CONTEXT.md / DICTIONARY.md / PROGRESS.md, deciding where a term or plot fact belongs, choosing term status (CONFIRMED/PROVISIONAL/REVIEW/KEEP), or resolving terminology priority conflicts.
compatibility: Reference rules only; no scripts.
metadata:
  version: "1.1.0"
---

# 文档体系规范

## 1. 术语表方向

维护项目级术语表，区分：原版官方术语、MOD 自创术语、角色名、地名、派系名、物品/法术/机制名、尚未确认的候选译名。术语表应记录来源、置信度和备注，避免把未经确认的单次翻译永久固化成标准译名。

## 2. `GLOBAL.md`：原版全局语境

项目根目录维护 `GLOBAL.md`，从 `dictionary/` 官方语料沉淀跨 MOD 可复用的 Skyrim 语义理解。它记录：原版世界观中稳定的政治、种族、宗教、地点、派系与称谓关系；易因同义词、共指或多义词造成误译的语义边界；各类记录类型的稳定语义功能；官方中文语料中可复用的风格规律与不能机械照搬的噪声；与信息揭示顺序、剧透风险相关的原版背景知识。

`GLOBAL.md` 不膨胀成词典副本，不保存某个 MOD 的局部剧情、原创专名或执行进度。官方词对查 `dictionary/`；MOD 局部信息由 `CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` 承担。`dictionary/` 新增语料时，只有新证据实质改变跨 MOD 的语义理解或稳定规则才更新 `GLOBAL.md`，不因词典文件数量变化机械重建全文。

## 3. `GLOSSARY.md`：跨 MOD 项目级术语

项目根目录维护 `GLOSSARY.md`，保存值得跨多个 MOD 复用的本地化决策：官方中文未覆盖但属于 TES 既有概念的专名；官方存在多个冲突译法、需要项目统一选择的术语；官方译法存在明确问题、需要稳定覆盖的情况；多个 MOD 反复引用的旧作/ESO/官方扩展术语；需要长期保持称谓层级差异的跨 MOD 概念。

`GLOSSARY.md` 不收录普通词，不复制官方固定译名，不保存单个 MOD 的原创术语。推荐状态：`CONFIRMED`、`PROVISIONAL`、`REVIEW`、`KEEP`、`OVERRIDE`。当前 MOD 明确需要不同译法时，在该 MOD 的 `DICTIONARY.md` 建立局部覆盖并说明理由，不改全局词条迁就单个 MOD。

新证据证明既有项目级决策错误时，直接修订原条目。

## 4. `global-forbidden-words.json`：项目级全局禁用词库

根目录 `global-forbidden-words.json` 是“坏形态档案”的 canonical 机器源，职责边界见
GLOSSARY.md §6（收录/不收录标准）。它是 `dictionary/`（正确译名权威）与
GLOSSARY.md（跨 MOD 决策）之外的第三类项目级文件：不记正确译名，只记
“官方名词的错误中文形态 + 正确形态 + 为什么禁”，由 term-contract-compiler
以 `--global-bans` 编译进各 MOD 契约，translation-quality-gate 以 TERM004 执行。

判定某个禁用词该放哪：

- 跨 MOD 官方名词的系统性坏形态 → `global-forbidden-words.json`（需 reason）。
- 单个 MOD 专有词的错误形态 / 正确译法 → 该 MOD `DICTIONARY.md`（表格内 严禁 备注
  会经 compiler 变成局部 term 的 forbidden，走 TERM002）。
- 正确的跨 MOD 译法决策（非错误形态）→ GLOSSARY.md。
- 官方词对 → `dictionary/`，不复制。

`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` 的职责与维护规则见 §5–§7。

## 5. `CONTEXT.md`：MOD 局部上下文

每个 `mods/<plugin>/` 目录维护 `CONTEXT.md`，保存"理解这个 MOD 才需要知道"的信息：MOD 基本信息与主题、故事前提与剧情阶段、主要角色身份/关系/性格/说话方式、任务结构与重要事件、地点/派系/组织/自创设定、对话中的称谓与指代、特殊文本风格（日记、碑文、古语、谜语）、已确认事实、待调查问题。

`CONTEXT.md` 区分"已确认事实"与"推测"，不为完整性编造剧情。翻译中获得会影响后续大量文本理解的新事实时更新它，避免后续 Agent 重复推理或前后不一致。普通单词译法不大量塞入；固定译法进 `DICTIONARY.md`。

## 6. `DICTIONARY.md`：MOD 局部术语表

每个 `mods/<plugin>/` 目录维护 `DICTIONARY.md`，记录值得跨文本保持一致的翻译决策：MOD 名称与副标题、自创角色/地点名、派系/组织/种族/阵营名、特殊物品/法术/能力/机制名、任务关键概念、特定含义的反复用词、明确保留英文或不翻译的名称、与原版译法不同的 MOD 特殊用法。已有稳定通用译法且无 MOD 特殊含义的普通词不加入。

每条术语包含：英文原文、中文译法、类型、状态、来源或依据、必要备注。

状态含义：

- `CONFIRMED`：已确认，作为固定译法使用。
- `PROVISIONAL`：暂定译法，可继续调整，是可继续工作的正常状态，不是等待用户拍板的阻塞状态。
- `REVIEW`：存在争议或上下文不足，翻译相关文本时必须复查。
- `KEEP`：明确保留原文，不翻译。

MOD 原创人名、地点名等能根据全文、拼写、发音、TES 命名习惯或剧情功能得到合理方案的专名，Agent 主动给出稳定、可回滚的暂定译名并继续翻译。`REVIEW` 应当非常少见：只有当不同译法会实质改变剧情理解、世界观身份、任务逻辑或核心概念，且通读现有文本后仍无法得到足够合理的判断时才使用。作者名、内部技术标签、测试字符串等明确不应本地化的内容用 `KEEP`，不为了降低未翻译计数制造中文伪译文。

### 6.1 翻译时的术语优先级

1. 原版官方明确固定译名。
2. 当前 MOD `DICTIONARY.md` 中有充分依据的明确覆盖或自创术语。
3. 项目级 `GLOSSARY.md` 中有充分依据的跨 MOD 默认决策；其中明确标记并有证据支持的 `OVERRIDE` 可纠正官方误译或不适用译法。
4. 当前 MOD 的 `CONTEXT.md` 与实际上下文。
5. 新生成的候选译法。

任何优先级不覆盖当前文本的信息揭示顺序与语义事实。MOD 明确采用不同命名体系时，可用 `DICTIONARY.md` 局部覆盖 `GLOSSARY.md`，记录原因。新证据证明现有术语表错误时，修正术语表，不迁就旧决定继续制造错误。

## 7. 术语决策责任

翻译 Agent 默认承担普通本地化决策责任。流程：先读足够上下文（必要时通读全文或完整剧情段落）；查官方词典或相关 TES 语料，区分既有世界观术语与 MOD 原创内容；对 MOD 原创内容形成可用方案并记录为 `PROVISIONAL`；后续出现更强证据时统一修订。用户不是普通翻译歧义的人工路由器；需要用户拍板的情况是例外，不是默认降级路径。

## 8. 三个文件的职责边界

- "这个词以后应该怎么翻？" → `DICTIONARY.md`
- "这句话为什么应该这么理解？" → `CONTEXT.md`
- "这一批具体文本翻到哪里了？" → `PROGRESS.md`
- "这个官方名词的坏形态为什么全局禁？" → `global-forbidden-words.json`（GLOSSARY.md §6）

三个文件都保持人工可读，允许 Agent 直接读取和维护。

## 9. `PROGRESS.md`：MOD 翻译执行状态

每个进入实际翻译阶段的 `mods/<plugin>/` 目录维护 `PROGRESS.md`，保存"这个 MOD 当前做到哪一步、哪些产物是当前有效结果"，让后续 Agent 不依赖聊天历史、零散 `.work/` 文件名或猜测恢复状态。典型内容：当前源插件 / xTranslator XML / xEdit 上下文文件；XML 基线统计；已完成记录范围与数量；`TRANSLATED` / `KEEP` / `REVIEW` / `PENDING` 状态统计；当前有效的结构化结果文件或批次范围；已实际执行与尚未执行的验证；当前阶段（翻译中 / 一致性审校 / 待写回 / 已写回待验收）；下一步。

`PROGRESS.md` 不保存：大段剧情或人物设定（进 `CONTEXT.md`）、固定译名与术语候选（进 `DICTIONARY.md`）、每条文本的完整 Source/Translation 副本（在结构化结果中）、未实际执行的验证结果。

维护规则：实际翻译范围、结果文件集合、状态统计或工作阶段明显变化时更新；按有意义的阶段节点维护，不每翻一条就更新；`.work/` 存在多代历史批次时，指出当前应继续使用的那一组；"已完成""已验证"必须来自实际结果与实际命令执行；计数尽量来自确定性脚本，人工估算必须标注；`PROGRESS.md` 不因 CONTEXT/DICTIONARY 的小改动频繁重建历史结果或重算无关哈希。
