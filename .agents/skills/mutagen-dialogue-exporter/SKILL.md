---
name: mutagen-dialogue-exporter
description: "Export deterministic DIAL→INFO dialogue structure from an original Skyrim ESP/ESM/ESL with Mutagen (.NET overlay parsing) in seconds, including topic/quest/branch links, per-INFO speaker (NPC name resolved via load-order link cache), prompts, response texts and condition function/runOn evidence. Also converts the Mutagen output into the xEdit-style dialogue-context shape consumed by translation-context-builder and extracts per-batch index files for its --index-file option. Replaces xedit-context-exporter (xEdit CLI takes over 10min or hangs; Mutagen parses Druadach.esm in ~1.3s). Use when INFO lines need their real DIAL topic / quest line attribution, when building task-line dialogue batches, or whenever plugin-grounded dialogue structure is needed without the xEdit GUI."
compatibility: "Requires Windows, .NET SDK 9+ (net9.0 build), the Mutagen repo cloned at tools/Mutagen (gitignored), and the target plugin's masters present in the installed Skyrim SE Data directory. Read-only: overlay parsing never modifies the plugin."
metadata:
  version: "0.2.0"
---

# Mutagen Dialogue Exporter

用 Mutagen（C# Bethesda 插件库）以 binary-overlay 方式秒级解析插件，导出确定性的
DIAL→INFO 对话结构。这是 `xedit-context-exporter` 的替代品：xEdit 命令行模式在
Druadach.esm（约 98MB，5235 DIAL）上超过 10 分钟不出完成标记，Mutagen 同一文件
**1.3 秒**出全量结构，且 speaker/quest/topic 解析同样来自插件结构而非猜测。

## 前置条件（一次性）

1. `tools/Mutagen/` 存在（克隆自 <https://github.com/Mutagen-Modding/Mutagen>，已加 .gitignore）。
2. .NET SDK：环境需安装 .NET 9 或 10 SDK（0.54.x 版 NuGet 包目标框架为 net9.0/net10.0；SDK 8 编不动）。检查：`dotnet --list-sdks`。
3. **不要走 NuGet 老包**：本地 nuget 缓存里的 `Mutagen.Bethesda.Skyrim 7.1.0` 是 2020 年旧版，API 完全不同（`SkyrimMod` 无 `CreateFromBinaryOverlay`）。本工程直接用 `ProjectReference` 指向 `tools/Mutagen/Mutagen.Bethesda.Skyrim/Mutagen.Bethesda.Skyrim.csproj`，与仓库源码同版。

## 导出器：scripts/DialogueExport

```powershell
dotnet build .agents/skills/mutagen-dialogue-exporter/scripts/DialogueExport/DialogueExport.csproj
dotnet run --project .agents/skills/mutagen-dialogue-exporter/scripts/DialogueExport -- `
  mods/<plugin>/<plugin>.esm `
  .work/<plugin>/context/<plugin>-mutagen-dialogue.json `
  "<SkyrimSE Data 目录>"
