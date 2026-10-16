#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超长书籍翻译的分片规划器。

解决什么问题
------------
一本书（BOOK:DESC）是一个字符串，内含全部分页；3 万字的巨著也是同一条
String。直接翻译要么超出单次上下文预算，要么被机械按字符切断在句子中间。

本工具按叙事边界把一本书切成若干翻译单元，每个单元带稳定 ID、原文哈希
与位置记录，产出可续跑的分片计划。**只读源 XML，不翻译，不写回。**

核心原则：翻译 Agent 的上下文长度不随书籍长度增长
------------------------------------------------
一本书五千字还是三十万字，每个翻译单元的原文都受同一个预算约束
（--budget）。书的长度只增加单元数量，不增加单元体积。长上下文风险
因此被关在固定范围内，而不是靠模型能力去扛。

分片边界优先级
--------------
1. `[pagebreak]` —— 章节/分页边界，最强边界
2. 自然段落边界（双换行，即 XML 里解码后的空行）
3. 硬预算切分 —— 仅当单章本身超预算时启用，切点落在段落边界

格式标记处理
------------
Skyrim 书籍文本不是纯文本。标记有两类：
- 字面量：`[pagebreak]`
- XML 实体形态的标签：`&lt;p align=&quot;center&quot;&gt;`、`&lt;img src=...&gt;`

本工具在 **XML 原文层面**（未解码）操作，不用 ElementTree 解码文本：
ElementTree 会在写回时重新转义（`&apos;` → `'`），造成与原文件逐字节
不一致。标记被抽出为占位符 token 后再交给翻译 Agent，回填由调用方负责。

已知局限
--------
`[sic]` `[x]` `[redacted]` `[untranslatable]` 是作者标给自己的元信息，
量极少且语义上属于文本内容，本工具**不做**占位符化，留在正文里译。
判定依据：它们是作者对自身的注记，不是排版控制符；且数量在数十次量级，
不构成格式风险。

用法
----
    py -3 plan_book_shards.py --xml <canonical.xml> --edid <BOOK:DESC的EDID> \
        --out-dir _tmp/data/book-shards --budget 8000

输出：
    shards/shard-001.md      每个翻译单元的读本（占位符化后的原文）
    plan.json                分片计划（单元 ID、原文哈希、字符数、状态）
    ledger.json              初始实体台账（书籍级，供各单元共享，只增不改）
