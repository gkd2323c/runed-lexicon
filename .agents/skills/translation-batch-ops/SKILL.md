---
name: translation-batch-ops
description: runed-lexicon 批次流水线的状态、覆盖、验收、对账重建与进度工具集。覆盖一轮收口的唯一串行入口（round_pipeline：consume→charset→check→write→verify→snapshot 单进程顺序执行 + 独占锁防并行覆盖）、批次产出机械验收（verify_subagent_batch）、批次状态覆盖率核查（check_batch_coverage）、计划覆盖缺口扫描与补遗批次切分（scan_plan_gaps）、整体进度快照（progress_snapshot）、大批次字符权重分片与合并（shard_batch）、翻译产出一键消费链（consume_batch）、批次文件对账与重建（batch_sync）、批次 context 单批重建（rebuild_context）。Use when 把验收批次收口至写回快照（一律走 round_pipeline）、验收翻译批次产出、核对批次状态/覆盖率、扫描未译行的计划归属缺口、切补遗批次、记录进度快照、拆分超大批次、合并翻译子代理分片交付、把子代理交付的 map 一键消费至验收就绪、处理批次文件与 canonical 的漂移或缺失（判向/拉平/补全，不逐条修补）、或修复备料 context 多批结构缺陷。Do NOT trigger for 翻译与词表裁决本身、契约编译（归 term-contract-compiler）。
compatibility: Requires Python 3.10+. Uses only the Python standard library. Expects the runed-lexicon project layout (.work/<plugin>/, mods/<plugin>/).
metadata:
  version: "1.4.0"
---

# Translation Batch Ops

批次流水线运维工具集。五个工具回答流水线的四个问题：

| 问题                     | 工具                               | 核心产出                                                          |
| ------------------------ | ---------------------------------- | ----------------------------------------------------------------- |
| 这批产出合格吗？         | `scripts/verify_subagent_batch.py` | 验收报告 `.work/<plugin>/reports/<BID>-verify-report.json`        |
| 批次推进到哪了？         | `scripts/check_batch_coverage.py`  | 每批状态（VERIFIED / TRANSLATED / PREPPED / MISSING）+ 未写回告警 |
| 还有未译行没有归属吗？   | `scripts/scan_plan_gaps.py`        | 缺口清单 + 补遗批次（GAP- 前缀）+ 机械匹配孤儿清点与核验卡点       |
| 整体进度如何？           | `scripts/progress_snapshot.py`     | 快照日志 `.work/<plugin>/reports/<plugin>-progress-log.json`      |
| 批次太大怎么派？         | `scripts/shard_batch.py`           | 按字符权重的 index 分片与 map 合并                                |
| 子代理交付怎么消费？     | `scripts/consume_batch.py`         | map 归位 → 展平 → immutable 同步 → fill → verify 一键链           |
| context 备料坏了怎么修？ | `scripts/rebuild_context.py`       | 单批模式重建 context + 骨架重生成（保留已有译文）                 |
| 批次文件与 canonical 漂移/缺失？ | `scripts/batch_sync.py`         | `check` 只读对账（含方向判定）/ `apply` 拉平 / `rebuild` 补全缺失    |

## 0. 标准续推循环（SOP）

推进一个 MOD 的翻译主线时，每一轮按同一序列执行（`<stem>` 为 `.work` 下的 MOD 名，`<BID>` 为批次号；命令均在项目根运行）：

1. **备料·索引**：`py -3 .agents/skills/mutagen-dialogue-exporter/scripts/make-batch-index.py <stem> <BID>` → 生成 `batches/<BID>/index.txt`（从主计划 JSON 提取该批 idx）。
2. **备料·context + 骨架**：`py -3 .agents/skills/translation-batch-ops/scripts/rebuild_context.py --stem <stem> --batch <BID>` → 单批全量 context.json + translation.json 骨架（mods 目录名自动探测）。
3. **备料·术语摘要**：`py -3 .agents/skills/translation-review-tools/scripts/term_digest.py --context .work/<stem>/batches/<BID>/context.json --out .work/<stem>/batches/<BID>/term-digest.txt`
4. **派单**：子代理 **write** 权限，产出落 `batches/<BID>/map.json`；译者轮换（hanako 分身 / butter）；任务卡按 `subagent-ops` 模板（输入指向 index.txt 与 term-digest.txt，禁枚举术语）。
5. **收口（唯一入口）**：`round_pipeline.py`（§5c）一条命令完成 consume→charset→check→write→verify→snapshot；语义 FAIL 时它停在写回前，裁决后重跑。

