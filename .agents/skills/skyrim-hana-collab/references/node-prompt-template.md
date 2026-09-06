# 节点 Prompt 模板（子 agent 是全新进程，prompt 必须自包含）

每个翻译节点 prompt 按此 8 段写，缺段即可能跑飞。方括号内按批替换。

## 模板

```text
【任务一句话】把 thedark 第 003 批（xml_index 80-119，共 40 条）翻成中文，
产出 .work/thedark-translation-003.json（init + fill + validate 全走完）。

【先读规则】（按顺序读，不凭记忆）
- AGENTS.md（项目根）
- GLOBAL.md、GLOSSARY.md（项目根）
- mods/thedark.esp/CONTEXT.md、DICTIONARY.md、PROGRESS.md
- .agents/skills/skyrim-translation-craft/SKILL.md（动笔前必读）
- .agents/skills/translation-executor/SKILL.md（按它的 init/fill/validate 走）
- .work/thedark-contract.compiled.json（术语契约，只执行已声明 binding）

【项目背景】项目根 <PROJECT_ROOT>；
源 XML：mods/thedark.esp/thedark_english_chinese.xml（以 PROGRESS 登记为准，
不要自行另选）；context：.work/thedark-context.json 第 3 批。

【任务范围】只做 xml_index 80-119；REC 不限但以 INFO:NAM1 为主；
不碰其他批次，不改 CONTEXT/DICTIONARY/PROGRESS，不碰 XML。

【工具命令】（精确到命令，产物路径写死）
py -3 .agents/skills/translation-executor/scripts/translation_result.py init .work/thedark-context.json --batch 3 --output .work/thedark-translation-003.json --force
# 写 map（中文引号“”，写完先 json.load 验证；ASCII 双引号禁入 translation 值）
py -3 .agents/skills/translation-executor/scripts/fill_translations.py --result .work/thedark-translation-003.json --map .work/thedark-map-003.json --output .work/thedark-translation-003.json --force
py -3 .agents/skills/translation-executor/scripts/translation_result.py validate .work/thedark-translation-003.json --context .work/thedark-context.json

【产物与格式】.work/thedark-translation-003.json（executor schema，TRANSLATED/KEEP/REVIEW
按其 SKILL.md 判定；PROVISIONAL 术语可继续翻，不以缺官方译名挂 REVIEW）；
map 文件 .work/thedark-map-003.json 一并保留备查。

【完成标准 + 验证】validate 无 PENDING、无 protected-token 变更；
如契约已就绪可附跑 gate 单批（FAIL 即在节点内修完再交）。

【边界】不改 <Source>/<EDID>/<REC>；不写 XML；不改任何 md 表；
保护 <Alias>/<Global>/%s/%d/{...}/[...] 原样；推测性说话人标注“推测”。

【交接】按 references/handoff.md 四字段返回 artifacts / verification /
changed_scope / residual_risks。
```

## workflow agent() 调用侧写法

```js
await agent(`<上段 prompt 全文>`, {
  label: "thedark-translate-003",
  access: "write",
  writeFolders: ["<PROJECT_ROOT>/.work/thedark-b003"],
  agentType: "hanako",
});
```

`writeFolders` 互斥是硬约束：并行节点不可声明同一目录。单文件两件套模式下，
可用同一 `.work/` 目录但文件名互斥；最稳的是每批一子目录。
读节点（纯审阅）用 `access: "read"` 且不声明 `writeFolders`。
