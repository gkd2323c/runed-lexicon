# 从 xTranslator 导出官方英中词典

本教程说明如何把你**合法拥有**的 Skyrim SE/AE 官方中文文本，导出为本项目 `dictionary/` 可用的 xTranslator 英中 XML。

导出的 XML 只做**本地术语证据**，不要提交到公共仓库（`.gitignore` 已默认排除 `dictionary/**/*.xml`）。

> 以下步骤基于 xTranslator 1.5.x / Skyrim SE 模式。界面措辞随版本略有出入，大意一致即可。

## 前提

1. **Steam（或同等正版渠道）中文版 Skyrim SE/AE**，且已下载官方中文语言包。验证方法：游戏 `Data/Strings/` 下存在 `Skyrim_Chinese.STRINGS` / `DLSTRINGS` / `ILSTRINGS`。没有这三个文件，说明中文语言内容没装，导出的目标语言列会是空的。
2. **xTranslator**（Nexus Mods 134，可免费获取）。解压即用，把 `xTranslator.exe` 放在任意目录。
3. 导出的是 Bethesda 官方文本，版权归原权利人。仅供本地翻译参考，不要再分发。详见 `THIRD_PARTY.md`。

## 步骤

### 1. 启动并选定语言对

1. 打开 `xTranslator.exe`，在游戏模式选择中选 Skyrim Special Edition（只玩 SE 可在“选项 → 启动”里固定，省去每次选择）。
2. 源语言选 `english`，目标语言选 `chinese`。这一对语言决定了导出 XML 的 `<Source>` / `<Dest>` 语种，也是本项目工具链默认的输入形态。

### 2. 加载官方插件

1. 菜单选择「文件 → 载入 Esp/Esm 文件」。
2. 到游戏 `Data/` 目录，按需选择官方插件。核心清单：
   - `Skyrim.esm`
   - `Update.esm`
   - `Dawnguard.esm`
   - `HearthFires.esm`
   - `Dragonborn.esm`
   - Creation Club 内容：`cc*.esl`（按需全选；本项目现有 `dictionary/` 已覆盖大部分 CC 插件，可对照补缺）
3. 一次加载一个插件，逐个导出。不要一次全选混在一起，导出是一对一的。

### 3. 确认中文列有内容

插件加载后，主表会列出全部可翻译字符串。检查目标语言（中文）列：

- 有中文 → 正常，xTranslator 已从该插件对应的 `*_Chinese.STRINGS` 读出官方译文。
- 全空 → 先排查：游戏是否真装了中文语言包；`Data/Strings/` 下有无该插件的 `*_Chinese.*`；xTranslator 目标语言是否为 `chinese`。空表导出的 XML 没有证据价值，不要用。

### 4. 导出 XML

1. 菜单选择「文件 → 导出翻译 → XML 文件（xTranslator）」（英文界面：`File → Export Translation → as .XML file`）。
2. 保存，命名建议 `<Plugin>_english_chinese.xml`，例如 `Skyrim_english_chinese.xml`、`Dawnguard_english_chinese.xml`。命名不强制，工具递归读取整个 `dictionary/` 目录，但统一命名方便对照补缺。
3. 重复步骤 2–4，直到核心插件全部导出。

### 5. 放入并验证

1. 把 XML 丢进本仓库 `dictionary/`（可直接放根下，也可分子目录，递归读取）。
2. 验证工具链能读到：
   - `git status --short` 里**不应出现**这些 XML（被 `.gitignore` 排除）。如果出现了，停下来检查忽略规则，不要 commit。
   - 跑一遍 gate 自测，确认总数仍是 47/47（`translation-quality-gate` 的 `selftest_corpus.py` 不依赖本地词典也应全过；真正用到词典的是 `dictionary-noun-audit`，可任选一个 MOD 跑一次，看命中数是否符合预期）。
3. 从此不要混入任何社区汉化、MOD 词典或个人术语表。这个目录的每一条中文都会被当成“官方说过”，掺假会污染全部下游裁决。

## 常见问题

- **导出的 XML 很大，正常吗？** 正常。`Skyrim.esm` 量级最大，单个文件几十 MB。它们本来就不进仓库，体积只占本地磁盘。
- **繁体中文用户怎么办？** 官方只发布了简体中文 strings。先按本教程导出简体版；繁体需求走 xTranslator「工具 → 语言特定工具 → 简转繁」另行转换（注意校对社区俗称与官方译名的出入，简转繁不解决术语问题）。
- **游戏更新后要重导吗？** 官方插件文本随版本极少变化。一般不需要；若 Creation Club 新增内容，按步骤 2–4补导新增的 `cc*.esl` 即可。
- **传奇版（LE）用户？** 流程相同，xTranslator 启动时选老版 Skyrim 模式，strings 命名规则一致（`*_Chinese.*`）。

## 参考

- xTranslator（Nexus Mods 134）：工具本体与更新
- xTranslator GitHub README：Esp mode / Hybrid Mode / XML import-export / 从 strings 构建字符串对等功能说明
- 本仓库 `dictionary/README.md`：目录语义与证据定位
- 本仓库 `THIRD_PARTY.md`：第三方内容边界