"""
import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

# 分页/分章边界。这是叙事边界，不是排版装饰：一本书在游戏里按它翻页。
PAGEBREAK_RE = re.compile(r"\[pagebreak\]")

# 排版标签。注意：本工具经 ElementTree 取得的是**解码后**的文本（`<p align="center">`），
# 不是 XML 原文里的实体形态（`&lt;p align=&quot;center&quot;&gt;`）。
# 分片器产物是给翻译 Agent 的读本文件而非 XML，在解码文本上工作是合理的；
# 写回时的逐字节保真由 write_translations.py 的 span 定位负责，两者不重叠。
TAG_RE = re.compile(r"<[^<>\n]*>")

# 分页符。既是分片边界的定位依据，也是**必须保护的格式控制符**：
# 漏掉它游戏内书籍分页就乱。因此它同时出现在两个地方：
# - 切分阶段按它定位章节（spans_by_pagebreak 用 PAGEBREAK_RE）
# - 占位符化阶段把它抽成 token（extract_format_tokens 用 MARK_RE）
# 两者用不同正则是有意的：切分只认它本身，编号还要包含尖括号标签。
PAGEBREAK_RE = re.compile(r"\[pagebreak\]")
MARK_RE = re.compile(r"\[pagebreak\]|<[^<>\n]*>")

# 段落在解码后是空行。用 "换行 + 仅空白 + 换行" 定位段落边界。
PARA_BREAK_RE = re.compile(r"\n[ \t]*\n[ \t]*\n?")

# 占位符 token 用罕见的括号对，避免与正文中文或常见标点冲突
TOKEN_FMT = "⟪F{}⟫"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_entry(xml_path: str, edid: str, rec: str | None) -> tuple[int, str, str]:
    """定位目标条目，返回 (XML 中的 String 序号, REC, Source 原文)。

    rec 为 None 时按 EDID 唯一匹配推断；多个同名 EDID 且 REC 不同时
    （书籍 FULL 与 DESC 同 EDID），必须显式给 rec，否则拒绝猜。
    Source 保持解码后形态，交由调用方处理占位符。
    """
    root = ET.parse(xml_path).getroot()
    hits = []
    for i, s in enumerate(root.iter("String")):
        if (s.findtext("EDID") or "").strip() != edid:
            continue
        r = (s.findtext("REC") or "").strip()
        if rec is not None and r != rec:
            continue
        hits.append((i, r, s.findtext("Source") or ""))
    if not hits:
        raise SystemExit(f"error: 在 {xml_path} 中未找到 EDID={edid}"
                         f"{' REC=' + rec if rec else ''}")
    if len(hits) > 1:
        recs = ", ".join(h[1] for h in hits)
        raise SystemExit(
            f"error: EDID={edid} 命中多条记录（{recs}）；"
            "书籍 FULL 与 DESC 同 EDID，请用 --rec 指定")
    return hits[0]


# 按 REC 选默认预算与边界策略。数值来自项目实测分布：
# - BOOK:DESC 最长 32514，p99 为 8038，默认 8000 使绝大多数章节一章一单元
# - QUST:CNAM 最长 965、MESG:DESC 最长 941，本就在常规翻译单元容量内，
#   默认预算取其上界，正常不触发分片（只对超长个体生效）
REC_PROFILE = {
    "BOOK:DESC": {"budget": 8000, "boundary": "pagebreak"},
    "QUST:CNAM": {"budget": 900, "boundary": "sentence"},
    "MESG:DESC": {"budget": 900, "boundary": "sentence"},
    "default": {"budget": 2000, "boundary": "sentence"},
}

# 句子边界。QUST:CNAM / MESG:DESC 是单段纯文本，无换行无分页符，
# 段落与分页边界均不可用，只能退到句子边界。句末标点后跟空白即断点。
SENT_END_RE = re.compile(r"(?<=[.!?。！？])[ \t]*\n?[ \t]*")

def boundary_re(boundary: str) -> re.Pattern:
    return {"pagebreak": PAGEBREAK_RE,
            "paragraph": PARA_BREAK_RE,
            "sentence": SENT_END_RE}.get(boundary, SENT_END_RE)


def extract_format_tokens(text: str) -> tuple[str, list[str]]:
    """把排版标记抽成占位符。

    返回 (占位符化后的文本, 标记原形列表)。列表下标即 token 编号，
    回填时按编号替换回原形。标记不进入翻译视野，避免模型改动版式。

    受保护标记含两类，权重相同：
    - 尖括号标签：`<p align="center">`、`</p>`、`<img src=...>`、`<font ...>` 等
    - 分页符：`[pagebreak]`

    后者曾不被保护，导致某轮拼合后 `[pagebreak]` 全数丢失而校验未报——
    漏掉它游戏内分页即错乱，与标签同级，必须纳入同一套守恒校验。

    `[sic]` `[x]` `[redacted]` `[untranslatable]` 是作者标给自己的元信息，
    量在数十次量级且语义上属于文本内容，不占位符化，留在正文里译。
    """
    tokens: list[str] = []

    def repl(m: re.Match) -> str:
        tokens.append(m.group(0))
        return TOKEN_FMT.format(len(tokens) - 1)

    return MARK_RE.sub(repl, text), tokens


def spans_by_boundary(text: str, sep: re.Pattern) -> list[tuple[int, int]]:
    """按边界正则定位片段区间。

    返回 [(start, end)]，连续不重叠，并集等于全文。分隔符归入前一区间尾部，
    因此不会产生空片段，守恒可机械校验。

    为什么不按 split：split 丢掉分隔符本身，join 回去与原文不符，哈希链断裂。
    用区间则每个单元的偏移可回查，守恒可断言。
    """
    marks = list(sep.finditer(text))
    if not marks:
        return [(0, len(text))] if text else []
    spans: list[tuple[int, int]] = []
    if marks[0].start() > 0:
        spans.append((0, marks[0].start()))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        spans.append((m.start(), end))
    return spans


def pack(spans: list[tuple[int, int]], text: str, budget: int,
         fallback: list[str]) -> list[dict]:
    """把片段装进预算内。

    两步，缺一不可：

    1. **贪心装包**（merge）：相邻的首选片段若都不超预算，合并到接近预算为止。
       书籍的 [pagebreak] 切出的 span 常远小于预算（一页 200 字符、预算 8000），
       不合并会让本可一章一单元的书碎成几十片，翻译调用次数翻倍，
       每片的上下文比预算允许的小一个数量级。
       合并只在同一边界语义内进行：不跨 [pagebreak] 合并（章节是叙事单元），
       但同一章内的多个句子片段可以合成一个单元。

    2. **超预算降级**（split）：装包后仍超预算的单元，按 fallback 中的更细边界
       依次二次切。所有边界用尽仍超预算（单句就超预算）→ 标 hard_cut，
       读本带警告，需人工复核衔接。

    硬切不是缺陷，是诚实的记录：静默产出超预算单元却不标记，
    会让下游误以为局部预算仍然成立。
    """
    # ---- 第 1 步：贪心装包 ----
    # 首选边界切出的片段常远小于预算（书籍一页约 200 字符、预算 8000）。
    # 不合并会让一章碎成几十片，翻译调用次数翻倍，每片上下文比预算允许的
    # 小一个数量级。合并只在同一边界语义内进行：不跨 [pagebreak] 合并
    # （章节是叙事单元），同章内的多个句子片段可合成一个单元。

    def merge_spans(sp_: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        cs: int | None = None
        ce = 0
        for s, e in sp_:
            if e - s > budget:
                if cs is not None:
                    out.append((cs, ce))
                    cs, ce = None, 0
                out.append((s, e))
                continue
            if cs is None:
                cs, ce = s, e
                continue
            if (ce - cs) + (e - ce) <= budget:
                ce = e
            else:
                out.append((cs, ce))
                cs, ce = s, e
        if cs is not None:
            out.append((cs, ce))
        return out

    merged = merge_spans(spans)

    # ---- 第 2 步：超预算降级 ----
    # 降级步切完必须再装包：更细边界（尤其段落）在日式排版文本上断点极密，
    # 每句一换行，纯切不并会产生上百个碎片单元，预算等于作废。

    def merge_spans(sp_: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        cs: int | None = None
        ce = 0
        for s, e in sp_:
            if cs is None:
                cs, ce = s, e
                continue
            if (ce - cs) + (e - ce) <= budget:
                ce = e
            else:
                out.append((cs, ce))
                cs, ce = s, e
        if cs is not None:
            out.append((cs, ce))
        return out

    def split_unit(s: int, e: int, depth: int) -> list[tuple[int, int]]:
        if e - s <= budget:
            return [(s, e)]
        if depth >= len(fallback):
            return [(s, e)]  # 边界用尽，交由上层标 hard_cut
        sep = boundary_re(fallback[depth])
        rel = spans_by_boundary(text[s:e], sep)
        if len(rel) <= 1:
            return split_unit(s, e, depth + 1)  # 本边界无新断点，试下一个
        abs_rel = [(s + rs, s + re_) for rs, re_ in rel]
        # 先装包再逐个递归：装包把碎片并回预算内，递归只处理仍超预算的
        out: list[tuple[int, int]] = []
        for a, b in merge_spans(abs_rel):
            if b - a <= budget:
                out.append((a, b))
            else:
                out.extend(split_unit(a, b, depth + 1))
        return out

    units: list[dict] = []
    for s, e in merged:
        if e - s <= budget:
            units.append({"start": s, "end": e, "hard_cut": False})
            continue
        pieces = split_unit(s, e, 1)
        units.extend({"start": ps, "end": pe,
                      "hard_cut": pe - ps > budget} for ps, pe in pieces)
    return units


def main() -> int:
    ap = argparse.ArgumentParser(description="长文本翻译分片规划器")
    ap.add_argument("--xml", required=True, help="canonical 译文 XML")
    ap.add_argument("--edid", required=True, help="目标条目的 EDID")
    ap.add_argument("--rec", help="记录类型，如 BOOK:DESC / QUST:CNAM / MESG:DESC；"
                                  "缺省按 EDID 唯一匹配推断")
    ap.add_argument("--out-dir", required=True, help="输出目录（约定 _tmp/ 下）")
    ap.add_argument("--budget", type=int,
                    help="单翻译单元原文字符上限（占位符化前）。"
                         "缺省按 REC_PROFILE 取该记录类型的默认值。"
                         "该值是架构约束而非模型上限：翻译 Agent 的上下文"
                         "长度不随文本长度增长，只随单元体积增长。")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的计划")
    a = ap.parse_args()

    idx, rec, raw = resolve_entry(a.xml, a.edid, a.rec)
    if not raw:
        raise SystemExit(f"error: EDID={a.edid} 的 Source 为空")

    profile = REC_PROFILE.get(rec, REC_PROFILE["default"])
    budget = a.budget if a.budget is not None else profile["budget"]
    first = profile["boundary"]
    # 降级链：首选边界之后依次尝试更细边界。书籍首选 pagebreak，
    # 降段落；纯文本首选 sentence，其后无更细边界，用尽即硬切。
    fallback = ["pagebreak", "paragraph"] if first == "pagebreak" else ["sentence"]

    os.makedirs(a.out_dir, exist_ok=True)
    shard_dir = os.path.join(a.out_dir, "shards")
    os.makedirs(shard_dir, exist_ok=True)

    plan_path = os.path.join(a.out_dir, "plan.json")
    if os.path.exists(plan_path) and not a.force:
        raise SystemExit(f"error: {plan_path} 已存在；用 --force 覆盖，"
                         f"或在既有计划上继续（见 SKILL.md 的检查点恢复）")

    spans = spans_by_boundary(raw, boundary_re(first))
    units = pack(spans, raw, budget, fallback)

    # 守恒断言：切分必须可逆。任一单元区间重叠或漏区间都会让哈希链失效，
    # 因此在这里机械拦截，不留给下游发现。
    covered = 0
    for u in units:
        if u["start"] != covered:
            raise SystemExit(
                f"error: 分片不守恒，单元起点 {u['start']} 与上一终点 {covered} 不衔接；"
                f"切分逻辑有缺陷，拒绝产出计划")
        covered = u["end"]
    if covered != len(raw):
        raise SystemExit(
            f"error: 分片覆盖 {covered} 字符，原文 {len(raw)} 字符，未覆盖全文；"
            f"拒绝产出计划")

    shard_meta = []
    for n, u in enumerate(units, 1):
        unit = raw[u["start"]:u["end"]]
        body, tokens = extract_format_tokens(unit)
        sid = f"{a.edid}-S{n:03d}"
        src_hash = sha256_hex(unit)
        # 原文字符数才是原文规模；body 长度只反映翻译视野的体积
        readme = os.path.join(shard_dir, f"shard-{n:03d}.md")
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(f"# {sid}\n\n")
            fh.write(f"> 单元 {n}/{len(units)} | 原文区间 [{u['start']}, {u['end']}) "
                     f"| {len(unit)} 字符 | 占位符化后 {len(body)} 字符\n")
            fh.write(f"> 原文 SHA256: {src_hash}\n")
            if u["hard_cut"]:
                fh.write("> 警告：本章内部无段落边界，已硬切。\n")
            fh.write("\n## 待译正文\n\n")
            fh.write(body)
            fh.write("\n")
        shard_meta.append({
            "shard_id": sid,
            "n": n,
            "span": [u["start"], u["end"]],
            "raw_chars": len(unit),
            "body_chars": len(body),
            "src_sha256": src_hash,
            "format_token_count": len(tokens),
            "hard_cut": u["hard_cut"],
            "readme": os.path.relpath(readme, a.out_dir),
            "status": "pending",
        })

    plan = {
        "xml": os.path.abspath(a.xml),
        "xml_string_index": idx,
        "edid": a.edid,
        "rec": rec,
        "boundary": fallback,
        "budget": budget,
        "book_raw_chars": len(raw),
        "book_sha256": sha256_hex(raw),
        "span_count": len(spans),
        "shard_count": len(units),
        "hard_cut_shards": sum(1 for u in units if u["hard_cut"]),
        "over_budget_spans": [
            {"span": [s, e], "chars": e - s}
            for (s, e) in spans if e - s > budget
        ],
        "shards": shard_meta,
    }
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)

    ledger_path = os.path.join(a.out_dir, "ledger.json")
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump({
            "edid": a.edid,
            "note": "实体台账：随译随增，只增不改。"
                    "人物/概念首次出现时登记，身份状态变化时追加新条目而非改旧条目。"
                    "各翻译单元只读取与自己相关的条目，不全量加载。",
            "entities": [],
            "terms": {},
        }, fh, ensure_ascii=False, indent=1)

    print(f"{rec} {a.edid}: {len(raw):,} 字符 / {len(spans)} 个首选片段 "
          f"/ {len(units)} 个翻译单元（预算 {budget:,}，边界 {fallback}）")
    if plan["over_budget_spans"]:
        print(f"  ! 超预算首选片段 {len(plan['over_budget_spans'])} 个，"
              f"已按 {' > '.join(fallback[1:]) or '无更细边界'} 二次切分")
    if plan["hard_cut_shards"]:
        print(f"  ! 硬切单元 {plan['hard_cut_shards']} 个："
              f"该章内部无段落边界，切点可能落在句子中间，需人工复核")
    print(f"  守恒校验通过：各单元区间连续不重叠，并集等于全文")
    print(f"  计划: {os.path.relpath(plan_path, os.getcwd())}")
    print(f"  台账: {os.path.relpath(ledger_path, os.getcwd())}")
    print(f"  读本: {shard_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
