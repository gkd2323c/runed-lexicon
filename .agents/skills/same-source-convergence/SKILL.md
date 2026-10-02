---
name: same-source-convergence
description: runed-lexicon 同源整族收敛工具集。判组（列出 canonical 与批次内全部同源多形组，含批内分裂与跨批副本）、表驱动收敛（裁决表外置为 JSON，脚本通用；写穿 map-part-*.json 防重合并冲掉；契约 target 机械核对；未裁决即中止防漏判）、分片合并与八条交卷门槛自检、按分片索引切分 readout、多批修正集合并与按盘面重同步、已写回批次按裁决表生成 close_round 修正集、批次侧产物与 canonical 的对齐与全库脱节扫描。Use when 翻译批次写回前需要把同源多形收敛成整族同形、把裁决表落成可复用的 JSON 而非一次性的按批号脚本、合并分片子代理交付、核对某批 map.json 与 translation.json 是否与 canonical 脱节、或已写回批次需要按裁决表收口。Do NOT trigger for 翻译语义裁决本身（那是人的判断）、契约编译（归 term-contract-compiler）、批次状态与覆盖率（归 translation-batch-ops）。
compatibility: Requires Python 3.10+. Uses only the Python standard library. Expects the runed-lexicon project layout (.work/<plugin>/, mods/<plugin>/).
metadata:
  version: "1.2.0"
---

# 同源整族收敛

把「同一个英文源句在库里出现多形」这件事从「靠人记得」变成「有工具、有护栏、有测试」。

## 为什么需要它

xTranslator 源 XML 里同一句英文会在多个 idx 重复出现（不同 NPC、不同记录族、不同段落）。
批内分片时两片看不到对方的 readout，同一句各起一形；跨批时先写的批与后写的批各选一形。
两种都会在写回前被 `round_pipeline` 的同源预检拦下，但预检只打前 10 组，而且**只看形数不看对错**——
它能告诉你「有 17 组」，不能告诉你「该用哪个形」。

裁决是人的判断，但**列组、查盘面、核契约、防漏判、别把成果冲掉**都该是工具。

## 五条判据（判组时必须逐条走）

1. **撞 canonical 默认向 canonical 对齐**；canonical 形有实质缺陷才改主宗。
2. **批内分裂是独立一类**：分片两片看不到对方 readout，复读段落各起一形，canonical 一行都没有。
   判组必须同时收「canonical ∩ 本批」和「本批内部自己就有多形」两类。只判第一类会漏掉全部批内分裂。
3. **批内多数默认取用；平票与少数形逐组回源文判**，不按机械多数。
4. **机械一致 ≠ 语义正确**：每组都要读完再收口。
5. **契约 target 优先于多数形**。`converge_batch.py` 会机械核对这一条；它抓到的真实错误比人眼多。
   同源豁免的前提是「prompt 不同」——同一源句内前一行源文相同却译得不同，属同 prompt 漂移，豁免不覆盖。

## 标准流程

```bash
S=YourMod; B=INFO-123
D=.agents/skills/same-source-convergence/scripts

# 0. 先看这批的产物与盘面是否脱节（写回后任何一次 consume_batch 都会造成脱节）
py -3 $D/sync_batch_artifacts.py --stem $S --batch $B

# 1. 判组：列出全部同源多形组（canonical ∪ 本批；批内分裂一并收）
py -3 $D/same_source_groups.py --stem $S --batch $B --out _tmp/data/ssg.txt --show-all

# 2. 每组下判断前，两把尺（契约 + 盘面）
py -3 $D/adjudicate.py --stem $S contract harvest distinction maker
py -3 $D/adjudicate.py --stem $S census bearer --form 承载者 --form 承担者 --show 3

# 3. 把裁决写成表 JSON（{batch, rationale, table, exempt_prompt_fix?}），
#    然后预检：列缺表的多形组 + 机械核契约
py -3 $D/converge_batch.py --stem $S --table _tmp/data/table.json

# 4. 改写（会一并写穿 map-part-*.json）
py -3 $D/converge_batch.py --stem $S --table _tmp/data/table.json --apply

# 5. 复验，再走写回
py -3 $D/converge_batch.py --stem $S --table _tmp/data/table.json --verify
py -3 .agents/skills/translation-batch-ops/scripts/round_pipeline.py --stem $S --batches $B ...
```

批已写回、需要按裁决表收口时（canonical 上已有别的形）：

```bash
py -3 $D/make_close_fixes.py --stem $S --batch $B --table _tmp/data/table.json
py -3 .agents/skills/translation-batch-ops/scripts/prune_close_patch.py --stem $S --batches $B --prune --drop-stale
py -3 .agents/skills/translation-batch-ops/scripts/close_round.py --stem $S --batches $B \
   --fixes .work/$S/batches/close-round-fixes.json --xml mods/$S.esp/${S}_english_chinese.xml \
   --contract .work/$S/contracts/${S}.compiled.json
```

