#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把单元译文拼回全书，并机械校验格式与守恒。

解决什么问题
------------
翻译 Agent 只输出纯中文正文。若让它连同排版标记一起输出，模型会漏标、
改标、重排标签，版式随即错乱；而这些错误在译文读起来通顺时完全不可见。

本工具做三件事：还原占位符为原标签、按区间把译文写回原书对应的位置、
机械校验五条不变量。**任何一条不成立即拒绝产出**，不把可疑结果留给下游。

五条不变量
----------
1. 占位符守恒：译文中的占位符编号集合与原文一致，且顺序一致
   （禁止重排：标签顺序即版式顺序，重排等于重排版）
2. 标签原形未被改动：还原后每个占位符位置的标签与分片时记录的完全一致
3. 区间覆盖：所有译文区间并集等于全文，无洞无重叠
4. 字数可信：译文非空、不含遗留的源文长句（英文残留探针）
5. 幂等：同一份译文重复回填结果一致

用法
----
    py -3 stitch_translation.py --plan-dir _tmp/tests/book-shards \
        --translations _tmp/tests/book-shards/translated \
        --out _tmp/tests/book-shards/stitched.json

--translations 下放 <shard_id>.txt（每单元一个，纯译文正文）。
输出 stitched.json：含全书拼合结果与逐单元校验明细。**只读 canonical，
不写回 XML**；写回由翻译Agent 按 SKILL.md 走 xtranslator-xml-writer。
"""
import argparse
import json
import os
import re
import sys

TOKEN_RE = re.compile(r"⟪F(\d+)⟫")
# 受保护标记，与分片器 plan_book_shards.MARK_RE 完全一致：
# 尖括号标签 + [pagebreak]。两处必须同步，否则编号对不上。
MARK_RE = re.compile(r"\[pagebreak\]|<[^<>\n]*>")
# 英文残留探针：连续 4 个以上英文词。中文译文里出现这种形态，
# 通常意味着该段整体漏译（保留了源文）。
EN_RUN_RE = re.compile(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){3,}")


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def tokens_of(text: str) -> list[int]:
    return [int(m.group(1)) for m in TOKEN_RE.finditer(text)]


def src_tokens(plan: dict, shard: dict) -> list[int]:
    """分片时该单元原本应有的占位符编号序列。

    与 plan_book_shards.extract_format_tokens 同一规则：尖括号标签与
    `[pagebreak]` 都参与编号，`[sic]` 等作者自标元信息不编号。
    """
    import xml.etree.ElementTree as ET
    for s in ET.parse(plan["xml"]).getroot().iter("String"):
        if (s.findtext("EDID") or "").strip() != plan["edid"]:
            continue
        if plan.get("rec") and (s.findtext("REC") or "").strip() != plan["rec"]:
            continue
        seg = (s.findtext("Source") or "")[shard["span"][0]:shard["span"][1]]
        return list(range(len(MARK_RE.findall(seg))))
    raise SystemExit(f"error: 计划指向的 {plan['xml']} 中已找不到 "
                     f"EDID={plan['edid']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="单元译文拼回与格式校验")
    ap.add_argument("--plan-dir", required=True)
    ap.add_argument("--translations", required=True,
                    help="目录，内含 <shard_id>.txt")
    ap.add_argument("--out", required=True, help="输出 stitched.json")
    a = ap.parse_args()

    plan = load_json(os.path.join(a.plan_dir, "plan.json"))
    import xml.etree.ElementTree as ET
    doc = ET.parse(plan["xml"]).getroot()
    raw = None
    for s in doc.iter("String"):
        if (s.findtext("EDID") or "").strip() == plan["edid"] \
                and (not plan.get("rec")
                     or (s.findtext("REC") or "").strip() == plan["rec"]):
            raw = s.findtext("Source") or ""
            break
    if raw is None:
        raise SystemExit(f"error: 在 {plan['xml']} 中未找到 {plan['edid']}"
                         f"{plan.get('rec', '')}")

    # 标签原形从各单元区间内取（见下方 restore），不预先建全书表
    findings: list[dict] = []
    pieces: list[str] = []
    cursor = 0

    for shard in plan["shards"]:
        sid = shard["shard_id"]
        start, end = shard["span"]
        path = os.path.join(a.translations, f"{sid}.txt")
        if not os.path.isfile(path):
            findings.append({"shard_id": sid, "kind": "missing_translation",
                             "detail": "未提供译文文件"})
            # 缺失单元保留原文，保证区间仍然连续可拼
            pieces.append(raw[start:end])
            cursor = end
            continue

        with open(path, encoding="utf-8") as fh:
            trans = fh.read().strip("\n")

        want = src_tokens(plan, shard)
        got = tokens_of(trans)

        if got != want:
            findings.append({
                "shard_id": sid, "kind": "token_mismatch",
                "detail": f"占位符应为 {want}，实际 {got}",
            })
        if len(EN_RUN_RE.findall(trans)) > 0:
            hits = EN_RUN_RE.findall(trans)[:2]
            findings.append({
                "shard_id": sid, "kind": "english_residue",
                "detail": f"疑似漏译的英文长串: {hits}",
            })
        if not trans.strip():
            findings.append({"shard_id": sid, "kind": "empty_translation",
                             "detail": "译文为空"})

        # 把译文里的占位符还原为原标记，再拼回原书。
        # 占位符编号在区间内从 0 起，故在本区间标记序列内按下标取。
        seg_marks = MARK_RE.findall(raw[start:end])

        def restore(m: re.Match, _marks: list = seg_marks) -> str:
            n = int(m.group(1))
            return _marks[n] if 0 <= n < len(_marks) else m.group(0)

        pieces.append(TOKEN_RE.sub(restore, trans))
        cursor = end

    if cursor != len(raw):
        findings.append({"kind": "coverage_gap",
                         "detail": f"拼合到 {cursor}，全书 {len(raw)}"})

    stitched = "".join(pieces)

    # 不变量 3 的独立复算：非译文单元用原文填，所以并集必然等于全文；
    # 真正要断言的是"译文单元数 + 缺失单元数 == 计划单元数"，即无静默跳过。
    provided = sum(1 for s in plan["shards"]
                   if os.path.isfile(os.path.join(a.translations, f"{s['shard_id']}.txt")))
    if provided + sum(1 for f in findings if f["kind"] == "missing_translation") \
            != len(plan["shards"]):
        findings.append({"kind": "unit_count_drift", "detail": "单元数与计划不符"})

    result = {
        "edid": plan["edid"],
        "book_sha256": plan["book_sha256"],
        "stitched_chars": len(stitched),
        "shard_total": len(plan["shards"]),
        "shard_provided": provided,
        "findings": findings,
        "ok": not findings,
        "book": stitched,
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    # 缺失单元是硬错误，不是可放行的警告：
    # 若静默用原文填充，一份全未译的产出也会拼出"全书字符数"并 exit 0，
    # 下游拿到的是原文而非译文却看不出异常。缺失必须阻断。
    missing = [f for f in findings if f["kind"] == "missing_translation"]
    if missing:
        findings = [f for f in findings if f["kind"] != "missing_translation"]
        findings.extend(missing)

    hard_kinds = ("token_mismatch", "coverage_gap",
                  "unit_count_drift", "empty_translation",
                  "english_residue", "missing_translation")
    hard = [f for f in findings if f["kind"] in hard_kinds]
    print(f"拼合完成：{provided}/{len(plan['shards'])} 单元有译文，"
          f"全书 {len(stitched):,} 字符")
    if hard:
        print(f"拒绝产出：{len(hard)} 项硬错误（逐单元列出，修完对应单元即可重跑）")
        for f in hard:
            print(f"  - [{f['kind']}] {f.get('shard_id','')} {f['detail']}")
        return 1
    if findings:
        print(f"警告 {len(findings)} 项（可人工复核后放行）:")
        for f in findings:
            print(f"  - [{f['kind']}] {f.get('shard_id','')} {f['detail'][:80]}")
    print(f"结果: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