> **硬规则（2026-09-22）：步 5 的收口链禁止拆成手动并行命令。** 手动编排曾六次产生覆盖时序（快照记入写前态 200/205/213/216/220、charset 读到 fill 前空文、统计读到写前态）；write_translations 与 progress_snapshot 内置 pipeline.lock 守卫，持锁期间外部命令直接拒绝。独立的多批 consume / apply_fixes 并行仍允许（不碰 canonical，见 skyrim-tool-dev-rules §2 第 4 条）。

派单安全规范（体量上限、验收三查、送达纪律）以 `subagent-ops` / `hana-subagent-ops` 为准，本表不重复。

**新会话接手指南**：读 `mods/<mod>/PROGRESS.md` 取「下一批编号」→ 按上表从步 1 开跑 → 每轮收口走 `round_pipeline`（步 5）。步 1~3 为幂等备料，可安全重跑。

**收尾交接要素**：任何停止点（停下汇报、等待用户、等待后台结果）之前，把 `PROGRESS.md`（下一批编号、待做、快照）与 `SOP.md`（命令序列）确认到「新会话可直接开工」；无变化时确认即过。验收细则见 `skyrim-doc-system` §11。

## 1. 批次验收（`verify_subagent_batch.py`）

批次验收「三查」（格式 / 契约 / 语义）中格式查的全部与术语查的机械前置由本脚本执行；语义查仍须通读。用法：

```text
py -3 .agents/skills/translation-batch-ops/scripts/verify_subagent_batch.py \
  --plan .work/<plugin>/context/<plugin>-info-batches.json --batch <BID> --map <map.json> \
  [--xml <source-xml>] [--result <translation.json>] [--context <context.json>] \
  [--contract <compiled.json>] [--keep-list <keep.json>] [--report <report.json>] [--repair]
```

三段：A. 完整性（`set(map) == set(idx)`，缺/多/重合率 < 0.5 判键错位整批不可用）；B. 内容初筛（WAITING/空译、非法状态、KEEP≠原文需 `--xml`、英文残留需 `--xml`）；C. gate 段（`--result` 时跑 executor 的 validate 需 `--context`，加 quality_gate `--auto-bind`；不写 gate 报告——gate 的 `--report` 受命名契约限制只认 `*-gate-report.json`，验收不借用）。

**gate WARN 可见性（必读）**：gate 在 `--auto-bind` 模式下把「required target 未出现」报为 review candidate（WARNING 而非 FAIL）；C 段会把 WARN 明细行并入顶层 warnings 输出（`warnings=N` 与 `WARN <batch> GATE_WARN ...` 行同屏显示），报告在 `gate.warn_lines` 留全量。**验收纪律：`warnings>0` 时逐条核 WARN 明细**，不得只看 verdict；漏读即可放过真漂移：契约词条与 DICTIONARY 的 target 不一致时，gate 的 WARN 是机械暴露点，被吞掉则全库用错形直写回。术语裁决落盘时两处同步核一遍。

安全边界：默认只读，不写任何文件（`--report` 指定的验收报告除外）。`--repair` 才写盘，且只填 PENDING（已译条目跳过，符合幂等保护），经 `fill_translation_set` 执行，修后重跑验证。退出码 0 PASS / 1 FAIL / 2 用法错误。

输出角色登记：验收报告 `.work/<plugin>/reports/<BID>-verify-report.json`（同路径覆盖式演进）。

**context provenance 失配处置**（`error: context provenance mismatch for mod_terms` 等）：terms.json / 源 XML 更新后，旧 context.json 记录的哈希与 translation.json 失配。恢复序列（翻译已存于 map 不丢）：① `build_translation_context.py` 重建 context；② `translation_result.py init --force` 重初始 translation.json；③ `fill_translations.py` 从 map 回填；④ 再 verify。（CONTEXT.md 与 DICTIONARY.md 为人类文档：AI 直接阅读、不进入机器链路、不参与机器校验；其更新不触发重建。）

验收统一走本脚本，不另写一次性替代品；禁形以 gate / 契约执行，不做手抄表。

## 2. 批次状态与覆盖核查（`check_batch_coverage.py`）

