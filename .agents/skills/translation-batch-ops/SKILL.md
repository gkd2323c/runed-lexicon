---
name: translation-batch-ops
description: runed-lexicon 批次流水线的状态、覆盖、验收与进度工具集。覆盖批次产出机械验收（verify_subagent_batch）、批次状态覆盖率核查（check_batch_coverage）、计划覆盖缺口扫描与补遗批次切分（scan_plan_gaps）、整体进度快照（progress_snapshot）、大批次字符权重分片与合并（shard_batch）。Use when 验收翻译批次产出、核对批次状态/覆盖率、扫描未译行的计划归属缺口、切补遗批次、记录进度快照、拆分超大批次、或在声明批次/类别收敛前做证据核查。Do NOT trigger for 翻译与词表裁决本身、XML 写回（归 xtranslator-xml-writer）、契约编译（归 term-contract-compiler）。
compatibility: Requires Python 3.10+. Uses only the Python standard library. Expects the runed-lexicon project layout (.work/<plugin>/, mods/<plugin>/).
metadata:
  version: "1.0.0"
---

# Translation Batch Ops

批次流水线运维工具集。五个工具回答流水线的四个问题：

| 问题 | 工具 | 核心产出 |
| --- | --- | --- |
| 这批产出合格吗？ | `scripts/verify_subagent_batch.py` | 验收报告 `.work/<plugin>/reports/<BID>-verify-report.json` |
| 批次推进到哪了？ | `scripts/check_batch_coverage.py` | 每批状态（VERIFIED / TRANSLATED / PREPPED / MISSING）+ 未写回告警 |
| 还有未译行没有归属吗？ | `scripts/scan_plan_gaps.py` | 缺口清单 + 可选补遗批次（GAP- 前缀） |
| 整体进度如何？ | `scripts/progress_snapshot.py` | 快照日志 `.work/<plugin>/reports/<plugin>-progress-log.json` |
| 批次太大怎么派？ | `scripts/shard_batch.py` | 按字符权重的 index 分片与 map 合并 |

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

报出「未译（Source==Dest 且含非空白内容）且不在任何批次计划内」的行。它是与前两个工具互补的第三个盲区：`check_batch_coverage` 只看计划内批次，批次计划只认领「生成时收集到的行」，两条流水线（INFO 计划只收 linked NAM1、非 INFO 计划排除 INFO 前缀）之间可能存在从未被任何计划收走的行（玩家对话与非链接 INFO 行是常见形态）。这类行不被常规覆盖率工具看见，不专门扫描就会一直停留在未译集合里。用法：

```text
py -3 .agents/skills/translation-batch-ops/scripts/scan_plan_gaps.py \
  --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
  --plan .work/<plugin>/context/<plugin>-info-batches.json \
  --plan .work/<plugin>/context/<plugin>-noninfo-batches.json
```

**检查点（硬规则）**：① 批次计划生成/更新后当场跑一次——覆盖率必须 100%，发现缺口立即处置；② 每轮收口与 progress_snapshot 一并跑；③ 任何收敛声明前跑 `--fail-on-gaps`（缺口>0 退出码 1），不得带缺口声明收敛。

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

## 6. 由修正 map 生成 canonical patch（`make_patch_from_maps.py`）

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
py -3 -m unittest test_batch_coverage test_scan_plan_gaps
```

测试覆盖：覆盖率分类的未写回检测（含 KEEP 行不计）、缺口扫描的未认领行检测（含空白行排除）、补遗批次装批（同源不拆）、gaps 计划幂等与保护、活跃批次目录拒写。

修改任一脚本后同步跑上列测试；接口或行为变化时更新本 SKILL。
