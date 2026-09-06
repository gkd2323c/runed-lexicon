# 失败恢复（先诊断再换方向，不盲重试）

## 节点超时 / 崩溃

- `subagent` 无响应：`check_pending_tasks` 确认状态；仍 pending 不轮询，
  先做手头其他工作，系统以 hana-background-result 回送结果。
- 超时本质多为 scope 太大（单节点 > 40 条或通读全文）：拆小重派，
  不加码重试同一 scope。重试节点 prompt 内注明“续跑 .work/ 已有产物，先 stat
  再接着 validate，不重做”。
- 中间产物必须写盘（result/map 即使 PENDING 也落盘），重试可续。

## 门禁 FAIL 路由

1. 看 gate `--report` 的逐条 issue，归类：术语（TERM002/TERM004）/ 占位符
   （PLACEHOLDER001）/ 繁简（CHAR001）/ 身份漂移（XML001）。
2. 打回原批次节点：同 thread `subagent_reply`（把 issue 全文贴回去），或新建
   `label=<plugin>-fix-<NNN>` 修复节点；禁止新建“全局修复”节点一把改多批。
3. 修复只改该批的 result JSON（经 filler，`--overwrite` + `expected_translation`
   保护旧值）；修完重跑 executor validate + gate 单批；双 PASS 才回 P4。
4. 若 FAIL 指向契约本身错误（binding 错、forbidden 误收）：先修 DICTIONARY/
   global-forbidden-words（主会话），重编译契约，再重跑 gate，不改译文凑合过。

## 越界 / 污染

- 节点写了 scope 外文件：在主会话用确定性命令核对（stat + git status），
  越界文件 revert 或移入 `.work/<plugin>-archive/`，不直接删证据。
- 疑似 map 引号污染（translation 值混入 `, "status":` 结构）：按
  translation-executor 废弃警告处理——手改源 map（中文引号），`json.load`
  验证后重跑 filler；`sanitize_translation_map.py` 已废弃，禁用。
- TRUNC001 WARNING：逐条对照 source 人审；真残缺补全，角色合法省略则忽略，
  不机械改写。

## 门禁测试与演练

- 测试 / 演练跑 gate 时**不要传 `--report`**：gate 对报告文件名执行确定性输出契约，
  只认 `<plugin>-gate-report.json`，另起名字直接 exit 2。演练看 stdout 的 verdict 即可；
  只有正式批次才写报告到 `.work/<plugin>-gate-report.json`（同路径覆盖）。
  （2026-09-07 hana-collab 自测实证：传 `_tmp/.../test-gate-0.json` 被拒，去掉即 PASS exit 0。）

## 写回失败

P5 单节点重跑即可（幂等，同名覆盖）。写回前后跑 `skyrim-xml-verification`
清单；哈希链记 PROGRESS。写回产物永远是 canonical 单文件，不另存
`final`/`v2` 分叉。