对批次计划做全量状态扫描，输出每个批次的状态（VERIFIED 已验收 / TRANSLATED 已交卷待消费 / PREPPED 已备料待翻 / MISSING 未备料）与未完成清单：

```text
py -3 .agents/skills/translation-batch-ops/scripts/check_batch_coverage.py \
  --plan .work/<plugin>/context/<plugin>-info-batches.json --batches-dir .work/<plugin>/batches \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml
```

规则：① 声明「已完成批次 XX～XX」前必须先跑一次核对——按序推进时的跳号跳批会使整批漏译（INFO-123、INFO-107～111 即此模式：混在声明范围内、从未备料），只有全量扫描能发现；② 每轮收口时用它定位下一批候选。

**`VERIFIED` 不等于已写回**：`VERIFIED` 的定义只是「translation.json 已填充」，它会盖住「三查 PASS 但 writeback 从没跑」的批次——按「已验收」口径统计时它们看起来像已完成。因此**必须带 `--xml` 跑**：工具会把每批 idx 与 canonical 逐行对照，报出「译文已填、但 canonical 仍与 Source 相同」的批与行数（KEEP 行不计）。声明批次收敛前，未写回行数必须为 0。

## 3. 计划覆盖缺口扫描（`scan_plan_gaps.py`）

报出「未译（Source==Dest 且含非空白内容）且不在任何批次计划内」的行。它是与前两个工具互补的第三个盲区：`check_batch_coverage` 只看计划内批次，批次计划只认领「生成时收集到的行」，两条流水线（INFO 计划只收 linked NAM1、非 INFO 计划排除 INFO 前缀）之间可能存在从未被任何计划收走的行（玩家对话与非链接 INFO 行是常见形态）。这类行不被常规覆盖率工具看见，不专门扫描就会一直停留在未译集合里。

**机械匹配孤儿清点**：同口径报出「已译（`Source != Dest`）但不在任何计划内」的行。这类行是导出期词典自动匹配产物（AGENTS.md §3.1），同时逃过未译扫描与批次验收两个口径；机械门禁对其错配零命中（实测：46 处 `No.`→「是的。」全部通过 gate；单 MOD 640 行孤儿中 63 处系统错配）。清点与核验卡点：

```text
--orphan-list <path>        全量孤儿清单（idx/rec/source/dest）
--verified-orphans <path>   核验清单（JSON 数组或 {"verified": [...]}）；核验过的 idx 从余额扣除
--fail-on-orphans           孤儿未核验余额>0 时退出码 1（收敛声明卡点）
```

核验方式：对话链（INFO 的 prompt→response 配对）或 REC 族逐行核；修正走 patch 链；核验清单落 MOD `notes/`。用法：

```text
py -3 .agents/skills/translation-batch-ops/scripts/scan_plan_gaps.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --plan .work/<plugin>/context/<plugin>-info-batches.json \
  --plan .work/<plugin>/context/<plugin>-noninfo-batches.json
```

**检查点（硬规则）**：① 批次计划生成/更新后当场跑一次——覆盖率必须 100%，发现缺口立即处置；② 每轮收口与 progress_snapshot 一并跑；③ 任何收敛声明前跑 `--fail-on-gaps`（缺口>0 退出码 1）与 `--fail-on-orphans`（孤儿未核验余额>0 退出码 1），不得带缺口或未核验孤儿声明收敛。

**处置（`--write-plan`）**：把缺口切成补遗批次（`GAP-<族>-NNN` 前缀，与主计划编号空间隔离；同源不拆、贪心装批、默认 60 unique / 150 rows 每批），写 `.work/<plugin>/context/<plugin>-gaps-batches.json` 与每批 `batches/<GAP-batch>/index.txt`，之后随正常流水线备料/派单/验收。已存在的 gaps 计划自动计入「已认领」（重扫显示剩余缺口，天然幂等）；`--write-plan` 遇已存在计划默认拒绝（`--force` 强写）；批次目录已有 map.json 时拒绝改写索引。输出角色登记：`<plugin>-gaps-batches.json`（补遗计划）。

**批计划完成 ≠ 类别收敛（类别维度核查）**：批次计划只覆盖「计划生成时收集到的行」，可能整体漏收某个子类型——计划 100% 完成、快照批次全 PASS 也不代表类别收敛：canonical 里可能仍余着整类未译行（玩家对话、非链接 INFO 行是常见形态），它们从未进入任何批次。**声明某类别收敛前（如「INFO 战役完成」），必须跑 `scan_plan_gaps.py` 且缺口为 0 行**；发现缺口用 `--write-plan` 切补遗批次（`GAP-` 前缀）后走正常流水线。

