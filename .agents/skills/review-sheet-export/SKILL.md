---
name: review-sheet-export
description: "把已翻译 MOD 的 xTranslator XML 与 Mutagen dialogue-context 合并，导出成一张人类可读的 xlsx 审校稿：对话按任务与插件内顺序成组排列，非对话按记录字段成组，未译单元格高亮，可自由排序筛选批注。Use when 需要人工通读译文、把稿子交给外部审校、按剧情顺序核对对话上下文、快速定位仍未翻译的字符串，或用户说「出一份审校稿 / 人读稿 / 对照表」「我要人工过一遍」「导出 Excel 看译文」「按剧情排序看看对话」时。Do NOT trigger for: 机械忠实度扫描（用 translation-fidelity-scan）、术语一致性审计（用 noun-consistency-scan / dictionary-noun-audit）、批次验收（用 verify_subagent_batch）、修改或写回 XML（用 xtranslator-xml-writer）。"
compatibility: Python 3.10+，依赖 openpyxl。只读项目输入，唯一写入是输出的 xlsx。
---

# 审校稿导出

## 这个工具解决什么

流水线的产物从 context 到 writeback 全是 JSON 和机器报告，agent 读得懂，人读不懂。真要人工通读一段对话、把稿子交给外部审校、或者核对某段剧情的上下文时，得从 JSON 里现拼。

本工具产出一张单表：对话按任务聚在一起、按插件内出现顺序排开，非对话按记录字段分组跟在后面，未译单元格标紫。人可以排序、筛选、加批注列，Excel 本身就是工作界面。

它只做导出，不承担其他环节：语境构建用 `translation-context-builder`，术语审计用 `noun-consistency-scan` / `dictionary-noun-audit`，批次验收用 `verify_subagent_batch`，写回 XML 用 `xtranslator-xml-writer`。需要这些能力时直接用对应 skill，不要在本工具上扩展。

## 前置条件

- `mods/<MOD>/` 下有译后 XML（`<base>_english_chinese_translated.xml`，缺失时回落到 `<base>_english_chinese.xml`）
- `.work/<工作目录>/context/` 下有 `*-dialogue-context.json`（由 `mutagen-dialogue-exporter` + `convert-dialogue-context` 产出）

两者缺一不可。没有 dialogue-context 时先跑备料，不要用本工具替代。

## 用法

```text
py -3 .agents/skills/review-sheet-export/scripts/export_review_sheet.py --mod <mods 下的目录名>
```

常用参数：

| 参数                  | 说明                                                                                         |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `--mod`               | 必填，`mods/` 下的目录名，如 `ExampleMod.esp`                                                   |
| `--work`              | `.work/` 下的工作目录名，默认取 `--mod` 去掉扩展名。MOD 目录名与工作目录名不一致时必须显式给 |
| `--xml` / `--context` | 显式指定输入路径，绕过自动定位                                                               |
| `--out`               | 输出路径，默认 `mods/<MOD>/<base>-review-sheet.xlsx`                                         |
| `--dialogue-only`     | 只导出对话段，丢弃非对话行                                                                   |
| `--root`              | 项目根目录，默认当前目录                                                                     |

示例：MOD 目录是 `ExampleMod.esp`，工作目录同名，直接 `--mod ExampleMod.esp` 即可。

## 输出列

| 列     | 内容                                                   |
| ------ | ------------------------------------------------------ |
| 序     | 全局行号，按导出顺序                                   |
| 段     | 对话 / 非对话                                          |
| 任务   | QUST 译名，无译名时回落 EDID                           |
| 类别   | context 的 category / subtype，如 `Topic / ForceGreet` |
| 对话   | DIAL 标题译名，无标题时回落 EDID                       |
| 说话人 | context 的 speaker_candidates，多个用分号连接          |
| 记录   | XML 的 EDID（可能是 `[06006C3A]` 形式的 FormID 占位）  |
| 字段   | XML 的 REC，如 `INFO:NAM1`、`QUST:FULL`                |
| 原文   | Source                                                 |
| 译文   | Dest                                                   |
| 状态   | 未译时标「未译」                                       |

排序：对话段按「任务首现序 → DIAL 序 → INFO 序 → 文档序」，非对话段按「REC → EDID → 文档序」。首行冻结、表头带筛选。

## 匹配规则（重要）

XML 不保存 FormID，只有 EDID；而一部分 INFO 记录在 XML 里根本没有 EDID，只有一个方括号占位 FormID。所以匹配分两轮：

1. 用 EDID 对 context 的 `editor_id`
2. 仍无归属时，取 `[XXXXXXXX]` 里的 FormID 对 context 的 `form_id`

第二轮是必需的。实测中它能把大量 INFO 从「非对话」拉回对话段，例如某 MOD 对话行从 2533 提升到 3084。少了这一轮，提示语（`INFO:RNAM`）和很多回复会全部掉进非对话段。

## 关键边缘情况

- **`speaker_candidates` 常常为空**。只有当 INFO 的条件里有 `GetIsID` 时才有值；用 VoiceType 或别名指向说话人的 MOD 整列都是空的。这不是工具的缺陷，是源数据的形状，列仍保留。
- **`editor_id` 可能整批为空**。有的 MOD 的 INFO 普遍没有 EDID（索引只有个位数），此时 formid 通道是唯一入口，命中数会远大于 EDID 命中数。
- **未命中不等于异常**。非对话记录（BOOK、NPC_、MESG、QUST 等）本来就不在对话索引里，落在非对话段是正常的。
- **未译判定是 `Source == Dest`**，与项目其他地方一致。已全译的 MOD 该列全空是正常的。

## 安全边界

- 只读 XML 与 dialogue-context，不改写、不规范化、不重新序列化。
- 唯一写入是 xlsx 产物，位于 `mods/<MOD>/` 下（该目录在 `.gitignore` 中，不会误提交）。
- 不触碰 canonical XML、map、contracts 或任何流水线中间产物。

## 验证

跑完后脚本会打印 JSON 报告，重点看四项：

- `dialogue_rows` / `non_dialogue_rows`：对话段占比是否符合预期
- `matched_edid_info` / `matched_formid_info`：两条匹配通道各自的贡献
- `unmatched`：应当约等于非对话记录数
- `untranslated`：与已知进度对照

实测耗时基线（供回归对照）：3086 条字符串约 1.0s，14968 条约 3.5s。远超 30s 说明输入规模异常或解析方式退化。
