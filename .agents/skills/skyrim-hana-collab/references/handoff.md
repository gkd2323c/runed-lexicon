# 结构化交接（节点完成时的硬性 handoff）

orchestrator 不读懂你的译文也能验收：只核协议，不核文采。

## 返回形状（JSON）

```json
{
  "artifacts": [".work/thedark-translation-003.json", ".work/thedark-map-003.json"],
  "verification": {
    "command": "py -3 .agents/skills/translation-executor/scripts/translation_result.py validate .work/thedark-translation-003.json --context .work/thedark-context.json",
    "result": "PASS"
  },
  "changed_scope": ["xml_index 80-119", "TRANSLATED 36 / KEEP 3 / REVIEW 1"],
  "residual_risks": ["xml_index 97 说话人推测为守卫，无结构证据", "未作游戏实机验证"]
}
```

## 字段约定

- `artifacts`：本节点产出的文件（相对项目根）。orchestrator 会 stat 确认存在。
- `verification`：实际跑过的验证命令 + 结果（PASS/FAIL/N/A）。结果必须与节点
  prompt 的完成标准一致；要求 PASS 就别交 FAIL 或省略。
- `changed_scope`：改动的范围（xml_index 区间 / 状态统计 / 触及的 REC）。
  便于查越界：scope 外多改一行即事故。
- `residual_risks`：未验证 / 存疑 / 影响下游的事项；没有就写 `["none"]`。
  有风险必须明示，不许吞。术语疑问（如“XX 疑似新地名，暂按音译”）记这里，
  由 P1 或修复节点统一收敛，不在翻译节点内改表。

## 禁止

只写自然语言 summary 就交差（如“已完成，看起来没问题”）→ 交接不完整，
orchestrator 打回补交。summary 与 handoff 都写：summary 一句话说人话，
handoff 给结构化证据。

容忍：handoff 外面包一层 markdown 代码 fence 不算违规，主会话照读内层 JSON；字段齐全即视为交接完整，不以格式外衣打回。（2026-09-07 workflow 自测实证：两节点各包一层 fence 返回，四字段齐全，直接验收通过。）