## 4. 整体进度快照（`progress_snapshot.py`）

统计 MOD 翻译整体进度（canonical 全库分类 + 批次流水线），可记录到固定日志：

```text
py -3 .agents/skills/translation-batch-ops/scripts/progress_snapshot.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --source-xml mods/<plugin>/<plugin>_english_chinese.xml \
  --plan .work/<plugin>/context/<plugin>-info-batches.json \
  --plan .work/<plugin>/context/<plugin>-gaps-batches.json \
  --batches-dir .work/<plugin>/batches \
  [--record] [--json] [--note "<备注>"]
```

`--plan` 可重复：主计划与补遗计划（存在时）一并传入合并统计——只传主计划会使缺口批写回的译文不进流水线口径，战役交叉校验持续报「口径不一致」（差值恰为缺口批行数）。

四段输出：① 全库已译/总数与分类分布（INFO/DIAL/QUST/NPC_/BOOK/其他——让未开工类别可见）；② INFO 战役口径（canonical 与流水线双口径交叉校验，不一致时显式告警）；③ 批次状态（已验收/待消费/已备料/未备料），并在存在「已验收但未写回」时追加告警行（需带 `--xml`）；④ 较上次快照增量。性能基线：约 1.5 万条（4.6MB）规模的全量统计 **~0.8s**。

**口径交叉校验的语义（②）**：pipeline 只计批次侧 `status==TRANSLATED` 的行。批次侧残存 REVIEW 条目（值已定稿并写回 canonical、仅状态未转正）会使 pipeline 少于 canonical、持续报「不一致」。差异处置：求差（canonical INFO 已译集 − 源预译集 − 各批 translation.json 的 TRANSLATED 并集）→ 核对差异行批次值==canonical 值 → 将仍挂 REVIEW 的条目在 map.json 与 translation.json 中一并转正。2026-09-21 TheKalpicAnomaly 按此清 23 条后双口径一致。

输出角色：进度日志 `.work/<plugin>/reports/<plugin>-progress-log.json`（追加式；与上一条内容一致时自动跳过；`--record` 才写盘，默认只读）。

**记录与汇报节奏（约定）**：① 每轮写回收口后记录一条快照（`--record`）；② 会话内汇报附快照摘要（全库 %、战役进度、批次进度、增量），让用户随时把握整体进度；向用户报进度数字一律以快照为准，不凭记忆报数；③ 趋势汇总直接读日志数组（`snapshots`）。④ 「定期」= 随每轮写回在会话内自然发生，**不建 automation 定时任务**；统计与记录已由快照工具确定化，无需后台调度。

## 5. 批次分片（`shard_batch.py`）

大文本批（单批源文 >10000 字符，如 BOOK 书籍正文）按**字符权重**而非条数拆分——一条 2700 字符的书相当于一个小批次，按行数切会失衡：

```text
py -3 .agents/skills/translation-batch-ops/scripts/shard_batch.py split --stem <S> --batch <B> --parts 2
# -> index-part-a.txt / index-part-b.txt（贪心平分字符权重）
# 子代理各持自己的 index-part-<x>.txt，写 map-part-<x>.json
py -3 .agents/skills/translation-batch-ops/scripts/shard_batch.py merge --stem <S> --batch <B> --parts a b
# -> map.json；分片键重叠或合并键集 != index.txt 时拒绝（退出码非零）

# 手动拆半场景（失败后拆半，产 map-blockX.json）：--pattern 指定文件命名
py -3 .../shard_batch.py merge --stem <S> --batch <B> --parts blockA blockB --pattern "map-{lab}.json"
```

`split` 按源文字符权重贪心平分，让两片承载量接近；按行数切会失衡：一条长文抵几十条短文。`merge` 默认找 `map-part-<lab>.json`，`--pattern` 可适配其它命名（分片键重叠、合并键集与 index.txt 不等均拒绝）。

**maps/ 分片兼容（v1.1.0）**：翻译子代理按分片各自交付时，产物通常落在 `maps/<BID><lab>-map.json`（如 `GAP-INFO-002a-map.json`）且值为扁平 `{idx: "译文"}`。merge 自动回退到该路径与形态（扁平字符串自动升格为 object 形态）。