```

第三个参数缺省为 `D:\SteamLibrary\steamapps\common\Skyrim Special Edition\Data`；
master 解析按目标插件 TES4 头声明的 master 列表在该目录中查找（overlay 加载，不写盘）。

### 输出契约（确定性）

`.work/<plugin>/context/<plugin>-mutagen-dialogue.json`，同名覆盖，禁止版本后缀：

- `topics[]`：目标插件每条 DIAL：`formKey`（`<local>:<mod>` 或 `<mod>:<local>` 形）、`edid`、`topic`（FULL 标题）、`category`/`subtype`（ForceGreet/Hello/Goodbye 等）、`quest`、`questEdid`（跨 master 经 LinkCache 解析）、`branch`。
- `topics[].infos[]`：每条 INFO：`formKey`、`edid`、`prompt`（RNAM）、`speaker`+`speakerName`（ANAM 显式说话者，解析为 NPC 显示名）、`speakerFromCondition`（ANAM 缺失时从唯一 GetIsID(Subject) 条件解析的说话者 NPC 名；多个 GetIsID 或无法唯一确定时为 null）、`prev`（上一句 INFO，线程顺序）、`responses[]`（每回复一条，含全部候选文本）、`conditions[]`（`kind`=条件数据类型名、`runOn`=subject、`reference`=通用参考 FormKey、`paramLink`/`paramAlias`=特化参数）。

`conditions[].paramLink` / `paramAlias` 对五类对话高频条件解析特化参数（FormKey 或别名索引）：
GetIsID→`Object`（NPC 说话者条件）、GetInFaction→`Faction`、GetIsRace→`Race`、GetIsClass→`Class`、GetIsVoiceType→`VoiceTypeOrList`。
需要判定说话者但 ANAM 为空时，走 `speakerFromCondition`；它只在条件集中恰有一条 GetIsID 且指向具体 NPC 时输出。

FormKey 与 xTranslator XML 的 `[xxxxxxxx]` EDID 关系：xTranslator 对插件自身记录写
**load-order FormID**（如 `[05088C1F]`，`05`=插件 load 位）。连接取后 6 位局部 FormID
（local FormKey），在目标插件的 INFO 集合内足以唯一定位（Druadach 实测：11399 条括号
EDID 命中 11307，99.2%；剩余 92 条全部是 REFR/FURN/TES4/DIAL 非 INFO 记录，零漏网）。

## 拆批：scripts/split-info-lines.py + plan-info-batches.py

在仓库根目录运行（第一个参数为插件文件名主干，第二个参数缺省为 `mods/<stem>.esm`，.esp 插件须显式传目录）：

```text
# Druadach（.esm，默认路径）
python .agents/skills/mutagen-dialogue-exporter/scripts/split-info-lines.py
python .agents/skills/mutagen-dialogue-exporter/scripts/plan-info-batches.py

# 其他插件（如 .esp）
python .agents/skills/mutagen-dialogue-exporter/scripts/split-info-lines.py Artaeum mods/Artaeum.esp
python .agents/skills/mutagen-dialogue-exporter/scripts/plan-info-batches.py Artaeum mods/Artaeum.esp
```

- `split-info-lines.py`：Mutagen JSON × XML INFO 行连接，按 `questEdid` 聚合为任务线；
  产出 `.work/<plugin>/context/<plugin>-info-split.json`（线→主题→INFO：xml idx、prompt_idx、speaker、
  复读 responses）与 `.work/<plugin>/context/<plugin>-info-unlinked.json`。speaker 取值顺序：
  `speakerName`（ANAM）→ `speakerFromCondition`（唯一 GetIsID 条件）→ `speaker`。
- `plan-info-batches.py`：以任务线为批次边界，CAP=45 唯一未译源句；单主题超容量按
  INFO 均分子主题；跨批全局源句去重（同句在后批只占一次容量、也只需翻一次）。
  产出 `.work/<plugin>/context/<plugin>-info-batches.json`（批次 → line/dials/unique_src）。

批次命名 `INFO-001…`，批次 id 是稳定契约 ID；重跑覆盖。

## 对接 translation-context-builder：scripts/convert-dialogue-context.py + make-batch-index.py

`translation-context-builder` 消费 xEdit 风格结构（`dialogues[]` + `infos[].form_id`），
**无法直接消费本导出器的 Mutagen 原始 JSON**（直接传会 0 连接，静默 unmatched）。
两个适配器把链路接通：

```text
# 1. Mutagen 结构 → xEdit 风格 dialogue context（含 FormID 前缀自动推导）
python .agents/skills/mutagen-dialogue-exporter/scripts/convert-dialogue-context.py Artaeum mods/Artaeum.esp
#    → .work/Artaeum/context/Artaeum-dialogue-context.json

# 2. 从批次计划抽取单批次索引（供 --index-file）
python .agents/skills/mutagen-dialogue-exporter/scripts/make-batch-index.py Artaeum INFO-001
#    → .work/Artaeum/batches/INFO-001/index.txt

# 3. 生成批次上下文（注意显式传 --dialogue-context 与 --index-file）
python .agents/skills/translation-context-builder/scripts/build_translation_context.py mods/Artaeum.esp \
  --xml mods/Artaeum.esp/Artaeum_english_chinese.xml \
  --dialogue-context .work/Artaeum/context/Artaeum-dialogue-context.json \
  --index-file .work/Artaeum/batches/INFO-001/index.txt \
  --batch-size 0 --output .work/Artaeum/batches/INFO-001/context.json --force
