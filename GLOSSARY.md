# GLOSSARY.md — 项目级跨 MOD 译法决策

`GLOSSARY.md` 记录跨多个 MOD 复用的项目级译法决策。

它位于官方证据与 MOD 局部术语之间：`dictionary/` 提供官方用例，`GLOBAL.md` 提供原版语义背景，`GLOSSARY.md` 保存需要跨 MOD 统一的本地化选择；单个 MOD 的特殊用法进入其 `DICTIONARY.md`。

---

## 1. 收录范围

适合收录：

- Skyrim 官方中文未覆盖、但会在多个 MOD 中反复出现的 TES 既有专名。
- 官方存在多个冲突译法，需要项目统一选择的术语。
- 官方常见译法存在明确问题，需要稳定覆盖的术语。
- 来自旧作、ESO、Creation Club 或其他官方内容，并具有跨 MOD 复用价值的名称。
- 需要长期保持专名 / 通称、古称 / 现代称呼、自称 / 外称差异的概念。

不收录：

- `Gate -> 大门` 一类普通词。
- 单个 MOD 独有的人名、地点、机制或剧情概念。
- 一次性措辞、人物关系、剧情事实和工作进度。
- 已能从 Skyrim 官方词典稳定得到唯一答案、且不存在项目级争议的固定译名。

本文件保持少而稳定，不作为 `dictionary/` 的副本。

---

## 2. 状态

- `CONFIRMED`：跨 MOD 固定使用。
- `PROVISIONAL`：已有可用方案，允许后续根据更强证据修订。
- `REVIEW`：不同方案会实质改变世界观、身份或剧情理解，现有证据不足。
- `KEEP`：保留原文。
- `OVERRIDE`：项目明确覆盖某个官方常见译法，并记录覆盖原因与证据。

`PROVISIONAL` 是正常状态；普通音译取舍和未来润色不构成 `REVIEW`。

---

## 3. 条目格式

| English | 中文 | 类型                 | 状态        | 依据 / 来源                   | 备注               |
| ------- | ---- | -------------------- | ----------- | ----------------------------- | ------------------ |
| 示例    | 示例 | 种族 / 地名 / 术语等 | PROVISIONAL | 官方语料 / 旧作 / 多 MOD 用例 | 必要时记录称谓边界 |

涉及称谓层级或信息揭示时，备注应保留必要边界，例如哪些英文形式不能互换、哪些名称属于特定文化或特定知识阶段。

---

## 4. 叙事与称谓边界

世界观上的共指关系不等于词汇上的完全同义。

- `Falmer` 与 `Snow Elf` 存在历史关系，但承担不同称谓与信息阶段。
- `Maormer` 与 `Sea Elf` 即使共指，也可能处于不同文化或叙事层级。
- `Dwemer` 与 `Dwarven` 分别更常对应民族 / 文明主体与传统形容词 / 装备命名层。
- 真名、化名、绰号、古称、自称和外称默认保留其差异。

`GLOSSARY.md` 统一的是需要统一的概念，不消灭原文中有意义的称谓差异。

---

## 5. 全局决策与局部覆盖

项目级术语通常作为跨 MOD 默认译法使用。MOD 明确采用不同命名体系、赋予新含义、使用古称 / 别称，或需要保留不同知识阶段时，可在该 MOD 的 `DICTIONARY.md` 建立局部覆盖并说明原因。

`OVERRIDE` 仅用于有明确证据支持的项目级纠正，例如官方译法冲突、明显误译、破坏关键世界观区分，或后续官方内容已经形成更稳定的名称。

新证据证明既有项目级决策错误时，直接修订原条目。

---

## 6. 项目级全局禁用词库（global-forbidden-words.json）

根目录 `global-forbidden-words.json` 是**项目级坏形态档案**的 canonical 机器源：
只记录“官方名词的错误中文形态 + 正确形态 + 为什么禁”，供
term-contract-compiler 在编译每个 MOD 的契约时以 `--global-bans` 嵌入，由
translation-quality-gate 以 TERM004（全局 KEEP 用 KEEP002）对每个已翻译单元
独立执行。它与 `dictionary/`（正确译名的唯一权威）互补：词库本身不收录也不
复制正确译名大全，只收系统性错误形态。

