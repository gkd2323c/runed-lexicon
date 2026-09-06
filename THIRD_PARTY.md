# Third-party content and local data

这个仓库的目标是公开**工作流、原创文档、Agent Skills 与确定性工具**，而不是重新分发游戏、MOD 或第三方工具内容。

## 默认不进入公共仓库

| 路径 | 内容 | 处理方式 |
| --- | --- | --- |
| `dictionary/**/*.xml` | Skyrim / DLC / Creation Club 英中 xTranslator 语料 | 本地提供，Git 默认忽略 |
| `mods/**` | MOD 插件、xTranslator XML、翻译成品和工作资料 | 本地提供，Git 默认忽略 |
| `tools/xEdit/**` | xEdit 可执行文件、随发行版附带脚本和资源 | 用户自行安装，Git 默认忽略 |
| `tools/term-rules/*.txt` | 外部译者积累的术语转换规则 | 未取得明确再分发许可前不随仓库发布 |
| `.work/` / `_tmp/` | 中间结果、审计报告、上下文缓存、归档 | 本地运行产物，Git 默认忽略 |

`.gitignore` 只负责降低误提交风险，不改变任何第三方内容本身的版权或许可证。

## 仓库内的第三方代码

`.agents/skills/skill-creator/` 自带 `LICENSE.txt`，当前文件声明 Apache License 2.0。若公开保留该目录，应原样保留其许可证，并在首次发布前确认其来源与许可证覆盖范围。

其他 Skill 和脚本若未来引入第三方代码，也应在对应目录保留上游许可证 / NOTICE，并在这里补充说明。

## 回归语料

`corpus/translation-regression/` 的失败模式来自本项目真实发生并确认的翻译事故，但公开 fixture 已改为**语义等价 synthetic rewrite**。公开版本不保存真实 MOD 插件名、角色名、xml_index、审计报告路径或逐字台词；真实事故证据只留在被 `.gitignore` 排除的本地工作区。

官方 TES 术语测试是例外：当用例本身就是验证 `Argonian`、`Helgen`、`sweetroll` 等 canonical 译名时，会保留最小必要的官方术语名称，但周围句子由本项目重新编写。

新增事故进入公开 corpus 时继续遵循同一规则：先在私有现场保存完整证据，再抽取失败机制并重写公开 fixture。不要为了让测试“看起来完整”而把整段任务、书籍、对话或完整 MOD 文本复制进公共 fixture。

## 商标与项目关系

Skyrim、The Elder Scrolls、Bethesda、Creation Club、xTranslator、xEdit 以及各 MOD 名称均属于各自权利人。本项目是非官方的翻译工作流项目，不代表上述项目或作者背书。
