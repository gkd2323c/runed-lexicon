## 改动内容

<!-- 一句话说清这个 PR 改了什么 -->

## 提交前自查（CONTRIBUTING.md 边界）

- [ ] 未提交游戏 / MOD 原始文本、插件、完整汉化文件、xEdit 二进制
- [ ] 未提交 `.work/`、模型输出缓存、日志、密钥、本地绝对路径
- [ ] 回归案例为 synthetic 改写或已确认可再分发，不过度引用第三方文本

## Skill / 工具改动

<!-- 如涉及，填写；不涉及可删 -->

- [ ] 已完整读取 `skill-creator` 与 `skyrim-tool-dev-rules` 的 SKILL.md
- [ ] 已跑对应 SKILL.md 声明的最小验证
- [ ] 关键路径（quality gate / writer / term compiler）已跑单元测试与 regression corpus

## 翻译相关改动

<!-- 如涉及，填写；不涉及可删 -->

- [ ] 语义判断与工程验证分开说明
- [ ] 无后续剧情反向注入早期译文
- [ ] XML 改动只走确定性 writer，保留占位符与排版