收录标准（全部满足才收录）：

- **官方名词的系统性错误形态**，有跨 MOD 复用价值（同一角色/地名/种族在多个
  MOD 出现），或由真实事故 / 审计确认的高频错译。
- 英文锚点（`english`）在该名词的常见语境下**无同形歧义**，或歧义可由上下文
  消解（source 侧整词锚点 + 复数/所有格容错已由 gate 处理；若一个英文词形
  在诗歌等语境中作普通名词，如 blades，则不能仅凭词形收，需上下文限定或
  不收）。
- 每条 reason 必须写清“为什么禁”（事故出处 / 官方证据 / 防混淆对象），让
  gate 报错可解释、可判定误报。

不收录：普通词（Gate→大门 类）、MOD 专有原创名词（进各 MOD 的
DICTIONARY.md，不进全局）、仅靠上下文才成立的禁令、无法给出明确 canonical
target 的猜测性音译。`keep` 数组只收“项目级明确保留英文、所有 MOD 通用”的
值（如作者署名），MOD 局部 KEEP 留在各 MOD。

维护：审计或翻译中确认一种新的系统性坏形态时，回填本文件（含 reason），
并重编译相关 MOD 的契约再跑 gate；词库随时间累积，收录过的坏形态以后都能直接拦住。

---

## 7. 当前项目级词条

以下词条来自对已完成 MOD（MVF1 / SB1 / dg04 / meresis）术语决策的归纳，逐条经 `dictionary/` 官方语料核验；仅收录官方未稳定覆盖、存在跨 MOD 复用必要、或存在称谓边界需要项目统一约束的决策。官方 Skyrim / DLC 已稳定固定且无歧义冲突的译名不在此列（直接查 `dictionary/`）。

