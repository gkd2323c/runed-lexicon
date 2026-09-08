# Contributing

欢迎改进翻译工艺、Agent Skills、确定性工具和回归测试。这个项目最看重的是：减少真实翻译事故，同时减少人工逐条复核的注意力成本。

## 提交前先确认边界

- 不提交 Bethesda / Creation Club 的原始文本语料、游戏资源或插件。
- 不提交未经作者许可的 MOD ESP / ESM / ESL、完整 xTranslator XML 或完整汉化文本。
- 不提交本机 xEdit 安装、第三方术语大表、`.work/`、模型输出缓存、日志、token、密钥或本地绝对路径。
- 回归案例如果引用第三方文本，应尽量保持为验证所需的最小片段，并在公开发布前确认可再分发性；不能确认时使用获得许可的测试素材或不随公共仓库发布。

## 修改 Skill / 工具

项目把可重复执行的工具放在 `.agents/skills/<skill>/`，每个独立 Skill 必须有自己的 `SKILL.md`。

实质修改 Skill 前，完整读取：

1. `.agents/skills/skill-creator/SKILL.md`
2. `.agents/skills/skyrim-tool-dev-rules/SKILL.md`

修改后至少执行 skill-creator 的环境 preflight 和 quick validation；环境存在 `skills-ref` 时再运行对应 validate。不要为了一个小需求另写临时脚本复制现有 Skill 的能力。

## 推送前本地复刻 CI

CI（`.github/workflows/ci.yml`）跑失败才发现问题太贵：推送前先在本地跑

```bash
python tools/pre-push-check.py
```

它按相同顺序复刻 CI 的三步（unittest discover、standalone 回归脚本、全 skill quick validation），5 秒左右出结果，失败即 exit 1。想让每次 `git push` 自动执行，安装一次 pre-push hook（`.git/hooks` 不随仓库发布，每台机器 clone 后装一次）：

```bash
python tools/pre-push-check.py --install-hook
```

改 CI 时同步改这个脚本，改脚本时同步检查 CI，两边步骤必须一致。

## 翻译相关变更

- 语义判断与工程验证分开说明。
- 不因为已知后续剧情而提前翻译出真实身份、阵营、亲缘或共指关系。
- 官方词典命中只是证据，是否适用于当前语境仍需结合上下文判断。
- 修改 xTranslator XML 时，只通过确定性 writer 改目标 `<Dest>`；不要用通用 XML serializer 或全局文本替换重写文件。
- 保留 `<Alias=...>`、`<Global=...>`、`%s`、`%d`、程序语义 `{...}` / `[...]`、实体和语义性换行。

## 测试与回归

工具变更应运行其 `SKILL.md` 中声明的最小验证。涉及 quality gate、writer、term compiler 等关键路径时，还应运行相关单元测试 / regression corpus，并确认性能没有从秒级退化到不可接受的范围。

不要仅因为脚本退出码为 0 就宣称“翻译正确”或“已实机验证”。

## 变更规模

保持 PR 聚焦。不要顺手格式化整个 XML、搬动无关目录、批量改名或重构一整套已经工作的流程。真实翻译需求优先于架构装饰。
