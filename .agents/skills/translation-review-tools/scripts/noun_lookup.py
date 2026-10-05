#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""term_digest 的「专名速查」段（待并入 term_digest.py）。

为什么需要：term-digest 的 `MOD:` 行只给**词表已收录**的专名。词表没收录的专名
在 digest 里一个字都不出现，译者只能自己猜译形——这正是「微光海景居」（库内
既成面是「海景」）与「德罗'达吉」（既成面「德罗·达里'奇」，音与撇号位置都错）
两处新造形的来源。

本段给足三件事：① 专名在**库内已译行**里的既成形（名称行最权威）② 各形的行数
与记录族，够判断既成面还是孤例 ③ 库内若已分裂成多形，明确点出（别再造第三形）。

**判据是结构证据不是形态猜测。** 前两版都栽在形态猜测上：
  · 第一版「首字母大写/驼峰/连字符」→ 句中的 `Palamay if you were I` 漏掉，
    而 `I've`/`That's`/`can't` 全被当专名（产出「正文行 2266 处」这种无意义条目）
  · 第二版改用 canonical 已译行圈专名 → 名称行常常还没译（`Palamay` 就是），
    全新专名被整批漏掉，而那恰恰最需要提醒译者「这是新专名、要谨慎」
现在用**源 XML 的名称行**圈定「哪些是专名」（REC + 英文，结构上就是玩家在界面
里看到的名字），用 canonical 查既成译形。分两档输出：
  A 库内已有译形 → 指向现成答案；已分裂的明确点出
  B 库内无既成译形 → 标明「全新专名，需新拟，notes 写明依据」

只报不判：选哪个形仍是译者与主会话的职责，本段只保证**信息不缺**。
"""
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

CJK = re.compile(r"[\u4e00-\u9fff]")
_SEP = re.compile(r"[\s\-_'’]+")

# 名称行记录族：源文即玩家在界面里看到的名字。
# **只收真正承载专名的族**。第一版把 QUST/BOOK/FLOR/SOUL 等也算进来，于是
# 任务标题 `Stay`、书名之类混进「专名速查」——它们不是专名，译者看了只会噪音。
NAME_RECS = ('NPC_', 'CELL', 'LCTN', 'MISC', 'RACE', 'FACT', 'ARMO', 'ALCH',
             'SPEL', 'WEAP', 'INGR', 'KEYM')
# 用于查「既成译形」的范围再窄一些：只认真正会显示名字的族
SHOWN_RECS = ('NPC_', 'CELL', 'LCTN', 'MISC', 'RACE', 'FACT', 'ARMO', 'ALCH',
              'SPEL', 'WEAP', 'INGR', 'KEYM')


def _norm(s):
    return _SEP.sub("", (s or "").lower())


def _load(path: str):
    return list(ET.parse(path).getroot().iter("String"))


def collect_known_names(source_xml: str) -> dict:
    """库内专名集合（取源 XML 的名称行，译没译都算）。"""
    names = {}
    for i, s in enumerate(_load(source_xml)):
        rec = s.findtext("REC") or ""
        if not rec.startswith(NAME_RECS):
            continue
        src = (s.findtext("Source") or "").strip()
        if not src or len(src) > 60 or "<" in src or ">" in src:
            continue
        if not src[0].isalpha():
            continue
        key = _norm(src)
        if key and key not in names:
            names[key] = {"src": src, "idx": i, "rec": rec}
    return names


def unregistered_nouns_section(context: dict, canonical_path: str,
                               registry_english: set,
                               source_xml: str | None = None) -> list:
    """专名速查：词表无此条的专名，分「库内有既成译形」与「全新需新拟」两档。"""
    if not canonical_path:
        return []
    known = collect_known_names(source_xml or canonical_path)
    batch_src = []
    for b in (context.get("batches") or []):
        for e in (b.get("entries") or []):
            batch_src.append(((e.get("xml") or {}).get("source")) or "")
    rows = _load(canonical_path)

    have, fresh = [], []
    for key, info in known.items():
        if key in registry_english:
            continue
        # 词边界必须在**原始文本**上匹配。第一版把整段 batch 源文先 `_norm`（去空格）
        # 再找边界，于是 `Palamay if I were you` 归一成 `palamayifwereyou`，
        # 负向前视永远失败 → 整段报「无」。F 池当初是逐行匹配原始 src 才没踩这个。
        pat = re.compile(r"(?<![a-z])" + re.escape(info["src"].lower())
                         + r"(?![a-z])")
        n_body = sum(1 for s in batch_src if pat.search((s or "").lower()))
        if not n_body:
            continue
        variants = defaultdict(list)
        for i, s in enumerate(rows):
            r0 = s.findtext("REC") or ""
            if not r0.startswith(SHOWN_RECS):
                continue
            src0 = (s.findtext("Source") or "").strip()
            if not pat.search(src0.lower()):
                continue
            d = (s.findtext("Dest") or "").strip()
            if not d or d == src0 or not CJK.search(d):
                continue
            variants[d].append((i, r0))
        if not variants:
            fresh.append((n_body, f"  {info['src']}（{info['rec'].split(':')[0]}）"
                                 f"　**库内无既成译形**，需新拟：按 terms.json / "
                                 f"DICTIONARY.md 的音译或意译惯例，notes 写明依据"))
            continue
        top = sorted(variants.items(), key=lambda kv: -len(kv[1]))
        zh_desc = "、".join(f"「{d}」x{len(v)}" for d, v in top[:3])
        # 「已分裂」只算**互不包含**的多形。「帕拉梅」与「科里纳尔和帕拉梅的家」
        # 是同一名的裸形与派生形（后者多出地名），不是两种译法；第一版按不同
        # 字符串一律判分裂，把每条派生都误报成风险。
        _norms = [_norm(d) for d in variants]
        distinct = {n for n in _norms if n}
        _pairs = [(a, b) for i, a in enumerate(distinct) for b in list(distinct)[i + 1:]]
        real_split = any(not (a in b or b in a) for a, b in _pairs)
        split = "　**库内已分裂**" if real_split else ""
        authority = "、".join(
            f"[{i}]{r.split(':')[0]}" for i, r in variants[top[0][0]][:3])
        have.append((len(top[0][1]) * 10 + n_body,
                     f"  {info['src']}  →  {zh_desc}　(权威面 {authority}){split}"))

    out = ["== 本批专名速查：词表无此条，但【库内已有译形】（勿自造）=="]
    if have:
        have.sort(reverse=True)
        out.append("  库里已有的译形就是答案，名称行最权威。库内已分裂的挑占多数"
                   "+跨记录族的那形，拿不准就在 notes 写明「本批取 X，因库内多数"
                   "形为 X」，别自造第三形。")
        out.extend(h for _, h in have)
    else:
        out.append("  （无）")
    out.append("")
    out.append("== 本批专名速查：词表无、库内也无既成译形（**全新专名，需新拟**）==")
    if fresh:
        fresh.sort(reverse=True)
        out.extend(h for _, h in fresh)
    else:
        out.append("  （无）")
    return out
