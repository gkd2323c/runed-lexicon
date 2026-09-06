# mods/

本目录保存每个 MOD 的**本地翻译工作区**。真实 MOD 文件和翻译成品默认不进入公共 Git 仓库。

典型目录：

```text
mods/<plugin>/
  <plugin>_english_chinese.xml
  <plugin>_english_chinese_translated.xml
  <plugin>.esp / .esm / .esl        # 仅在结构恢复需要时
  <plugin>_dialogue_context.json     # 可选的 xEdit 导出
  CONTEXT.md
  DICTIONARY.md
  PROGRESS.md
```

三个 MOD 文档职责：

- `CONTEXT.md`：剧情、角色、任务、关系、语气、知识边界与已确认事实；
- `DICTIONARY.md`：MOD 专名、固定译法、KEEP / PROVISIONAL / REVIEW 等翻译决策；
- `PROGRESS.md`：当前有效输入输出、完成范围、验证状态与下一步。

开始实际翻译前，Agent 必须完整读取 `CONTEXT.md` 与 `DICTIONARY.md`。缺失时应停止翻译并询问是否根据实际 XML / 插件资料建立初版，而不是静默创建空模板。

不要把整个 `mods/` 目录强制加入公共仓库；如果某个作者明确允许公开测试素材，建议单独整理成最小、可再分发的 fixture，而不是直接提交完整 MOD 工作目录。