分片与收口侧的配套：

```bash
py -3 $D/shard_readout.py  --stem $S --batch $B --parts a b      # 切 readout
py -3 $D/check_shards.py   --stem $S --batch $B --parts a b --merge
py -3 $D/merge_and_resync.py --stem $S fix-a.json fix-b.json     # 多批修正集合并 + 重同步
py -3 $D/sync_batch_artifacts.py --stem $S --scan               # 全库脱节扫描（一次解析）
```

## 裁决表格式

```json
{
  "batch": "INFO-595",
  "rationale": { "<英文源句>": "<为什么这么裁；要写下盘面数字>" },
  "table":      { "<英文源句>": "<裁决形>" },
  "exempt_prompt_fix": { "<已登记豁免的源句>": "<目标形>" }
}
```

裁决表放 JSON 而不是写进 `.py`，是因为**裁决是一次性的、脚本不是**：
写进代码就等于每个批次产出一个注定被丢掉的脚本。表外置之后脚本可以被每一轮、每个 MOD 复用。

`rationale` 不是装饰。下一轮有人遇到同一组时，这是他唯一能看到的依据；
只写「取多数」等于把判断留给了运气。

## 护栏与它们各自防住的事故

| 护栏 | 防住的事故 |
|---|---|
| 未裁决的多形组即中止 | 漏判一组就写回，漂移留进 canonical |
| 契约 REQUIRED 词条机械核对 | 按批内多数收敛压过契约 target（已发生 3 次：那重区别、收获/收成、transformation/蜕变） |
| 契约 forbidden 形一律中止 | 撞全局禁用词 |
| 写穿 `map-part-*.json` | 只改 `map.json` 时，任何一次重跑 `consume_batch` 都用未收敛的分片覆盖回去（已发生） |
| 待改 idx 不属本批即中止 | 批已写回却改 `map.json`，改了个没人读的文件 |
| 已登记豁免源句按 `round_pipeline` 口径跳过 | 豁免族被当漂移强行统一 |
| `exempt_prompt_fix` 单独通道 | 豁免前提是 prompt 不同；同 prompt 漂移要单收，不能靠豁免放行 |
| 合并时按 canonical 复核 `expected_current` | 拿旧结论改新行；或重复收口撞 CAS |
| 落盘后 `json.loads` 读回断言 | 写出自认为成功、实际损坏的 JSON |
| 收口前 `same_source_family.py` 核族 | 只改同源族里一行——净效果为零，且照样触发 `same_source_split` 拦截（本轮实测一族跨 596/597/598 三批 8 行） |
| 收口前 `anchor_overlap.py` 核锚 | 把「跨锚撞形」当同源漂移处理。本轮实测「权柄」36 行里 authority 26 + power 10，只看中文形票数会把两类混作一次替换、方向就错了 |
| 子代理普查必须核到全库口径 | 拿批内普查当全库用，方向整个反了（本轮实测：要求把回指改成批内多数形「重担」，而全库 `burden` 是 重负 251:负担 93:重担 37，重担是最稀形） |

## 收口前的两个前置核（比判组更早跑）

`same_source_groups.py` 按**批次**列组；收口一条 finding 前还需要两个全库口径的核，
它们各自防住一类会被 `close_round` 的 `same_source_split` 拦下、但原因不同的问题。

### `same_source_family.py`：这个 idx 的同源族到底有几行

```text
py -3 same_source_family.py --stem <STEM> --batches-dir .work/<STEM>/batches --idx 41291,41296
py -3 same_source_family.py --stem <STEM> --idx-file _tmp/data/idx.txt     # 大批时
```

逐个 idx 列出全库同 `Source` 的全部行（带所属批次与现译），并标「孤例 / 同源族 N 行」。
**只改族里一行，净效果为零**，且会当场触发 `same_source_split` 硬拦截。
族可以跨批（本轮实测一族横跨 596/597/598 三批 8 行），所以不能只看本批。

### `anchor_overlap.py`：这个中文形在渲染哪些英文锚

```text
py -3 anchor_overlap.py --xml <canonical.xml> --form 权柄 --anchors authority,authoritative,power
```

按**源文**命中的锚把某个中文形的行分组，标出「同一形跨多个英文锚」的行。
词形普查（`adjudicate.py census`）只给中文形分布，看不出跨锚撞形——
本轮实测「权柄」36 行里 authority 26、power 10，混着两个锚。

**跨锚撞形直接改变裁决方向**：一个同时服务两个锚的形不该当主形，而这次普查正是
把 `authority` 从「权威 63 / 威权 24 / 权柄 26 的三向乱局」收敛成
「权威（认知制度位）62 行不动 + 威权（裁断位）收编权柄 26 行 + 权柄让出给 power 的 10 行归位为权力」
的唯一依据。只看中文形票数会把这两类混作一次替换，方向就错了。

