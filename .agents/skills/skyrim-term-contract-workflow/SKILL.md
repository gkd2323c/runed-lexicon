---
name: skyrim-term-contract-workflow
description: skyrim-mod-translator 的官方名词契约与质量门禁工作流：新 MOD 开工前与批次收尾后的强制流程（dictionary-noun-audit 候选发现 → Agent 消解 → term-contract-compiler 编译契约 → unit bindings → translation-quality-gate → 确定性写回 → 复扫），含 PASS 声明口径与已知工具行为。Use when starting a new MOD translation, finishing any translation/rectification batch, running or interpreting dictionary-noun-audit / term-contract-compiler / translation-quality-gate / xtranslator-xml-writer results, or writing convergence claims in reports.
compatibility: Reference workflow; no scripts. Consumes the pipeline skills dictionary-noun-audit, term-contract-compiler, translation-quality-gate, xtranslator-xml-writer.
metadata:
  version: "1.1.1"
---

# 官方名词契约与门禁工作流

开始任何新 MOD 的翻译前，把官方名词建成契约与门禁，再进入实际翻译；翻译 / 整改批次完成后用同一套机制复扫确认。机制来自 MVF1FollowerBeta.esp 多轮审计教训：仅靠手工高风险名单无法覆盖整个 `dictionary/` 的官方译名体系，人工名单每轮都会漏项。可靠做法是让官方证据直接驱动可执行的检查，由独立核查工具发现"Source 有官方名词、译文却没有对应译名"的漏检。

## 涉及的工具（均有配套 SKILL.md）

- `proper-noun-index`：读 `dictionary/` 建稳定索引，扫 MOD 源 XML / translation-result JSON，列出该 MOD 实际出现的官方人名/地名/派系名（含官方中文候选、支持文件数、别名风险提示）。定位是 Source 侧候选清单生成器——不译、不改、不判决；是否录为术语由 Agent 在消解时决定。
- `dictionary-noun-audit`：读 `dictionary/` 全目录 + 扫翻译成品 XML / translation-result JSON，报"Source 含官方专名、Dest 缺官方中文形式"的 CHECK 候选。定位是 candidate finder, not a judge——只报字符串事实，是否真错由 Agent 判（回指省略、上下文别称、语境特定译法是合法排除项）。
- `term-contract-compiler`：把 `DICTIONARY.md` / `GLOSSARY.md` 编译成机器契约（`terms` 段）。
- `translation-quality-gate`：消费契约 + unit bindings 做写回前机械门禁。
- `xtranslator-xml-writer`：确定性写回 XML。

## 开工前置步骤（按顺序）

