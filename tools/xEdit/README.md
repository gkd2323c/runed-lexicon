# Local xEdit installation

这个目录是 `xedit-context-exporter` 的默认**本地 xEdit 安装位置**，不是项目源码的一部分。

公开仓库只保留本 README；xEdit 可执行文件、发行版附带脚本、Themes、日志和缓存均由 `.gitignore` 排除。

使用结构上下文导出时：

- 可以把自己的 xEdit 安装放在 `tools/xEdit/`；
- 也可以按 `xedit-context-exporter/SKILL.md` 使用 `--xedit` 指向其他安装位置；
- 项目自己维护的导出脚本位于 `.agents/skills/xedit-context-exporter/scripts/ExportDialogueContext.pas`，不会依赖把整套 xEdit 发行版纳入版本管理。

请从 xEdit 的正规发布渠道获取并遵守其许可证。
