---
name: fantasy-context-auditor
description: 奇幻语境违和检测器（语义层）。用本地 LLM 判断 Skyrim MOD 译文是否明显出戏——提到现实世界才有的事物/概念（现代科技、互联网、现代制度、现实宗教专名等），在剑与魔法语境中显得荒唐。Use when 需要扫描翻译结果或 canonical 里词表（TERM004 unconditional）漏网的现代/现实语义内容，作为批次收尾或波末的质量复查。定位是预筛排序器：输出候选供 Agent 人眼复核，不自动改任何文件。与 hardfix-triage（词表/规则硬伤）互补：词表抓「看见即判错」的词形，本工具抓「语义出戏」的句子。
compatibility: 需要本机 Ollama 在跑，且配置了一个支持 think:false 参数的中文指令模型（≥9B 量级为宜，1B 级判别力不足——见 SKILL.md「模型要求」）。Python 3.10+，仅标准库。只读，不修改 XML/译文/词库。
metadata:
  version: "0.2.0"
---

# fantasy-context-auditor

## 用途与定位

词表（`global-forbidden-words.json` 的 unconditional 条目，TERM004 执行）抓的是
「看见即判错」的词形（上帝/微信/服务器/身份证…）。但翻译模型可能自创
**词表外的现代句式**（如把整句翻成现代职场口吻），词表抓不到。本工具用本地
LLM 做语义层判断，兜住这类漏网。

**核心判定思路**（决定本工具成败，勿偏离）：
把句子放进剑与魔法奇幻语境，问「是否明显出戏」。**不是**问「是否现代气息」——
后者是抽象风格判断，模型没有完整世界观时会乱猜；前者只需常识违和感
（云端/KPI/快递放进中世纪世界当然荒唐），实测准确率高且明显荒唐零漏报。

采用「语境异常检测器」模板：默认放行（无罪推定），只报高置信度明显荒唐；
内置同形反例（“中国”在“国王下令封锁城门”、“夜总会”在“夜晚总会”、
“西藏”在“东躲西藏”）防止字符串误报；输出 PASS / FAIL|<片段>|<类别>|<原因>，
类别取 MODERN_TECH/MODERN_INTERNET/MODERN_SOCIAL/MODERN_FINANCE/
MODERN_ADMIN/REAL_WORLD_ENTITY/REAL_WORLD_RELIGION/AI_META/OTHER_INTRUSION。
模板全文在 `scripts/fantasy_audit.py` 的 PREFIX（改动需回归）。

**定位是预筛排序器，不是判决器**：出戏候选含少量边界误报（如「写个报告」——
古代也有军事文书，但模型可能不认），必须由 Agent 人眼复核后裁决。不自动
FAIL，不改任何文件。

## 调用方式

项目根目录执行：

```text
# worker res JSON / translation result JSON（每批 apply 前可选）
py -3 .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --file .work/<mod>-info-<batch>-translation.json

# canonical XML 全扫（波末复查；可 --rec 过滤 REC、--limit 限量）
py -3 .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --xml mods/<plugin>/<plugin>_english_chinese_translated.xml --rec INFO --limit 500

# 逐句 stdin（配合其它工具管道）
echo '把文件上传到云端。' | py -3 .../fantasy_audit.py --stdin

# JSON 输出（供脚本消费）
py -3 .../fantasy_audit.py --xml ... --limit 200 --json
```

选项：`--model`（指定本地 Ollama 模型名，按部署环境配置）、`--host`
（默认 `http://127.0.0.1:11434`，Ollama 默认端口）、`--cache`
（默认 `_tmp/data/fantasy-audit-cache.json`，同句不重查，增量扫描秒级）、
`--no-cache`、`--limit N`、`--json`、`--rec PREFIX`。

## 模型要求与已知边界

- 走 `/api/chat` + system/user 模板（世界观锚点在 system，句子在 user）。
- **`think: false` 必须是顶层参数**，不能放 `options` 里——放 options 会被
  静默忽略，模型进入思考模式且不回填 response，表现为输出全空。
- 模型规模：1B 级模型判别力不足（接近随机），至少需要 ≥9B 量级且中文
  指令跟随良好的模型；部署方可按此门槛自选模型，`--model` 指定即可。
- 边界误报（接受，人眼秒放）：报告文书类（「写个报告」）、现实古国引用
  （「罗马」——奇幻作品常引作古文明）。
- 边界漏报（低频，词表可兜）：「希腊神话」类（「神话」在奇幻有对应概念
  会迷惑模型）。
- 速度：约 0.2s/句。全量 canonical ~8000 已译行 ≈ 30 分钟；单批 45 行 ≈
  10 秒。缓存落盘后重复扫描同批秒回。
- prompt 内置世界观锚点（九圣灵/圣骑士/神殿/王国/商会/税收/文书/骑士团），
  不要随意删减——删掉会导致斯坦达尔等 TES 专名台词误报。

## 与其它工具的分工

| 工具                             | 层   | 抓什么                                           |
| -------------------------------- | ---- | ------------------------------------------------ |
| translation-quality-gate TERM004 | 词形 | unconditional 禁词（世界观荒谬词）               |
| translation-fidelity-scan | 规则 | 术语禁形/丢否定/空译/英文残留 + ANACHRONISM 候选 |
| **本工具**                       | 语义 | 词表外的现代/现实语义出戏句子                    |
| 词表同形误报（夜总会/西藏）      | —    | 语义层正确放行（两套互补）                       |

## 安全边界

- 只读。不修改 XML、译文、词库、契约。
- 候选不自动 FAIL、不自动改稿；Agent 复核后决定回改或放行。
- 发现的真出戏（词表外新词）应回填 `global-forbidden-words.json`
  （unconditional）或 `translation-fidelity-scan` 的候选表，形成闭环。

## 验证

- 冒烟：对任意含已译行的 translation result 跑 `--file`，确认能输出候选（可为空）。
- 更换 prompt 或模型后，用一组已知边界用例（云端=出戏、固若金汤=正常、
  斯坦达尔台词=正常、夜总会同形句=正常）人工对照，确认无明显退化。
- 具体基准数据（含边界用例明细）属本地调试记录，不入库。
