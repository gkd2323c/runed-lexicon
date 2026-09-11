---
name: noninfo-batch-planner
description: "Plan deterministic non-INFO translation batches for a Skyrim MOD XML: scan untranslated rows (Source == Dest), group by record family (CELL/NPC_/QUST/BOOK/...), pack into pipeline-ready batches (batches[].id + idx + per-batch index.txt), and write the plan consumed by translation-context-builder / verify_subagent_batch / xtranslator-xml-writer. Use when the INFO section of a MOD is done and the non-INFO categories (QUST logs, NPC names, CELL/WRLD/REFR placenames, BOOK titles, MGEF/SPEL names, FACT, MESG, containers, keys...) need systematic batch preparation before translation. This planner supersedes hand-slicing; it is the non-INFO counterpart of the INFO batch-plan pipeline. Read-only with respect to MOD XML."
compatibility: Requires Python 3.10+. Uses only the Python standard library.
metadata:
  version: "0.1.0"
---

# Non-INFO Batch Planner

切分非 INFO 类别的未译行，产出流水线可直接消费的批次计划。是 INFO 战役之后各类别系统收尾的备料工具；与 `translation-batch-preparer`（单批手工挑选的旧工具）不同，本工具面向全量切批、产出固定 schema。

## 核心模型

- **候选**：`Source == Dest` 且非空、`REC` 不以 `INFO` 开头的行。
- **分组单元**：同一条 `Source` 的全部行是不可分割组（共享同一译文）；同源行永不跨批。组序 = 首次出现序（XML 顺序）。
- **批次**：按 REC 大类（`:` 前缀）分家族，族内贪心装批；装批上限 `--max-unique`（distinct 源数，默认 60）与 `--max-rows`（行数，默认 150），单个超限组仍整组输出。
- **批 id**：`NI-<家族slug>-<NNN>`（家族按名排序、批号从 001 起），例如 `NI-CELL-001`、`NI-NPC-001`。

## 主要命令

```text
py -3 .agents/skills/noninfo-batch-planner/scripts/plan_noninfo_batches.py \
  --xml mods/Artaeum.esp/Artaeum_english_chinese.xml \
  --stem Artaeum
```

常用变体：

```text
# 只看统计不写盘
py -3 .../plan_noninfo_batches.py --xml <xml> --dry-run

# 只切指定类别（演练/分阶段）
py -3 .../plan_noninfo_batches.py --xml <xml> --recs "CELL:FULL,WRLD:FULL,REFR:FULL"

# 调整装批上限
py -3 .../plan_noninfo_batches.py --xml <xml> --max-unique 40 --max-rows 120
```

## 输出契约（确定性）

- 计划：`.work/<stem>/context/<stem>-noninfo-batches.json`（同名覆盖式演进）。
  `batches[]` 条目：`id` / `line`（家族名）/ `recs` / `unique_src` / `row_count` / `srcs` / `idx`。
  `id` 与 `idx` 字段与 `verify_subagent_batch.py` 的 `--plan` 兼容。
- 索引：`.work/<stem>/batches/<BID>/index.txt`（每行一个整数 xml_index）。

下游链路与 INFO 相同：

```text
plan → index.txt → translation-context-builder --index-file → translation_result.py init
     → 子代理翻译 → verify_subagent_batch --plan <plan> --batch <BID> → 增量写回
```

## 安全边界

- 只读 MOD XML；不修改、不重序列化。
- 不写 MOD 目录；只写 `.work/<stem>/` 下的计划与索引。
- 重复行只翻译一次（同源同批），但写回仍覆盖全部行——写回链路由 translation.json 的全 idx 覆盖保证，不靠本工具。
- 与 `DICTIONARY.md` / `terms.json` 无直接交互：术语命中由 translation-context-builder 在备料时附加。

## 边缘情况

- 单源组超大（如某词重复 80 行）：整组输出为一个批，可能超过 `--max-rows`；这是有意行为（拆开会导致同词两条译文）。
- 同一 Source 跨类别出现（如地名同时出现在 CELL 与 REFR）：v1 按家族分开装批，不做跨家族合并；译名一致性由词表与验收层保证。
- 空候选或过滤后为空：报错退出（exit 2），不产出空计划。
- `--dry-run` 不写任何文件。

## 验证方式

```text
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/noninfo-batch-planner
py -3 -m py_compile .agents/skills/noninfo-batch-planner/scripts/plan_noninfo_batches.py
py -3 .agents/skills/noninfo-batch-planner/scripts/test_plan_noninfo.py
```

`test_plan_noninfo.py` 覆盖：INFO/已译行排除、同源不跨批、装批上限切分、超大组整体输出、index.txt 与 idx 一致、REC 过滤、dry-run 不写盘、重跑逐字节一致。
