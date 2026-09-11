---
name: translation-review-tools
description: "Review and revise translation batch artefacts without hand-writing one-off scripts: read a batch's source/translation pairs (read_batch.py), compile a batch context into a compact dispatch digest of per-idx terminology evidence (term_digest.py), search any word across a translated xTranslator XML with REC/EDID/status and batch attribution (query.py --src/--dst/--idx/--locate), and apply correction lists to a batch's map.json + translation.json with an optional canonical patch (apply_fixes.py). Use during translation acceptance (三查验收通读), terminology adjudication (查全库分布/先例), cross-batch consistency checks, dispatch preparation (pre-chewing context.json so subagents never shell-grep the dictionary), and post-review corrections. This is the standard toolkit replacing ad-hoc python -c / throwaway scripts for these jobs. Read-only except apply_fixes.py."
compatibility: Requires Python 3.10+. Uses only the Python standard library.
metadata:
  version: "0.2.0"
---

# Translation Review Tools

翻译评审与修订环节的标准工具集，替代四类反复手写的一次性脚本：

| 脚本 | 职责 | 替代的临时代码 |
| --- | --- | --- |
| `read_batch.py` | 读一个批次的源译对照（验收通读标配） | 各种 `readout_*.py` |
| `term_digest.py` | 把批次 context.json 编译成一屏派单摘要（每 idx 的 MOD/官方术语命中 + 语境锚点） | 各种 `dump_*terms*.py` |
| `query.py` | 全库搜词（源/译两侧）、idx 定位、批次归属 | 各种 `check_*.py` |
| `apply_fixes.py` | 修正清单联动同步 map + result + canonical patch | 各种 `apply_fixes_*.py` / `fix_*.py` |

## read_batch.py

读单个批次的源文/译文对照。数据源优先 `translation.json`（含 source/status），退化到 `map.json` + `--xml`。

```text
py -3 .agents/skills/translation-review-tools/scripts/read_batch.py \
  --stem Artaeum --batch NI-CELL-002

# 只看待决条目 / 输出到文件
py -3 .../read_batch.py --stem Artaeum --batch INFO-023 --status WAITING,KEEP
py -3 .../read_batch.py --stem Artaeum --batch NI-NPC-001 --out _tmp/data/readout.txt
```

## term_digest.py

把批次 `context.json` 编译成紧凑派单摘要。**派单前必跑**：原始 context.json 单批约 95–105 KB，子代理一次 `read` 读不下，会绕道 shell 查词典树，把预算烧在机械查证上。

```text
py -3 .../term_digest.py --context .work/Artaeum/batches/NI-NPC-005/context.json \
  --out .work/Artaeum/batches/NI-NPC-005/term-digest.txt
```

每 idx 三行（含空命中标注 `-`）：

```text
[idx] REC | source | dup=N
    MOD: English→中文 (STATUS); ...
    OFF: English=中文[REC]; ...
    CTX: quest=<edid>; cat=<category>; topic=<topic>   # 仅 dialogue_context.status=matched
```

MOD = 项目契约命中（`mod_terms_hits`）；OFF = 官方词典命中（`official_dictionary_hits`）；CTX = 对话语境锚点（仅对话条目）。空命中照列 `-`，让执行者知道「没有」是真的，不是漏看。派单任务卡的输入段指向本摘要，不指向原始 context.json。纯只读，除 `--out` 指定的文件外不碰任何文件。

### 法术名统一表附带（`--spell-registry`，默认自动探测）

摘要末尾附 MOD 专属法术名统一表（`.work/<plugin>/notes/*-spell-name-registry.md`，自动探测；也可 `--spell-registry` 显式指定）：

```text
== 法术名统一表（83 项；* = 本批源文命中） ==
  * Throwdoll = 击倒术
    Charge = 充能术
```

**为何必须带**：此表的法术名已写回 canonical，但不进 `terms.json`（普通词作 REQUIRED 会过度绑定，如 Charge/Ghost/Diamond）。摘要不带时，子代理看不到已定形，只能凭音感自造（真实事故：BOOK-004 的 Throwdoll/Sacrifice/Charge 三处自创，与已写回的击倒术/祭品/充能术偏离，验收时才发现）。`*` 标出本批源文命中项，便于优先对齐。

## query.py

四个模式（互斥）：

```text
# 按源文搜（大小写不敏感，字面匹配）
py -3 .../query.py --xml mods/Artaeum.esp/Artaeum_english_chinese_translated.xml --src Thrall

# 按译文搜
py -3 .../query.py --xml <same> --dst 斯罗尔

# 单行 + 邻域（--context K）
py -3 .../query.py --xml <same> --idx 3023 --context 4

# 批次归属（扫批次计划）
py -3 .../query.py --locate 3023

# 正则 / 大小写敏感 / 提高上限
py -3 .../query.py --xml <same> --src "Thrall|斯罗尔" --regex --limit 0

# 批量锚点核查：一个英文锚一行，报每个锚的全库中文 Dest 形态分布
py -3 .../query.py --xml <same> --anchors anchors.txt
```