```text
py -3 .../shard_batch.py merge --stem <S> --batch GAP-INFO-002 --parts a b \
    --pattern "../../maps/{lab}-map.json"   # 或省略 pattern，自动尝试 maps/<BID><lab>-map.json
```

## 5a. 子代理产出一键消费（`consume_batch.py`）

把「翻译产出 → 验收就绪」的机械链条收敛为一个命令（2026-09-20 GAP 战役沉淀；
此前此链条散落在多个一次性脚本中，属流程缺陷）：

```text
py -3 .agents/skills/translation-batch-ops/scripts/consume_batch.py \
  --stem <plugin> --batch <BID> \
  --xml mods/<plugin>/<plugin>_english_chinese.xml \
  --contract .work/<plugin>/contracts/<plugin>.compiled.json
```

五步链（每步可独立失败退出，不产生半成品状态）：

1. **归位**：优先 `batches/<BID>/map.json`；否则从 `maps/<BID>-map.json` 复制；
2. **展平**：扁平 `{idx: "译文"}` 自动升格为 `{idx: {translation, status, confidence}}`
   写 `map.filled.json`（object 形态原样通过）；
3. **immutable 同步**：`review_reasons` / `protected_tokens` 以 context 为唯一真相，
   用 `translation_result.review_reasons()` / `protected_tokens()` 原函数重算——
   **禁止手写近似逻辑**（曾因手写推断导致 immutable field changed FAIL）；
4. **fill**：`fill_translations.py --force --overwrite`（map 为唯一真相源，已译也覆盖）；
5. **verify**：`verify_subagent_batch.py` 全链（--plan 自动探测：GAP- 前缀走 gaps 计划）。

前置条件：批次目录已有 `context.json` 与 `translation.json`（备料产物）；context 必须
单批结构（见 5b）。退出码：0 PASS / 1 链中失败 / 2 用法错误。

**非 INFO 批次须显式 `--plan`**：计划自动探测只认 `GAP-` 前缀（→ gaps 计划）与默认 info 计划；`NI-*` 等非 INFO 批次必须传 `--plan .work/<plugin>/context/<plugin>-noninfo-batches.json`，否则 verify 报 `unknown batch`。拆半批次先 `shard_batch.py merge` 归并 `map.json` 再消费。

## 5b. context 单批重建（`rebuild_context.py`）

修复备料 context 的多批结构缺陷：`build_translation_context.py` 若以 `--batch-size 1`
备料，会生成 N 批每批 1 条的 context；而 `translation_result.expected_items()` 只认
`batch_index=0` 那批，validate 对其余单元报 `unknown translation_unit_id`。

```text
py -3 .agents/skills/translation-batch-ops/scripts/rebuild_context.py \
  --stem <plugin> --batch <BID>
```

行为：① `--batch-size 0` 重建 context（强制单批全量）；② 按新 context 重生成
translation.json 骨架；③ 已有译文（TRANSLATED 且非空）按 xml_index 保留，
immutable 字段一律以新 context 重算。mods 目录名与 stem 不一致（`.esp` 后缀）
自动探测；`--xml` / `--mod-terms` 可显式覆盖。

## 5c. 一轮收口唯一入口（`round_pipeline.py`）

把「consume→charset→check-only→写回→段核对→快照」整链收敛为**单进程严格顺序**执行，并持 `.work/<stem>/reports/pipeline.lock` 独占锁——从工具链层面拒绝并行（手动并行编排曾六次产生覆盖时序）：

```text
py -3 .agents/skills/translation-batch-ops/scripts/round_pipeline.py \
    --stem <plugin> --batches INFO-XXX INFO-YYY \
    --xml mods/<mod>/<plugin>_english_chinese.xml \
    --contract .work/<plugin>/contracts/<plugin>.compiled.json \
    [--phases consume,charset,check,write,verify,snapshot] \
    [--note "<快照备注>"] [--break-lock]
```

步骤（顺序固定，任一步失败即停、后续不执行，锁必然释放）：

1. `consume`：逐批 consume_batch（语义/契约 FAIL 即停，打印 verify-report fails 明细交主会话裁决后重跑）；
2. `charset`：逐批 normalize_charset 检查，有差异自动 apply + 重 consume（0 差异跳过）；
3. `check`：writer `--check-only` 预检（跨批 duplicate / scope / KEEP 冲突）；
4. `write`：逐批 writer `--in-place` 串行写回；
5. `verify`：段核对（各批 idx 在 canonical 中 Source==Dest 计数必须为 0）；
6. `snapshot`：progress_snapshot `--record`（同进程内写回落定后执行，hash 必然一致）。