```

完整流水线（新增 MOD 时按序执行）：

```text
DialogueExport → split-info-lines → plan-info-batches
                                     ├─ make-batch-index（逐批抽 idx）
                                     └─ convert-dialogue-context（一次）
                                                ↓
                          build_translation_context（--index-file 逐批）
                                                ↓
                          translation-executor → quality-gate → xml-writer
```

- `convert-dialogue-context.py`：结构映射（topics→dialogues、formKey→form_id、
speakerName/speakerFromCondition→speaker_candidates、questEdid→quest_edid）＋
**FormID 前缀自动推导**。xTranslator XML 的括号 EDID（`[0600F5AE]`）比 Mutagen 的
局部 FormID（`00F5AE`）多 2 位 load 位，脚本从 XML 采样括号 EDID 与 Mutagen 局部
FormID 交叉验证命中率，取最高频前缀——不写死、不猜 load order，推导结果打印在
stdout（Artaeum 实测 7735/7735 全命中）。输出 `.work/<stem>/context/<stem>-dialogue-context.json`。
- `make-batch-index.py`：从 `.work/<stem>/context/<stem>-info-batches.json` 按批次 id 抽 `idx` 到
`.work/<stem>/batches/<BATCH-ID>/index.txt`（每行一个 XML 索引）。

> 前缀推导报错（XML 与 Mutagen 无交叉命中）通常意味着 XML 与插件不是同一代次，
> 或该 XML 尚无任何本插件 INFO 行。

## Druadach 实测基线（回归对照）

- Druadach.esm：5235 DIAL / 8089 INFO，1297ms；
- INFO 连接率 99.2%，未译 INFO NAM1 10673 行全部归线，unlinked=0；
- 319 批（CAP=45，唯一源句 10462，批大小中位 42、最大 45）。
  行数>唯一句数是因为大量复读台词（守卫、市民套话），去重省 211 句 + 后续批次免审。

## 安全边界

- 只读：overlay 解析不改插件、不写 Data 目录、不进游戏目录。
- 永不提交 `tools/Mutagen/` 与 `bin/`、`obj/`（已 gitignore）。
- 缺失 master 时打印 WARNING 并继续，但 speaker/quest 解析会退化为 null——看到
  WARNING 先补 Data 目录再下结论。
- 结构事实优先：speaker 只有 ANAM 或可唯一解析的条件才落到 `speakerName`；
  不得从条件集小反推说话者（继承 xedit-context-exporter 的纪律）。

## 已知坑（禁止重踩）

1. `IConditionGetter` 无 `Function`，函数枚举在 `c.Data` 派生 getter 上，而 overlay
   的 `IConditionDataGetter.Function` 未实现 → 输出条件用类型名（`kind`=如
   `GetIsIDConditionData`）。条件数据本体经 `CreateDataFromBinary` 完整解析，特化
   参数可用接口模式匹配读出：`IGetIsIDConditionDataGetter.Object`、
   `IGetInFactionConditionDataGetter.Faction`、`IGetIsRaceConditionDataGetter.Race`、
   `IGetIsClassConditionDataGetter.Class`、`IGetIsVoiceTypeConditionDataGetter.VoiceTypeOrList`
   （IFormLinkOrIndex 形，用 `UsesLink()`/`Link.FormKey` 或 `UsesAlias()`/`Index`）。
   实现在导出器 `FlattenLink<T>` 与 `SpeakerFromConditions`。
2. `IFormLinkNullableGetter` 无 `.Value`，用 `.FormKey` / `.FormKeyNullable`。
3. `ITranslatedStringGetter` 不是 string，用 `.String` 显式取。
4. `dotnet run` 在 sandbox shell 里 cwd 行为不稳，路径一律传绝对路径、用
   `dotnet <dll>` 最可靠。
5. overlay 实例 dispose 前不能 `using` 掉再建 LinkCache（本工程 masters 不 dispose，
   进程退出统一回收）。
