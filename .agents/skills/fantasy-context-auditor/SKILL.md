---
name: fantasy-context-auditor
description: 奇幻语境违和检测器（语义层）。用本地 qwen3.5:9b 判断 Skyrim MOD 译文是否明显出戏——提到现实世界才有的事物/概念（现代科技、互联网、现代制度、现实宗教专名等），在剑与魔法语境中显得荒唐。Use when 需要扫描翻译结果或 canonical 里词表（TERM004 unconditional）漏网的现代/现实语义内容，作为批次收尾或波末的质量复查。定位是预筛排序器：输出候选供 Agent 人眼复核，不自动改任何文件。与 hardfix-triage（词表/规则硬伤）互补：词表抓「看见即判错」的词形，本工具抓「语义出戏」的句子。
compatibility: 需要 Ollama 在线且装有 qwen3.5:9b（或等效支持 think:false 参数的模型）。Python 3.10+，仅标准库。只读，不修改 XML/译文/词库。
metadata:
  version: "0.1.0"
---

# fantasy-context-auditor

## 用途与定位

词表（`global-forbidden-words.json` 的 unconditional 条目，TERM004 执行）抓的是
「看见即判错」的词形（上帝/微信/服务器/身份证…）。但模型翻译时可能自创
**词表外的现代句式**（如把整句翻成现代职场口吻），词表抓不到。本工具用本地
LLM 做语义层判断，兜住这类漏网。

**核心判定**（经边界压测验证，2026-09-08）：
把句子放进剑与魔法奇幻语境，问「是否明显出戏」。不是问「是否现代气息」——
后者是抽象风格判断，模型无世界观知识会乱猜（实测 qwen3.5 误报 30%）；
前者只需常识违和感（云端/KPI/快递放中世纪当然荒唐），实测 95% 且明显荒唐零漏报。

**定位是预筛排序器，不是判决器**：出戏候选约 10% 为边界误报（如「写个报告」——
古代有军事文书但模型不认），必须由 Agent 人眼复核后裁决。不自动 FAIL，不改文件。

## 调用方式

项目根目录执行：

```text
# worker res JSON / translation result JSON（每批 apply 前可选）
py -3 .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --file .work/Druadach-info-INFO-XXX-translation.json

# canonical XML 全扫（波末复查；可 --rec 过滤 REC、--limit 限量）
py -3 .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --xml mods/Druadach.esm/Druadach_english_chinese_translated.xml --rec INFO --limit 500

# 逐句 stdin（配合其它工具管道）
echo '把文件上传到云端。' | py -3 .../fantasy_audit.py --stdin

# JSON 输出（供脚本消费）
py -3 .../fantasy_audit.py --xml ... --limit 200 --json
```

选项：`--model`（默认 qwen3.5:9b）、`--host`（默认本机）、`--cache`
（默认 `_tmp/data/fantasy-audit-cache.json`，同句不重查，增量扫描秒级）、
`--no-cache`、`--limit N`、`--json`、`--rec PREFIX`。

## 模型要求与已知边界

- 必须支持 `think: false` 参数且走 `/api/generate`（chat 接口不吃 think 参数，输出会空）。
- minicpm5:1b 判别力不足（55% 接近随机），不要用。
- 边界误报（接受，人眼秒放）：报告文书类（「写个报告」）、现实古国引用（「罗马」）。
- 边界漏报（低频，词表可兜）：「希腊神话」类（神话在奇幻有对应概念会迷惑模型）。
- 速度：约 0.2s/句。全量 canonical ~8000 已译行 ≈ 30 分钟；单批 45 行 ≈ 10 秒。
  缓存落盘后重复扫描同批秒回。
- prompt 内置世界观锚点（九圣灵/圣骑士/神殿/王国/商会/税收/文书/骑士团），
  不要随意删减——删掉会导致斯坦达尔等 TES 专名台词误报（实测教训）。

## 与其它工具的分工

| 工具 | 层 | 抓什么 |
| --- | --- | --- |
| translation-quality-gate TERM004 | 词形 | unconditional 禁词（世界观荒谬词 719 条） |
| hardfix-triage | 规则 | 术语禁形/丢否定/空译/英文残留 + ANACHRONISM 候选 |
| **本工具** | 语义 | 词表外的现代/现实语义出戏句子 |
| 词表同形误报（夜总会/西藏） | — | 语义层正确放行（两套互补，实测验证） |

## 安全边界

- 只读。不修改 XML、译文、词库、契约。
- 候选不自动 FAIL、不自动改稿；Agent 复核后决定回改或放行。
- 发现的真出戏（词表外新词）应回填 `global-forbidden-words.json`（unconditional）
  或 `hardfix-triage.py` 的 BANS/ANACHRONISM 表，形成闭环。

## 验证

基准（记录于 `_tmp/data/fantasy-edge-verdict.md`，2026-09-08）：
26 例边界 85% 准确、明显荒唐零漏报；canonical 真实 20 行 4 秒零候选。
修改 prompt/模型后重跑 `_tmp/scripts/fantasy-edge-bench.py` 对照。