锁语义：acquire 用 O_EXCL 创建（token/pid/phase/started）；子进程经 env `RUNED_PIPELINE_TOKEN` 继承 token；**write_translations 与 progress_snapshot 内置同锁守卫**，外部无 token 的独立命令一律拒绝（rc=2）——手动快照/写回撞上 pipeline 直接报错，不再产生写前态。stale 锁（进程崩溃遗留）用 `--break-lock` 清除。`--phases` 可选子集（如裁决后只补 `charset,check,write,verify,snapshot`）。

退出码：0 全链 PASS（尾行 `PIPELINE PASS canonical=<hash8>`）/ 1 某步失败 / 2 用法或锁冲突。

验证（2026-09-22 首跑，TheKalpicAnomaly）：全链 13 步 PASS，92 行写回 hash 链 `325ad903→06f50eb5→2635ec7a`，段核对与快照第 225 条同进程落定对账一致；守卫四场景（独立拒 / token 放行 / 无锁放行）单测过。性能：含双 consume 约 40s（consume 为主，写回本体仍秒级）。

## 6. 批次文件对账与重建（`batch_sync.py`）

canonical 是唯一真相源，批次文件（map.json / translation.json）是它的派生视图——可从 canonical 重建，不承载独立信息。**处置原则：一律机器重建（重新生成），不逐条手工修补、不逐案调查历史成因。**

```text
# 只读对账（报 A: map vs canonical / B: translation vs canonical / C: 批内）
py -3 batch_sync.py check --stem <plugin> [--xml <translated.xml>] [--limit 40]

# 拉平（canonical 或 patch → 批次文件）
py -3 batch_sync.py apply --stem <plugin> [--patch <fix-patch.json>] [--dry-run]

# 补全缺失（map.json 缺文件/缺行键；只增不改）
py -3 batch_sync.py rebuild --stem <plugin> [--batch BID] [--dry-run]
```

**处置对照（check 结果 → 动作）**：

| 对账结果 | 动作 |
| --- | --- |
| A/B 漂移，批次值命中共修正集（maps/） | 修正未写回：`make_patch_from_maps` + `write_translations --patch`（勿拉平） |
| A/B 漂移，canonical 新 | `apply` 拉平 |
| C 批内 map vs translation 不一致 | 重链该批：`consume_batch`（map 为批次内真相源） |
| map.json 缺文件 / 缺行键 | `rebuild` 补全（只增不改）→ `consume_batch` 重链 |
| translation.json 缺行 / 缺文件 | `consume_batch` 重链（immutable 字段须经 fill 重算，禁止手写） |

**方向判定**：`check` 会扫 `.work/<stem>/maps/` 下的修正集；漂移 idx 命中修正集时
判为「修正未写回 canonical」（处置：`make_patch_from_maps` + 写回），否则判为
「批次落后」（处置：`apply` 拉平）。判错方向就是数据丢失：拉平会冲掉未写回的修正。

**重建语义（`rebuild`）**：map.json 缺失时从 canonical 重建（仅 canonical 已译行，status=TRANSLATED）；文件存在但缺行键时只补缺失键，**绝不覆盖既有值**（值漂移属 `apply` 职责）。translation.json 不在此重建（immutable 字段必须经 fill 链路重算）；命令会检测 translation.json 缺行/缺失并列出需重链的批次。验证（2026-09-21，TheKalpicAnomaly）：补全 13 批缺失 map（INFO-001/174~177 + NI-* 8 批）、8 批 NI 重链回填，零漂移保持。

**自动环节**：跨批修正（`apply_fixes` 无 `--batch`、`make_patch_from_maps`）默认把
patch 值同步进批次文件，不需要事后手工跑。`--no-sync-batches` 可显式关闭。
`make_review_view.py` 生成视图前做一致性守卫：批次与 canonical 不一致时拒绝生成
（`--allow-drift` 跳过）。

## 6b. 由修正 map 生成 canonical patch（`make_patch_from_maps.py`）

对**已写回 canonical** 的条目做审查修正时，不能用 result 模式重放：增量模式的 `original_dest` 软保护会报 `original_dest mismatch` 并整批拒写。必须改用 `--patch`，其 `expected_dest` 取 canonical 的当前 Dest。

