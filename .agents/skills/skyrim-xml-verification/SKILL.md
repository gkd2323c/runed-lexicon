---
name: skyrim-xml-verification
description: skyrim-mod-translator 的 XML 安全与验证规范：写回前后必须执行的验证清单、哈希与 provenance 的使用边界、性能与验证的职责分离原则。Use before or after any xTranslator XML writeback, when designing or reviewing XML-modifying tools, when deciding whether hashes/provenance need rebuilding, or when judging whether "工程验证通过" can be claimed.
compatibility: Reference rules only; no scripts.
metadata:
  version: "1.1.0"
---

# XML 安全与验证规范

## 1. Agent 与确定性程序的职责分离

按职责分工：

Agent / LLM 负责：语义理解、通读连续原文、建立整体剧情与人物理解、上下文判断、翻译、术语候选生成、一致性检查。

确定性程序负责：XML 读取和写回、节点定位、ID 映射、占位符保护与验证、结构完整性验证、diff 控制、批量统计、可重复执行的检查。

能用确定规则保证的事情，交给程序，不靠 prompt 约束。反过来，结构提取成功、记录关联成功、词典查询成功、哈希一致、验证脚本通过，都不能证明译文在语义上正确。"工程验证通过"与"语义翻译正确"是两个独立维度，分别确认。项目用程序消除机械错误，把 Agent 的精力留给无法机械化替代的语义理解，而不是让 Agent 沉入脚本与中间产物维护。

## 2. 修改前后的验证

任何批量修改 XML 的工具或脚本，至少验证：

- XML 仍能成功解析。
- `<String>` 数量没有意外变化。
- `<EDID>` / `<REC>` / `<Source>` 没有意外变化。
- 仅预期的 `<Dest>` 被修改。
- 必须保留的占位符在翻译前后保持一致。
- 不存在意外空译文。
- 不存在明显的 XML 转义损坏。

如能生成可审阅的修改摘要或 diff 更好。未执行验证时，不声称修改"已确认可安全导回"。

### 2.1 哈希与 provenance 的使用边界

哈希用于防止把译文写回错误版本的源文件，不用于制造无意义的重算工作。

- 源 XML 的哈希是写回前的重要门禁：确认当前 XML 仍与翻译时针对的源版本一致。
- 写回完成后，结合结构检查与 diff 验证实际修改范围。
- `CONTEXT.md`、`DICTIONARY.md` 或中间 context JSON 的哈希用于追踪来源和辅助诊断；文档小改动后不必把所有历史译文重新绑定或重建。
- 上下文或术语表更新没有实质改变某批译文依赖的语义时，旧译文继续有效。
- 只有新证据实际改变译法、说话者/剧情理解、占位符或其他影响译文正确性的内容时，才重验受影响批次。

验证机制服务于翻译和安全写回，不反过来支配工作流。

## 3. 结果口径

- 未实现的功能标注为未实现。
- 未验证的结果标注为未验证。
- 推测的剧情关系、说话者或记录关联标注为推测。
- 未经 xTranslator 实际导入或游戏内测试，就明确写未做这两项验证。

## 4. 中间数据可追溯性

Agent 友好的中间表示应保留可追溯性。每条待翻译文本至少能追溯到：原始插件、原始 XML 文件、`EDID`、`REC`、`Source`、当前 `Dest`、原始节点或稳定定位信息。任务、角色、Topic、书籍等更高层语义结构，也必须保留回写到原始 `<String>` 的稳定映射。

对 `DIAL -> INFO`、说话者、任务阶段等 Bethesda 记录关系：xTranslator XML 可能只保留可翻译字符串和有限元数据，丢失父子引用、条件、说话者等结构信息。只有 XML 本身存在明确关联字段，或从原始 ESP / ESM 等可靠记录源恢复出关系时，才作为确定映射；否则标注为启发式 / 推测。