| English                           | 中文                                | 类型               | 状态        | 依据 / 来源                                                                                        | 备注                                                                                                                                                                    |
| --------------------------------- | ----------------------------------- | ------------------ | ----------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Housecarl                         | 侍卫                                | 头衔               | CONFIRMED   | 官方 HearthFires（“私人侍卫”）；GLOBAL §4                                                          | 领主封赐的宣誓贴身近卫（如莱迪亚）。官方语料中“the Jarl's housecarl”随语境亦作“总管”，本项目固定为“侍卫”，避免与 Steward（总管）混同                                    |
| Vigilant / Vigil of Stendarr      | 警戒者 / 斯丹达尔警戒者             | 组织               | CONFIRMED   | 官方 CLAS:FULL“警戒者”；meresis DICTIONARY                                                         | 斯丹达尔教团成员。meresis 曾误作“斯丹达尔警戒者之光”，已回退。组织与成员统一“警戒者”                                                                                    |
| Mysticism                         | 秘法                                | 学派               | CONFIRMED   | TES 旧学派（湮灭/晨风官方文本作“秘法”）；SB1 DICTIONARY                                            | 非 Skyrim 六系之一，属旧大陆学派体系；Skyrim 官方词典无此词条。严禁译“神秘主义/神秘学”（已入 global-forbidden）                                                         |
| Murkmire                          | 墨沼                                | 地名               | PROVISIONAL | ESO 地区名；SB1 DICTIONARY                                                                         | 黑沼泽的 ESO 地区；与“黑沼泽”（Black Marsh 大区）区分。若后续改音译“默克迈尔”需全局修订                                                                                 |
| Sea of Ghosts                     | 幽灵海                              | 地名               | CONFIRMED   | 官方 Skyrim/CC 语料（“幽灵海”固定，如“掉进幽灵海的小学院”）                                        | 天际以北寒冷海域；官方统一为“幽灵海”（不带“之”）。meresis 曾作“幽灵之海”，应改为官方形式；本项目以“幽灵海”为跨 MOD 默认                                                 |
| The Dragonborn Comes              | 龙裔归来                            | 歌曲名             | CONFIRMED   | 官方歌集；dg04 DICTIONARY                                                                          | 吟游歌曲固定标题（Bards College 曲目）；作专名，与玩家身份“Dragonborn→龙裔”区分语境                                                                                     |
| Aldmeri Dominion                  | 先祖神洲                            | 势力               | CONFIRMED   | 官方既有（Skyrim/Dawnguard，如“跟先祖神洲的战争”）；GLOBAL §4.1                                    | 梭默背后的高精灵政权；与 Thalmor（执法机关）分层                                                                                                                        |
| White-Gold Concordat              | 白金协定                            | 条约               | CONFIRMED   | 官方既有（Skyrim BOOK/对话）；GLOBAL §4.1                                                          | 帝国与先祖神洲停战条约                                                                                                                                                  |
| High King                         | 至高王                              | 头衔               | CONFIRMED   | 官方既有（“Ulfric Stormcloak is the true High King”→乌弗瑞克·风暴斗篷才是真正的至高王）；GLOBAL §4 | 天际全境最高统治头衔，与 Jarl（领主）层级区分                                                                                                                           |
| Moot                              | 领主议会                            | 制度               | CONFIRMED   | 官方既有 + GLOBAL §4                                                                               | 关乎至高王选举的全境领主集会。官方词典无独立词条，译法来自天际政治语境归纳                                                                                              |
| Aetherius                         | 光界                                | 宇宙构造           | CONFIRMED   | 官方语料 + GLOBAL §8.1                                                                             | 光芒与魔力源泉，恒星为魔力孔隙                                                                                                                                          |
| Aurbis                            | 奥比斯                              | 宇宙构造           | CONFIRMED   | 官方 Skyrim 语料（BOOK 正文）                                                                      | 涵盖一切可能性的宏观宇宙全貌                                                                                                                                            |
| Nirn                              | 奈恩                                | 宇宙构造           | CONFIRMED   | 官方语料                                                                                           | 凡世星球；泰姆瑞尔大陆坐落其上。复合词作前缀（如 Nirnroot→奈恩根）                                                                                                      |
| Soul Cairn                        | 灵魂石冢                            | 地点               | CONFIRMED   | 官方 Dawnguard WRLD/LCTN                                                                           | 理想之主支配的亡者领域                                                                                                                                                  |
| Thu'um / Shout / Voice            | 吼声之道 / 龙吼 / 声音之道          | 龙语机制           | CONFIRMED   | TES 设定；GLOBAL §7（Skyrim 词典无独立专名条目）                                                   | 三位阶：Thu'um 古老秘术、Shout 具体招式、Voice 神赐力量。译名需保持层级差异                                                                                             |
| Nine Divines                      | 九圣灵                              | 宗教               | CONFIRMED   | 官方既有；GLOBAL §8.2                                                                              | 八圣灵加飞升的塔洛斯；与精灵诸神体系区分                                                                                                                                |
| Wraithguard                       | 幽魂之护                            | 神器               | CONFIRMED   | 官方 ccbgssse008 ARMO:FULL                                                                         | 晨风审判席圣器回归                                                                                                                                                      |
| Sunder                            | 裂解                                | 神器               | CONFIRMED   | 官方 ccbgssse008 WEAP:FULL                                                                         | 晨风工具之一，与幽魂之护同批回归                                                                                                                                        |
| Umbra                             | 蚀影                                | 神器               | CONFIRMED   | 官方 ccbgssse016 WEAP:FULL                                                                         | 噬魂魔剑回归                                                                                                                                                            |
| Goldbrand                         | 黄金烙印                            | 神器               | CONFIRMED   | 官方 ccbgssse005 WEAP:FULL                                                                         | 波耶西亚的黄金魔剑回归                                                                                                                                                  |
| Wabbajack                         | 瓦巴杰克                            | 神器               | CONFIRMED   | 官方（谢尔格拉魔杖）                                                                               | 狂乱魔杖，历代传承                                                                                                                                                      |
| Dunmer / Dark Elf                 | 丹莫 / 暗精灵                       | 种族称谓           | CONFIRMED   | 官方 RACE；GLOBAL §6                                                                               | 自称/古称（丹莫）与通俗称谓（暗精灵）保留差异。同体系：Altmer 自称傲特莫/日常高精灵；Bosmer 自称博斯莫/日常木精灵；日常用通俗形，自称语境用本族名（高等精灵官方零出现） |
| Falmer / Snow Elf                 | 伐莫 / 雪精灵                       | 种族称谓           | CONFIRMED   | 官方 RACE；GLOBAL §3                                                                               | 同源但承担不同信息阶段：地下退化形态为“伐莫”，“雪精灵”是历史/剧透称谓，知识边界内严禁提前使用（伐莫侧已入 global-forbidden）                                            |
| Dwemer / Dwarven                  | 锻莫 / 矮人                         | 民族 / 修饰        | CONFIRMED   | 官方 + GLOBAL §2.1/§3                                                                              | 锻莫=民族文明主体；矮人=传统装备/机械构装修饰。不可无条件互换                                                                                                           |
| The Blades / blades               | 刀锋会 / 刀刃                       | 组织 / 普通词      | CONFIRMED   | 官方 FACT；MVF1 DICTIONARY                                                                         | 大写组织“刀锋会”；诗歌等语境中普通名词 blades 作“刀刃”，按上下文区分，不做全局机械替换                                                                                  |
| Snow Elf / Snow Elves             | 雪精灵                              | 种族称谓           | CONFIRMED   | 官方 Dawnguard（历史称谓）                                                                         | 与 Falmer（伐莫）同源但分属不同信息阶段：雪精灵为历史/剧透称谓，知识边界后可正常使用（与 gfw 伐莫禁令配合）                                                             |
| Housecarl（语境歧义）             | 侍卫（随从）/ 总管（管家型）        | 头衔               | CONFIRMED   | 官方 HearthFires“私人侍卫”；Skyrim“his housecarl→总管”                                             | 玩家随从语境一律“侍卫”（莱迪亚）；领主的管家型 housecarl 官方亦作“总管”。先定指称再译，不机械套                                                                         |
| Louis（CC 角色）                  | 路易士                              | 角色               | CONFIRMED   | 官方词典；MVF1 round6 实测“路易斯”漏网                                                             | 社区通行“路易斯”为错；官方定“路易士”（CC 晨风回归角色）                                                                                                                 |
| Gray Fox / the Cowl               | 灰狐 / 面具                         | 角色 / 回指        | CONFIRMED   | 官方（灰狐为盗贼传说角色；CC 神器为诺克图娜尔的灰面具）                                            | 角色“灰狐”与 CC 神器“诺克图娜尔的灰面具”区分；台词中 the Cowl 口语回指译“面具”（MVF1 DICTIONARY 约定）                                                                  |
| Daedra / Daedric / Daedric Prince | 迪德拉 / 迪德拉（前缀）/ 迪德拉君王 | 界域 / 修饰 / 头衔 | CONFIRMED   | 官方词典（迪德拉种族/装备前缀/君王头衔）                                                           | 社区通行“魔族/魔族王子/魔神”为高频错译（已入 gfw）。区分：Dremora=魔人（迪德拉战士种族，官方固定）；Oblivion=湮灭界；Prince of Oblivion 亦作湮灭君王                    |
| Ulfric Stormcloak                 | 乌弗瑞克·风暴斗篷                   | 角色               | CONFIRMED   | 官方（全名带姓氏；dg04 v2）                                                                        | 全名统一带“风暴斗篷”姓氏；单称 Ulfric 亦“乌弗瑞克”，不与他名混                                                                                                          |

> 状态说明：CONFIRMED 词条均有官方语料或跨 MOD 一致用法支撑，作为跨 MOD 默认；后续发现冲突可修订并升级为 OVERRIDE（记录覆盖原因）。PROVISIONAL（当前仅 Murkmire）允许随更强证据修订。MOD 明确采用不同命名体系时，按 §5 在该 MOD DICTIONARY 建局部覆盖并说明理由。

优先关注的未来来源：旧作 / ESO 术语（尤其随 CC 回归的神器与宗派）、跨 MOD 反复出现的扩展世界观名词、以及新确认的系统性误译。