1. **完整读取四个前置文档**（`GLOBAL.md` / `GLOSSARY.md` / MOD `CONTEXT.md` / `DICTIONARY.md`）。
2. **官方名词清点（proper-noun-index scan）**：对当前 MOD 的源 XML 跑一次 `scan`，得到该 MOD 实际出现的官方专名清单（按 ENTITY / ENTITY_LOW / MULTIWORD 分层，带官方候选与别名风险）。这一步在翻译前做：先知道 MOD 会碰到哪些官方人名地名，后续翻译与审计才有谱。
3. **词典候选发现（dictionary-noun-audit）**：对当前 MOD 的源 XML（或早期草稿 / translation JSON）跑一次审计，得到候选清单。默认覆盖多词专名 + 被官方强记录（WRLD/LCTN/CELL/NPC_/RACE/FACT…）证明的单字名；模糊单字（BOOK/MISC 等，如 Letter/Time）需 `--include-single` 或 `--entity` 点名。空译文与未翻译行（Dest 空或 == Source）也是候选。scan 与 audit 互补：scan 是翻译前清点 Source，audit 是翻译后查 Dest 漏项。
4. **Agent 逐条消候选**：对 scan 清单与每个 CHECK 候选判断——真误译（改 translation JSON）、合法回指省略（不改）、上下文别称 / 语境特定官方形式（不改并可在 DICTIONARY.md 备注）。scan 清单里的语义变体（如 Whiterun → 白漫城/白漫领）与别名风险词（Blades/Companions）需按 MOD 语境裁决，确认结果落入 MOD `DICTIONARY.md`（含禁止变体，如"严禁……/不得……"），再进入编译。
5. **建立官方名词契约（term-contract-compiler）**：把 `DICTIONARY.md`（需要时加 `GLOSSARY.md`）编译成机器契约，并**传入 `--global-bans global-forbidden-words.json`**（项目级全局禁用词库嵌入 `global_bans`，让本 MOD 零成本继承历史坏形态），产物存入 `.work/term-contracts/<plugin>.compiled.json`。契约是派生产物，`DICTIONARY.md` 是 canonical。
6. **生成 unit bindings**：对无歧义的官方 REQUIRED 词做逐 unit 绑定；对 `Blades` / `Companions` / `Dwemer` / `Falmer` / `Jarl` / `Thane` / `Dragonborn` 等 alias / 剧透边界词，只对 Agent 人工确认过的 unit 显式绑定；合法回指省略显式标 `required: false`。
7. **写回前跑 translation-quality-gate**：每个 translation-result JSON 必须 PASS（TERM001 / TERM002 / TERM003 / TERM004 / KEEP001 / KEEP002 / PLACEHOLDER001 / CHAR001 / XML001 零 FAIL），通过后才允许确定性 XML 写回。TERM004 来自契约内的 `global_bans`：对每个已翻译单元独立扫描项目级坏形态（source 英文锚点 + dest 坏形态），无论该行是否带 binding。门禁只验证声明过的绑定 + 全局词库，不代替语义判断。
8. **写回后再跑一次 dictionary-noun-audit**：对新生成的 translated XML 复扫，确认上一轮候选已消失、没有新引入的漏项。工具能发现契约 / 绑定覆盖不到的未声明遗漏，与 gate 互补。
9. **PASS 声明口径**：报告中写"契约绑定检查 PASS""dictionary-noun-audit 复扫 CHECK 候选已消解"或"本轮已确认实体收敛"，不写"全 MOD 官方术语 0 违规"，除非检查集合确实由 `dictionary/` + MOD Source 自动形成并覆盖全部绑定项。

## 已知工具行为注意

- `translation-quality-gate` 中 binding 的 `required_target` 不参与匹配判定，term 级 `match.accepted` 才是匹配依据；需要放宽复合地名（如 `裂谷监狱` 与 `裂谷城` 都接受）时改 term 级 `match`，不依赖 `required_target`。
- alias / 剧透边界词（含 `risk_flags`）只显式逐 unit 绑定，不做自动全局绑定。
- 全局禁用词库（`global-forbidden-words.json`）的收录边界见 GLOSSARY.md §6：只收跨 MOD 官方名词系统性坏形态；MOD 专有词、普通词、需上下文限定词不入库（否则 TERM004 会误报）。新发现的系统性坏形态回填词库后，重编译契约再跑 gate。
- 契约与 bindings 是派生产物，`DICTIONARY.md` / `GLOSSARY.md` 是 canonical；术语决策变更后重编译契约并重跑 gate。
- `dictionary-noun-audit` 是候选发现器，不是判决器：不编辑、不自动修、不把候选升级为 REVIEW；每个候选由 Agent 结合上下文与官方证据判断。默认 exit 0（找到候选也是成功执行），`--fail-on-flags` 才用于 CI 门禁。
- `proper-noun-index scan` 是 Source 侧清点器，不是判决器：它只列出 MOD 实际出现的官方专名与官方候选，不写 terms、不升级状态。索引是 `dictionary/` 的派生产物（整目录即官方词典，文件一律平等），词典变更后需重新 `build`。冠词大小写不作区分：索引把 `the Rift`/`The Rift` 折叠为同一词条（`The Rift`），scan 对行文小写 the 做对齐匹配，两种形态都能命中。单字强记录人名地名标 `ENTITY_LOW`（LOW 语义，需 Agent 裁决），不自动升级 CONFIRMED。
- 性能基线与回归触发线见各工具 SKILL.md 与 `skyrim-tool-dev-rules` §2（工具性能门禁）。