本脚本读 canonical 现值，为每条修正补上 `expected_dest` / `source`：

```text
py -3 .agents/skills/translation-batch-ops/scripts/make_patch_from_maps.py \
    .work/<mod>/maps/<mod>-fix-map-blockA.json [blockB.json ...]

# 显式指定（跨 MOD 目录或推断失败时）
py -3 .../make_patch_from_maps.py --canonical <translated.xml> --out <patch.json> <map.json>
```

约定：

- 输入 map 为扁平 JSON：`{ "<xml_index>": { "translation": ..., ... } }`，键是 xml_index（见 §6 坐标系），不是文件行号。
- canonical 默认由 map 路径 `.work/<stem>/maps/` 上推，按 `mods/*/<stem>_english_chinese_translated.xml` 匹配（`<stem>` 与目录名可不同，如目录带 `.esp` 后缀）。
- 同一 idx 在多个 map 中译文冲突 → 报错退出，不静默取后者。
- 输出默认 `.work/<stem>/maps/<stem>-fix-patch.json`，可直接喂给 `write_translations.py --patch`。

只读 canonical，只写 `--out` 指定的补丁文件。

## 7. 跨工具约定

**idx 坐标系**：批次目录 `index.txt`（`.work/<plugin>/batches/<BID>/index.txt`）里的数字是流水线的 `xml_index` 契约——即 ElementTree `findall('.//String')` 的 **String 元素序号**（从 0 开始），不是文件物理行号。用物理行号去 canonical 取行会落在 FURN/WEAP 等错误记录上（Ming 审计已验证）。派单/验收描述统一用「xml_index」或「idx 行号（String 元素序号）」，不要叫物理行号。（全工具链已统一 0-based；2026-09-17 前 `skyrim-xml-tools` 输出为 1-based，旧记录对照时先减 1。）

**同源句与专名分裂的全量核销**：用 `query.py --src "<源文片段>"` 列出同一英文句/词的全部出现位置与各自译文；发现多形时按 `skyrim-translation-craft` §8 收敛，改完复扫确认零分裂。

**术语本体 vs 修辞形**：词表 note 里的修辞说明不是术语译名（Blood Price=血价，note 中的"血债血偿"仅指整句修辞的译法）。术语本体位置必须用词表译名，修辞形只在整句修辞中使用。验收时对 note 含修辞/例外说明的术语，重点查本体位置是否用了正确译名。

## 8. 验证方式

```text
cd .agents/skills/translation-batch-ops/scripts
py -3 -m unittest test_batch_coverage test_scan_plan_gaps test_batch_sync
```

测试覆盖：覆盖率分类的未写回检测（含 KEEP 行不计）、缺口扫描的未认领行检测（含空白行排除）、补遗批次装批（同源不拆）、gaps 计划幂等与保护、活跃批次目录拒写、机械匹配孤儿的检测/清单落盘/核验清单两种形态扣除/--fail-on-orphans 卡点；
batch_sync 的双文件同步/已就位/缺失/mismatch 仍同步/dry-run、CLI check 方向判定、
make_patch_from_maps 的同步链、rebuild 的缺文件重建/补键（只增不改）/dry-run/--batch 过滤/缺 index 跳过/重链提示（覆盖判定）。

新命令（consume_batch / rebuild_context / shard merge maps 兼容）的验证方式：
对任一已消费批次做幂等复跑（应 PASS 且译文保留数不变），例如：

```text
py -3 .agents/skills/translation-batch-ops/scripts/consume_batch.py \
  --stem <plugin> --batch <已验收批> --xml <source xml> --contract <compiled.json>
py -3 .agents/skills/translation-batch-ops/scripts/rebuild_context.py --stem <plugin> --batch <已验收批>
# 重建后 translation.json 的 TRANSLATED 数应不变
```

修改任一脚本后同步跑上列测试；接口或行为变化时更新本 SKILL。

## 9. 工具化纪律

批次消费链、context 重建、分片合并均有正式命令（5a/5b/§5）；**禁止再为同类需求手写
一次性脚本**。遇到流水线断点时的正确顺序：先查本 SKILL 与相关 skill 是否已有对应能力
→ 无则在本 skill 内扩展子命令 → 确无通用性才落 `_tmp/scripts/` 且当轮清理。
