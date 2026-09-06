# UNSAFE-WORKFLOW-SCRIPTS-001

2026-09-05 将四个已确认存在危险行为的临时脚本从原调用路径撤下，原字节保存在本目录的 `.py.disabled` 文件中。没有执行这些样本，没有改动历史响应或翻译结果。改扩展名仅用于停用常规脚本入口，不是安全沙箱；禁止显式交给 Python 执行。

| 原位置 | 本目录原件 | SHA-256 |
| --- | --- | --- |
| `.work/robust_batch_runner.py` | `robust_batch_runner.py.disabled` | `850decadae2c5fb8d0b88567066db7d69a43459926429b241b2b73f728f85320` |
| `.work/fix_batch_01.py` | `fix_batch_01.py.disabled` | `38a8682f65e1c269181a19ec853c443fd8ffbe7fdf5a937bcf246e4c2317c09f` |
| `tmp_verdict.py` | `tmp_verdict.py.disabled` | `f60a2b9976446890c9f2b1cd2d133ea512b97ae24471fdce066891c769a21a2d` |
| `tmp_final_audit.py` | `tmp_final_audit.py.disabled` | `337b5a99777fa6cb3733ef11814e88ea1c66d4283665369318e49009d1072b2c` |

## 已确认危险行为

前两个脚本猜测恢复模型 ID、用原文兜底缺译、清空保护字段并直接赋值 `validation.ok=true`，未重新计算验证。第三个读错契约 `source` 为 `english` 并据此忽略警告。第四个将部分统计和误报判定硬编码到打印语句。

替代路径：local-model-translator 的 `import-map` 重新校验 raw 与结构化输出并生成 REVIEW map；translation-executor 的 `query` 展开证据，filler / fill_translation_set 应用显式修订。人工审校结论不由自动工具代判。

## 验证与边界

归档前在 `.work` 的 Python、Markdown 文件中检索四个脚本名，未发现引用；不是对所有外部调度器或其他平台调用的证明。归档后逐字节哈希一致，原路径不存在。当前最终译文是否曾受这些脚本影响未在本次验证，不能由源码风险倒推生产污染。

恢复须先核对哈希，再把对应原件移回表中原位置；目标存在时停止，不能覆盖。恢复只用于经确认的调查或正式修复，不能恢复危险生产路径。此目录属于工程事故证据，不进入翻译 gate 语料集合。