### `gen_form_convergence_tables.py`：按「中文形 + 锚」批量生成裁决表

跨锚撞形收敛（见上）的成规模做法——一次几十行、跨几十个批次，人工逐行建表不现实：

```text
# dry-run 先看计划（不落盘）
py -3 gen_form_convergence_tables.py --stem <STEM> --batches-dir .work/<STEM>/batches \
    --rule "权柄:authority,authoritative->威权" --rule "权柄:power->权力" \
    --out-dir _tmp/data/authtabs
# 核完再加 --apply 生成表
py -3 ... --out-dir _tmp/data/authtabs --apply
```

规则语法 `<现形>:<锚,锚>-><目标形>`；同一现形可给多条规则（不同锚 → 不同目标形），
命中时第一条匹配生效。**同一现形给两条规则正是跨锚撞形的用法**。

三条关键性质（都有测试盯）：

- **以源句为键，不是以行为键**：同源族跨批时，每个相关批次都拿到同一条 `源句 -> 目标` 映射，
  不会「只改一族里一半行」。实测 authority 收敛 37 行跨 25 批就靠这条。
- **锚不匹配则跳过而不是硬替换**：形在但源文不含指定锚的行会单列出来让你核，不会被顺手改掉。
- **同一源句现有两形即中止**：注意这**不是**「规则自相矛盾」——同一源句必然匹配同一条规则
  （命中即 break）。真正触发条件是该源句现有两形，机械替换会忠实地把这个分裂保留下来，
  那不叫收敛，须先人工定主形。

### `fill_table_rationale.py`：给生成的表补判据

裁决表 `rationale` 必填（只写「按规则替换」等于把判断留给运气）。**判据文本由调用方提供**
（`--rationale-file`，规则名 -> 正文）——裁决是人的判断，把它硬编码进脚本等于把脚本变成
穿工具外衣的一次性脚本（本脚本的第一版就是这么写的，被自己的测试逼改了）。

```text
py -3 fill_table_rationale.py --dir _tmp/data/authtabs \
    --rationale-file _tmp/data/authtabs-rationale.json
```

三条机械校验：判据为空即拒；目标译文找不到任何已知目标形即拒；**目标译文同时命中多条判据
即拒**（同名目标形被多条规则指向时无法判该用哪条——这里必须存成 `目标形 -> [规则]` 列表，
用 dict 存单值会静默互相覆盖、歧义判据形同虚设）。

## 边缘情况

- **契约 `terms` 可能是 dict 也可能是 list**：`adjudicate.py` 与 `converge_batch.py` 两种都收，不要假设只有一种。
- **英文屈折**：契约写词元（`harvest` / `maker`），源句写屈折形（`harvests` / `makers`）。
  两边都做后缀还原（`'s / s / es / ed / ing` + 双写辅音），否则契约核对会**静默漏检**——
  看起来查过了，其实没生效，这是最危险的一种漏。
- **未译行不能算有译文**：xTranslator 源 XML 的 `<Dest>` 里放的是英文原文。
  比对盘面时必须同时取 `Source` 再比，只看 `Dest` 非空会得到一片假阳性。
- **收口固定跑 `prune_close_patch --prune --drop-stale`**：上一轮遗留的 `close-round-patch.json`
  会被 `close_round` 的 CAS 当 stale 拦下。
- **有别的进程在动同一批时**：`round_pipeline` 有 `pipeline.lock`，但本 skill 的改写不持锁。
  判组前先 `--scan` 确认盘面没在这期间被别人写回；写回后再 `--scan --apply` 把批次侧拉回一致。
- **`--apply` 覆盖「当前已单形」的表项**：有人误跑 `consume_batch` / `inherit_prefill` 会把同源多形
  压成单形，胜出的是随机一侧。表是裁决，照表覆盖，不看它当前是不是多形。

## 安全边界

- 只读源 XML 与 canonical；**不改写 canonical**，写回一律走 `round_pipeline` / `close_round`。
- 只改 `.work/<plugin>/batches/` 下的产物文件。
- 中文一律用 Write 工具直写字面中文落盘；**不要在 PowerShell 命令行里写中文或 `\uXXXX`**
  （会被改写码位，正则静默失效、普查结论全错），也不要手算 `\uXXXX`。
- 本 skill 不做翻译裁决，也不产出句子；只列组、核盘面、落裁决。

## 验证方式

```bash
py -3 -m unittest discover -s .agents/skills/same-source-convergence/scripts -p "test_*.py"
node .agents/skills/skill-creator/scripts/check_env.mjs --capability quick-validate
py -3 .agents/skills/skill-creator/scripts/quick_validate.py .agents/skills/same-source-convergence
py -3 tools/pre-push-check.py
```

`--verify` 退出码 0 是「该批无剩余同源异形组」；`converge_batch.py` 无 `--apply` 时只报不改，
退出码 1 表示契约硬失败或缺表。
