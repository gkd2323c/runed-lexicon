---
name: translation-protect
description: "游戏文本翻译的 Protect 阶段：把句子中间的行内运行时 token（别名占位 Alias、全局变量 Global、%s/%d 与 {0} 格式化占位、字面换行、[pagebreak] 分页符、颜色与排版标签 font / p / br）掩码成稳定哨兵符，译文返回后按哨兵符索引按位还原，并对还原做哨兵符多重集严格相等校验；同时把特殊字符串分三桶——行内 token 走掩码还原、代码标识符（资源路径 / FormID / 脚本属性名）整条排除不进模型、灰区（大小写变体别名、性别与语境变量、非引擎方括号标签）标记二审。Use when 需要准备送进模型的翻译文本（掩码运行时 token）、需要把模型返回的带哨兵符译文还原成可写回的文本、需要在写回前校验运行时 token 完整性、要判断某条 Source 该不该进模型、或换模型 / 换哨兵符形态后要复验还原守恒。Do NOT trigger for: XML 结构与节点校验（归 skyrim-xml-verification）、术语契约与禁用词（归 translation-quality-gate / term-contract-compiler）、术语与名词裁决（归 translation-review-tools）、长书籍分片流程（归 long-book-translation，其哨兵符语法是本阶段在书籍场景的既有前身）、XML 写回本身（归 xtranslator-xml-writer）。"
compatibility: "Depends on runed-lexicon project layout and Python 3.10+; scripts use only the standard library. Read-only with respect to MOD XML; intermediate artifacts go under _tmp/."
metadata:
  version: "0.1.0"
---

# Protect 阶段：行内 token 掩码、还原与校验

## 1. 掩码把 token 完整性从概率行为变成字符串操作

Skyrim 文本里嵌着引擎在运行时解析的标记：`<Alias=Target>` 由任务目标动态填补、`%s` 由脚本填参、`[pagebreak]` 切分书页、字面换行控制显示框排版。这些 token 一旦被模型改写、漏掉或挪位，游戏里就是缺参、乱版、串页，而译文本身读起来完全通顺——错误不可见。

两种护法：

| 护法 | token 完整性取决于 | 失效形态 |
| --- | --- | --- |
| 要求模型原样保留 | 模型的概率行为 | 漏标 / 改标 / 重排；读起来通顺但 token 已坏 |
| 掩码还原（本阶段） | 确定性字符串操作 | 只有掩码自身出错才会坏，而掩码是程序做的 |

掩码**不取消校验，只让校验更便宜**：原文里的标签形态多变（大小写、空格、属性顺序、实体形态），逐字符比对脆弱；掩码后译文里只剩稳定哨兵符，比对退化成多重集相等。因此**还原之后仍必须做哨兵符多重集严格相等校验**，不因为"反正有程序兜着"就跳过。

**不许直接删除 token。** 删除会连位置与语序信息一起丢掉：模型拿到的是残缺句（主语没了、宾语没了），还原时也无从判断该塞回哪里。掩码保留位置信息——哨兵符在句子里的位置，就是原 token 的位置。

## 2. 三桶分流

工具按**整条 Source** 判定桶位，输出 `bucket` 字段：

| 桶 | 判据 | 处置 |
| --- | --- | --- |
| `MASK` | 句子含行内运行时 token | 掩码 → 进模型 → 还原 |
| `EXCLUDE` | 整条即代码标识符：资源路径、FormID、纯标识符 | 不进模型，按 KEEP 处理 |
| `GRAY` | 灰区：大小写变体别名、性别 / 语境变量、非引擎方括号标签 | 掩码照做，同时记 `gray_reasons` 待二审 |

`EXCLUDE` 优先于 `MASK`：整条是路径或 ID 时，里面的 `<`、`%` 等字符是路径字符而不是 token，不该被掩码。

`GRAY` 不是「拿不准就都塞进去」的垃圾桶：它只装**形态已知但需要人判**的条目，二审要能给出结论（它是 token，还是可译文本）。判据由 `scripts/protect_text.py` 顶部的 `DEFAULT_GRAY` 与 `KNOWN_ANGLE` 表达，全部是启发式候选发现器，不是裁决者。

`GRAY` 里最典型的是方括号标签：`[pagebreak]` 是引擎分页符（进 `MASK`），而 `[Show Ring]` 这类动作提示按天际官方中文惯例应当中文化——两者形态相同、语义相反，只能由二审区分。这与 `translation-executor` 的 `waived_tokens` 是同一条缝的两种表达。

## 3. 哨兵符形态

`--sentinel-form` 二选一：

