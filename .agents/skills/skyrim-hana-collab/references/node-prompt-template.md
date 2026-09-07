# 节点 Prompt 模板（子 agent 是全新进程，prompt 必须自包含）

每个翻译节点 prompt 按此 9 段写，缺段即可能跑飞。下文以 thedark 为例，照批替换。

## 模板

```text
【任务】把 thedark 第 003 批共 40 条翻成中文。context 用 .work/thedark-context.json
第 3 批。交付 .work/thedark-translation-003.json 与 .work/thedark-map-003.json，
init、写 map、fill、validate 四步都要跑完。

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

## workflow 侧写法（以 script 字符串提交）

`workflow` 工具不直接吃 JS 调用：把扇出逻辑写成 `script` 字符串提交
（首行必须是 `export const meta = { name, description }`），批次表与 prompt
模板在脚本内用 JS 拼装，节点经 `agent(prompt, opts)` 派生。`opts` 常用：
`label`（批次名）、`access: "write"`（跑命令验证的节点必选）、`writeFolders`
（互斥的已存在绝对目录，见 SKILL.md §5）、`agentType`（人格路由，可省）。
并发与超时走 `limits`（如 `{ maxConcurrent: 5, nodeTimeoutMs: 900000 }`，并发严禁超过 5）。

```js
export const meta = { name: "druadach-p3-wave1", description: "29批并行翻译" };
const R = "C:/Users/gkd2323c/Documents/runed-lexicon";
const jobs = [
  { id: "qo-04", ctx: ".work/Druadach-questobj-context.json", batch: 4, kind: "任务目标短句" },
  // …一批一项，scope 互不重叠
];
function promptFor(j) { return `【任务】…（上段模板全文，路径用 ${R} 拼成绝对路径）…`; }
await parallel(jobs.map(j => async () => await agent(promptFor(j), {
  label: `druadach-${j.id}`,
  access: "write",
  writeFolders: [`${R}/.work/druadach-batches/${j.id}`],
})));
```

提交后 `workflow` 即返回后台任务 id，不阻塞。用 `wait_for_tasks` 登记等待
终态（结果经后台通道自动回送），不要轮询。失败时错误体带 `resumeFromRunId`，
用它重派，已完成节点可从缓存直接复用。读节点（纯审阅）用 `access: "read"` 且不声明
`writeFolders`。
