# Public release checklist

这个文件记录“本地工作区”变成“可公开仓库”前还需要人为确认的事项。它不参与翻译语义和 MOD 进度。

## 已完成的仓库卫生

- [x] `.work/`、`_tmp/`、Skill eval workspace 默认排除。
- [x] `dictionary/**/*.xml` 与 `mods/**` 默认排除，防止误提交游戏 / MOD 文本和插件。
- [x] 完整 xEdit 本地安装与外部术语规则默认排除。
- [x] 为 `dictionary/`、`mods/`、`tools/xEdit/` 保留公开 README 接口说明。
- [x] 清理已发现的开发机绝对路径；Skill 文档不再假定某个固定 Python 安装目录。
- [x] 初步扫描公共候选文件，未发现明显 API key / credential 文件。
- [x] regression corpus 已改为语义等价 synthetic fixture；真实 MOD 名、角色名、xml_index、审计路径与逐字台词已从公开 corpus 移除，并通过 47 条 gate 回归。

## 第一次公开前必须确认

- [x] **选择根项目许可证。** 已选择 Apache-2.0（根目录 `LICENSE`），与 `.agents/skills/skill-creator/` 自带的 Apache-2.0 同许可证，无兼容问题。
- [ ] **确认 vendored skill 来源。** `.agents/skills/skill-creator/` 带 Apache-2.0 `LICENSE.txt`，发布时确认来源记录并保留许可证。
- [ ] **检查 GLOBAL / GLOSSARY 的公开范围。** 两者是本项目原创归纳与翻译决策，但发布者仍应确认其中没有误纳入大段第三方原文。

## 建 Git 仓库时

项目当前规则禁止 Agent 未经明确要求自行 `git init` 或改写历史，所以这里只提供检查项，不自动初始化。

初始化后建议先执行：

```text
git status --short --ignored
git check-ignore -v dictionary/<some-file>.xml
git check-ignore -v mods/<plugin>/<plugin>.esp
git check-ignore -v tools/xEdit/<binary>.exe
```

确认真正准备提交的集合只包含原创代码、公开文档、允许再分发的 fixtures 和保留完整许可证的第三方代码，再执行第一次 commit。

## 发布后建议

- 给仓库启用 secret scanning / dependency alert（托管平台支持时）。
- CI 至少跑 Python 单元测试、Skill quick validation，以及公开可分发的 regression cases。
- issue 模板要求复现者不要上传完整 MOD / 游戏文件；必要时只给最小结构化样本。
- 每次引入新的外部数据源，先更新 `THIRD_PARTY.md`，再决定是否进入版本管理。
