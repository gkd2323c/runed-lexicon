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

## MOD 汉化发布流程

每个完成首轮收口的 MOD 走向 Nexus / 社区发布时走同一条链。以下各项按顺序执行；任何一步不达标就不进入下一步。

### 1. 收口状态核验（机器）

发布前置条件，全部机器可查：

- `scan_plan_gaps --fail-on-gaps` 缺口为 0
- `scan_plan_gaps --fail-on-orphans` 孤儿核验余额为 0
- `progress_snapshot --record` 最新快照显示的覆盖率与目标口径一致
- canonical XML 通过 writer 的 post-write validation（写回链已完成）
- `.work/<stem>/contracts/<stem>.compiled.json` 是当前 terms.json 的最新编译产物

任何一项不满足，回到 `translation-batch-ops` 与 `skyrim-term-contract-workflow` 补齐，不进入发布。

### 2. 独立审查完成（质量）

- 全局终审（整篇 canonical 通读，发现跨批/跨层问题）至少一轮，所有 high/medium 级问题已回正，low 级酌情处理或记录。
- 审查报告落盘 `.work/<stem>/reports/review-final-global.json`（或同语义命名），问题与处置可追溯。

### 3. 产物归置

- canonical：`mods/<plugin>/<plugin>_english_chinese_translated.xml`（xTranslator 可直接导入）
- 四文档：`CONTEXT.md` / `DICTIONARY.md` / `PROGRESS.md` / `SOP.md` 留在 `mods/<plugin>/`
- `terms.json` 留在 `mods/<plugin>/`（机器侧术语源）
- 发布简介：`<plugin>/NEXUS-INTRO.md`（或同语义命名）
- 封面图：归资料库（`OH-媒体库` 或同级集中区），不进 `mods/` 工作区
- 审查报告、专名清点、孤儿核验清单、进度日志留在 `.work/<stem>/reports/` 与 `.work/<stem>/notes/`

### 4. 发布简介必备要素

简介面向 Nexus 玩家，按以下结构写（顺序可调，要素不可缺）：

- 原 MOD 名 + 作者名 + 原址链接（占位符 `XXXX` 必须替换）
- 一句话剧情钩子（不剧透 boss / 反转）
- MOD 内容列表（任务规模、新增地点、敌族、机制、特殊文本）
- 汉化覆盖范围（总字符串数 / 类别分布 / KEEP 原则说明）
- 关键译名裁决（专名主线的取舍依据一句话）
- 使用说明（ESP 装入方式 / xTranslator 回导方式 / 起点提示）
- 已知问题与刻意设计（KEEP 项、语域分层、分支结构）
- 版权与致谢（原作者署名、汉化项目名、反馈渠道）

### 5. 封面图

- 比例 16:9 横构图，符合 Nexus 头图惯例；PNG 格式。
- 内容要求：能一眼识别 MOD 核心意象（主角/地点/核心怪物或奇观）；避免依赖文字标题传达题材（Nexus 列表缩略图下文字不可读）。
- 禁忌：不出现字幕、不出现屏幕文字、不出现水印、不出现真实厂商 logo、不直接拼接游戏截图。
- 审美倾向：符合 MOD 题材气氛（恐怖/航海/雪原等），不用通用奇幻模板凑数。

### 6. ESP 回导

- 用 xTranslator 将 canonical XML 导回 `.esp`（工具行为属外部流程，由发布者操作）。
- 回导前先开游戏或 xEdit 验证插件可加载、任务可触发；汉化文本显示无乱码（控制台 `sqv <quest>` 可核）。
- 发布物打包为「汉化 ESP」或「汉化 XML + 使用说明」，不打包原英文 ESP。

### 7. 署名与版权

- 简介与发布页必须保留原作者署名与原 MOD 链接。
- 汉化项目署名（runed-lexicon 或实际使用者）单独一行，不覆盖原作者位置。
- 若 MOD 作者另有授权要求（如需联系作者再发布），按作者条款执行。

### 8. 版本管理

- canonical SHA-256 记入 `PROGRESS.md`，每次发布后递增版本号（v1.0 / v1.1 …）。
- 发布后的每次 canonical 修改都走同一条「consume → write → verify → snapshot」链，不绕过 writer 直改 XML。
- 术语决策更新走 `terms.json` 修改 → 重编译契约 → 新一轮写回，不手工同步。

## 发布后建议

- 给仓库启用 secret scanning / dependency alert（托管平台支持时）。
- CI 至少跑 Python 单元测试、Skill quick validation，以及公开可分发的 regression cases（已落地：`.github/workflows/ci.yml`，3.10–3.13 矩阵，含 gate 47 条自测语料；注意 CI 需安装 `zhconv` 可选依赖以复现本地 PASS 基准）。
- issue 模板要求复现者不要上传完整 MOD / 游戏文件；必要时只给最小结构化样本。
- 每次引入新的外部数据源，先更新 `THIRD_PARTY.md`，再决定是否进入版本管理。