`--anchors` 是**验收时核专名的标配**（取代手写 scan_review_terms.py 类脚本）：每行一个英文锚，以整词（含复数 s）匹配，报该锚在全库已译行里出现的每个中文 Dest 及其行号；≥2 种形态标 `<<< 分裂`。与 `noun-consistency-scan` A pool 互补：本例不排除对话行（INFO/DIAL），且以词为锚而非整句 Source，因此能抓到 A pool 因按整句分组而漏掉的**句内专名分裂**（真实例：Elise 在 QUST 作「伊莉丝」、INFO 作「艾莉丝」；Pål 在 NPC/DIAL 作「波尔」、INFO 作「保尔」）。形态按整句 Dest 计，故同锚不同句会算作不同形态，分裂计数为**候选**偏高，需人工按语境裁决。

输出每行：`[idx] REC EDID 状态` + SRC/DST 两行；`--locate` 输出 `[idx] -> <batch-id> (<plan 路径>)`。

## apply_fixes.py

把修正清单一次应用到 `map.json` + `translation.json`，并可生成带 CAS 守卫的 canonical patch。

fixes JSON：

```json
{
  "2943": "你不该来这儿",
  "14729": {
    "new": "……你在耍我吧？",
    "expected_current": "……你在耍我把？",
    "notes": "错字修正",
    "status": "TRANSLATED"
  },
  "5268": {
    "status": "TRANSLATED",
    "notes": "仅改 status（无 new）：REVIEW → TRANSLATED，译文保留现值"
  }
}
```

**`new` 可选**：缺省时只改 status/notes/confidence（常见需求：把 REVIEW/KEEP 状态转成 TRANSLATED 而译文不动），避免为此手写一次性脚本。

**`--find/--replace`（同型多行替换）**：不值得为 5 条同型修正写全量 new JSON 时用；从各 idx 现值做子串替换并生成 fixes，走同一条写盘链路。`--idx-list` 限定行；无可替换时 no-op 且 exit 0。

**CAS 双处校验（重要）**：expected_current 同时校验 `map.json` 与 `translation.json`，任何一个不匹配即中止且**两处都不写盘**。因此手工改过 map 数值后须同步 translation.json（正常流程由 `fill_translations.py` 保证两者一致）。

```text
py -3 .../apply_fixes.py --stem Artaeum --batch NI-CELL-002 --fixes _tmp/data/fix.json

# 同型多行子串替换（不必写全量 new）：全批替换，或用 --idx-list 限定
py -3 .../apply_fixes.py --stem Artaeum --batch NI-QUST-023 --find "拉格瓦滕岛" --replace "洛格瓦滕岛"
py -3 .../apply_fixes.py --stem Artaeum --batch NI-QUST-014 --find "「" --replace "“" --idx-list "5730,5731"

# 同时生成 canonical patch（expected_dest 自动取 XML 当前 Dest）
py -3 .../apply_fixes.py --stem Artaeum --batch NI-CELL-002 --fixes fix.json \
  --translated-xml mods/Artaeum.esp/Artaeum_english_chinese_translated.xml \
  --patch-out .work/Artaeum/maps/Artaeum-fix-patch.json
```

行为细则：

- **先全量校验、后统一写盘**：任何一条 `expected_current` 不符即整体中止（exit 2），不写任何文件——这是防错批、防手滑的机械保证。
- **幂等**：译文已等于 new 时跳过（不报错）；只携带 notes/status/confidence 的修正仍会应用字段。
- **KEEP 自动转换**：把 KEEP 条目改译时，status 自动转 `TRANSLATED`（避免写回时被 KEEP 语义把译文还原成英文），并打印 WARN。
- **patch 生成**：`--patch-out` 已存在时默认合并（同 idx 以本次值为准并计数提示）；XML 当前 Dest 已等于 new 的条目跳过（已就位）。
- 不触 MOD XML 本身；patch 由 `xtranslator-xml-writer` 消费写回。

## 安全边界

- `read_batch.py` / `query.py` 纯只读。
- `apply_fixes.py` 只写 `.work/` 下的批次文件与指定 patch 路径。
- 不修改 MOD XML、不替代写回工具、不自动重跑 gate。修正后的重验与写回照常走
  `verify_subagent_batch.py` → `write_translations.py`。

## 验证方式

```text
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/translation-review-tools
py -3 .agents/skills/translation-review-tools/scripts/test_review_tools.py
```

`test_review_tools.py` 覆盖：read_batch 双数据源与状态过滤、query 四种模式、
apply_fixes 的更新/校验中止/KEEP 转换/幂等/patch 生成、term_digest 的
MOD/OFF/CTX 格式化与空命中标注、dup 阈值、`--out` 落盘。