| 形态 | 形如 | 取舍 |
| --- | --- | --- |
| `opaque`（默认） | `⟪0⟫` `⟪1⟫` | 不携带类型信息，模型难以从形态推断该填什么，最不易被改写；译文质量略低 |
| `typed` | `⟪T0⟫` `⟪P1⟫` | 类型字母给模型一点线索，译文质量更好；但模型可能「顺手」把类型字母改写或补齐 |

括号用 `⟪⟫`（U+27EA / U+27EB），与 `long-book-translation` 一致。还原正则同时认 `⟪0⟫`、`⟪T0⟫` 与 `⟪F0⟫`，因此与书籍流水线天然兼容。

**形态必须经过模型实测**：同一符号在不同模型上表现不同（有的把生僻 Unicode 空格化，有的把看起来不完整的标签补齐，有的干脆把哨兵符当语气符号删掉）。换模型或换形态后，用同一组样本重跑一遍还原校验，确认多重集仍严格相等，再决定采用哪种。默认取 `opaque` 是因为主人说过它最安全；`typed` 的质量优势要用实测证明，不靠感觉。

## 4. 命令

掩码（准备送模型的文本）：

```text
py -3 .agents/skills/translation-protect/scripts/protect_text.py mask \
  --input _tmp/data/batch-units.json --out _tmp/data/batch-masked.json \
  --sentinel-form opaque
```

还原并校验（模型译文回来后）：

```text
py -3 .agents/skills/translation-protect/scripts/protect_text.py restore \
  --masked _tmp/data/batch-masked.json \
  --translations _tmp/data/batch-translations.json \
  --out _tmp/data/batch-restored.json --report _tmp/data/batch-protect-report.json
```

输入 / 输出都是 JSON，`scripts/protect_text.py` 的 docstring 给出精确结构。`--grammar <json>` 可覆盖默认文法（`inline` / `exclude` / `gray` 三段），用于 MOD 特有的 token 形态，不改代码。

`restore` 的退出码：全部通过 `0`；出现硬错误 `1`。硬错误逐条列在报告里，修完对应条目重跑即可。

## 5. 校验口径

`verify` 报三类 finding：

| kind | 含义 | 级别 |
| --- | --- | --- |
| `sentinel_multiset_mismatch` | 哨兵符多重集不等（漏标 / 多标） | 硬错误，拒绝产出 |
| `sentinel_reordered` | 多重集相等但顺序漂移（仅 `--check-order` 时） | 硬错误，拒绝产出 |
| `missing_translation` | 该条没有译文 | 硬错误，拒绝产出 |

**为什么默认只查多重集、不查顺序。** 翻译会合法地重排句内 token。中文语序常把参数提到别名之前——`Kill <Alias=Target> with %s.` 译成「用 %s 杀死 <Alias=Target>。」时两个哨兵符的顺序必然颠倒。若拿顺序相等当硬断言，一批正确译文会被整批拒收。多重集相等才是「token 没丢、没多、没被改形」的判据，与主人的原话一致。

顺序只在**顺序即语义**的场景才需要断言（书页 `[pagebreak]`、成对排版标签），用 `restore --check-order` 单独开启。这个开关不默认开：默认开会把合法重排误判成缺陷。

## 6. 边界与相邻 skill

- **只读**：不写 MOD XML。所有中间产物落 `_tmp/`，收尾即清。
- **不判语义**：桶位是形态判定，不做"这个 token 是什么意思"的解释。专名与术语裁决归 `translation-review-tools` / `translation-quality-gate`。
- **与 `long-book-translation`**：两者都是掩码还原路线，但作用域不同——那是长书分片的前身（只管尖括号标签 + `[pagebreak]`），本阶段面向通用批次。哨兵符语法互相兼容。
- **与 `translation-executor`**：executor 的 `protected_tokens()` 只做检测 + 多重集比对，属「要求模型保留」路线。两条路线**不要在同一批里混用**：同一批要么整体掩码，要么整体要求保留，混用会让校验口径互相打架。

## 7. 验证

改脚本后必跑回归（纯标准库，不碰真实项目文件）：

```text
py -3 .agents/skills/translation-protect/scripts/test_protect_text.py
```

覆盖：掩码 / 还原往返守恒、多重集漏标与多标被拦、顺序漂移被拦、`EXCLUDE` 分类、`GRAY` 分类、字面换行与 `{0}` 占位、typed 与 opaque 两形态、与 `⟪F数字⟫` 语法兼容。

创建或修改本 skill 后，按 `skyrim-tool-dev-rules` §3 跑 `skill-creator` 的快速验证流程。
