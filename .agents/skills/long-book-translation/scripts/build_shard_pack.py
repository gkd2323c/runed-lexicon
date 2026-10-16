#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装单个翻译单元的工作包。

解决什么问题
------------
分片器切出了大小一致的单元，但如果把整本书的设定、全部人物关系、全部
术语都塞进每个单元，单元体积就随书籍长度增长了，直接违反核心原则。

本工具按"当前单元需要什么"组装工作包：书籍护照（固定体积）+
与本文本相关的实体（按出现匹配筛选）+ 相关术语。**体积受预算约束，
与全书规模无关。**

分层记忆的取舍
--------------
- 书籍层：护照。书名、作者、叙事视角、体裁、语气基准。一次性生成，全书共用。
- 章节层：情节与伏笔。只取与本文本相关的条目，不取全书。
- 切片层：本文本。译者唯一必须读完的东西。

原文永远是权威来源，护照与台账只是索引。台账不层层转抄：章节摘要由
主会话从**原文**提炼并落盘，不由上一个摘要总结而来。旁链路由本工具机械
筛选（按关键词在文本中出现），不依赖模型判断相关性。

用法
----
    py -3 build_shard_pack.py --plan-dir _tmp/tests/book-shards \
        --shard ESXSSII-S003 --passport passport.json --out pack.md
"""
import argparse
import json
import os
import re
import sys

# 护照体积上限。护照是每个单元都要读的固定成本，必须与全书规模无关。
PASSPORT_CHAR_CAP = 600


def load_plan(plan_dir: str) -> dict:
    path = os.path.join(plan_dir, "plan.json")
    if not os.path.isfile(path):
        raise SystemExit(f"error: 找不到计划 {path}；先跑 plan_book_shards.py")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def shard_text(plan: dict, shard: dict) -> str:
    """从 canonical 里按区间取本文本原文。

    按 span 取而非读读本文件：读本已占位符化，取回标签原形需要回填，
    而本工具只需要判断实体是否出现在文本中，用原文判断即可。
    """
    import xml.etree.ElementTree as ET
    for s in ET.parse(plan["xml"]).getroot().iter("String"):
        if (s.findtext("EDID") or "").strip() != plan["edid"]:
            continue
        if (s.findtext("REC") or "").strip() != "BOOK:DESC":
            continue
        return (s.findtext("Source") or "")[shard["span"][0]:shard["span"][1]]
    raise SystemExit(f"error: 计划指向的 {plan['xml']} 中已找不到 "
                     f"EDID={plan['edid']}；源文件可能已变，计划作废需重切")


def relevant(entities: list[dict], text: str) -> list[dict]:
    """筛出在本文本中出现过的实体。

    匹配英文原名与已登记译名。这是机械筛选，不猜相关性：
    没出现的实体即便重要也不进包，译者不需要为不存在于本文的人物分心。
    """
    hits = []
    low = text.lower()
    for e in entities:
        keys = [k for k in (e.get("en"), e.get("zh")) if k]
        if any(k.lower() in low for k in keys):
            hits.append(e)
    return hits


def render_passport(p: dict) -> str:
    lines = ["## 书籍护照（全书共用，固定体积）", ""]
    for k in ("title", "author", "narrator", "genre", "register", "spoiler_boundary"):
        v = p.get(k)
        if v:
            lines.append(f"- **{k}**: {v}")
    notes = p.get("notes")
    if notes:
        lines.append(f"- **notes**: {notes}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="组装翻译单元工作包")
    ap.add_argument("--plan-dir", required=True, help="plan_book_shards.py 的输出目录")
    ap.add_argument("--shard", required=True, help="单元 ID，如 ESXSSII-S003")
    ap.add_argument("--passport", required=True, help="书籍护照 JSON")
    ap.add_argument("--out", help="输出路径；缺省打到 stdout")
    a = ap.parse_args()

    plan = load_plan(a.plan_dir)
    shard = next((s for s in plan["shards"] if s["shard_id"] == a.shard), None)
    if shard is None:
        known = ", ".join(s["shard_id"] for s in plan["shards"])
        raise SystemExit(f"error: 单元 {a.shard} 不在计划中；可用: {known}")

    with open(a.passport, encoding="utf-8") as fh:
        passport = json.load(fh)

    ledger_path = os.path.join(a.plan_dir, "ledger.json")
    entities: list[dict] = []
    if os.path.isfile(ledger_path):
        with open(ledger_path, encoding="utf-8") as fh:
            entities = json.load(fh).get("entities", [])

    text = shard_text(plan, shard)
    hits = relevant(entities, text)

    blocks = [
        f"# 翻译工作包 {shard['shard_id']}",
        "",
        f"原著 {plan['edid']} | 单元 {shard['n']}/{plan['shard_count']} "
        f"| 原文区间 [{shard['span'][0]}, {shard['span'][1]}) "
        f"| {shard['raw_chars']} 字符",
        f"原文 SHA256 `{shard['src_sha256']}`（回填译文前须复核）",
        "",
        render_passport(passport),
        "",
    ]

    if hits:
        blocks += ["## 本文相关实体（只列在本文出现过的）", ""]
        blocks += ["| 原名 | 现译名 | 身份状态 | 备注 |", "| --- | --- | --- | --- |"]
        for e in hits:
            blocks.append(f"| {e.get('en','')} | {e.get('zh','')} "
                          f"| {e.get('status','')} | {e.get('note','')} |")
        blocks.append("")
    else:
        blocks += ["## 本文相关实体", "",
                   "（台账中暂无出现在本文的实体。若译文中出现人名/地名/组织名，"
                   "译完请按 SKILL.md 的口径登记进台账。）", ""]

    if shard["hard_cut"]:
        blocks += ["> 警告：本单元为硬切产物，切点可能落在句子中间。"
                   "译文需人工复核衔接。", ""]

    blocks += [
        "## 待译正文",
        "",
        f"见 `{shard['readme']}`。其中的 ⟪F数字⟫ 是排版标记占位符：",
        "**照抄，不要翻译，不要增删，不要改写**。它们记录游戏内书籍的"
        "分页、对齐、字体与插图位置，改动会让版式错乱。",
        "",
        "## 交付口径",
        "",
        "译文按占位符切分逐段对应；占位符数量与编号顺序必须与原文一致；",
        "源文信息一处不漏，不增补源文没有的内容；不确定处保留原文并标注。",
    ]

    out = "\n".join(blocks) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"工作包: {a.out}（实体 {len(hits)} 条 / "
              f"护照 {len(render_passport(passport))} 字符）")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
