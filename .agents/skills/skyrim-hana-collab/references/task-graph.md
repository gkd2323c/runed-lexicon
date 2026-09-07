# 任务图 P0~P5

五个阶段顺序推进，P2 试点是放行门：管线没跑通之前不开批量。

## P0 准备（主会话 inline，不外包）

1. 四文档门禁：读 GLOBAL、GLOSSARY、MOD 的 CONTEXT/DICTIONARY；缺任一即停问用户。
2. 编译契约：`term-contract-compiler`（含 `--global-bans`）。
3. 拼 context：`translation-context-builder` 落盘 `.work/<plugin>-context.json`。
4. 切批：默认每批 20~40 条（context-builder `--batch-size` 按 MOD 规模与文本长短微调；
   单节点保证一轮能翻完并自验）。
5. 在 PROGRESS.md 登记源 XML 路径（多 XML 共存时后续节点照抄，禁止节点自选）。

## P1 术语（Ming，单个 subagent，access=write）

- 输入：noun-audit 报告 + DICTIONARY/GLOSSARY。
- 动作：noun-audit → Agent 消解 → 改 DICTIONARY/GLOSSARY → 重编译契约 → 复扫。
- 产出：重编译后的契约 JSON + 消解记录。
- 禁止：顺手翻译正文；一次改多个 MOD 的表。

## P2 试点（2 节点并行）

- 取前 2 批（约 40 条），跑通 init → fill → validate → handoff 全链路。
- 试点都 PASS 且无系统性问题（context 缺字段、切分错位、术语未绑定），才放行 P3。
- 暴露管线问题 → 打回 P0 修，不另起名目掩盖。

## P3 批量（并行扇出）

- 剩余批次一一映射到节点，scope 以 0-based `xml_index` 区间或 Source 清单界定，不重叠。
- ≤ 3 批：同一轮次连续调多个 `subagent`（`label=<plugin>-translate-<NNN>`）。
- ≥ 4 批：走 `workflow` 脚本并行扇出（门禁仍归 P4，不进扇出）。
- 节点内只许写自己的 result/map 两件套；术语疑问记 handoff，不改表。

## P4 门禁复扫（Ming 审校，主会话或单节点）

- `translation-quality-gate`（多 `--result` 一次跑完）+ `dictionary-noun-audit` 复扫。
- FAIL → 按 `references/failure-recovery.md` 打回原批次；PASS → P5。

## P5 确定性写回（单节点，永远不并行）

- `xtranslator-xml-writer` 写 canonical
  `mods/<plugin>/<plugin>_english_chinese_translated.xml`（同名覆盖式演进）。
- 写后跑 `skyrim-xml-verification` 清单；报告落
  `.work/<plugin>-writeback-report.json`（同路径覆盖）。
- 上一代 canonical 进 `.work/<plugin>-archive/`；PROGRESS 记哈希链。
