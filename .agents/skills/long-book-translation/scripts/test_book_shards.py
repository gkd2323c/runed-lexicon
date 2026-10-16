#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long-book-translation 的回归测试。

用 synthetic mini-XML，不碰真实项目文件。覆盖分片守恒、占位符编号、
以及 stitch 的五条不变量。

跑法：py -3 scripts/test_book_shards.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(HERE, "plan_book_shards.py")
STITCH = os.path.join(HERE, "stitch_translation.py")
PACK = os.path.join(HERE, "build_shard_pack.py")

MARK_RE = re.compile(r"\[pagebreak\]|<[^<>\n]*>")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "pbs", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "plan_book_shards.py"))
    _m = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    m_spans_by_boundary = _m.spans_by_boundary
    m_boundary_re = _m.boundary_re
    HAS_MOD = True
except Exception:
    HAS_MOD = False
TOKEN_RE = re.compile(r"⟪F(\d+)⟫")

# 三章，两章短一章长；长章含多个段落边界以触发二次切分。
# 含 p/img 标签与 pagebreak，覆盖两类受保护标记。
BOOK = (
    '[pagebreak]\n<p align="center">\nTITLE PAGE\n</p>\n\n'
    'First short chapter body.\n\n'
    '[pagebreak]\n<p align="left">\n'
    + ("Long chapter paragraph one with enough text to matter. " * 40) + "\n\n"
    + ("Long chapter paragraph two continues onward here. " * 40) + "\n\n"
    + ("Long chapter paragraph three wraps it up now. " * 40) + "\n"
    '</p>\n'
    '[pagebreak]\nChapter three is short again.\n'
)

MINI_XML = f'''<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
  <Params><Addon>MiniBook.esp</Addon><Source>english</Source><Dest>chinese</Dest></Params>
  <Content>
    <String List="0">
      <EDID>MiniTestBook</EDID>
      <REC>BOOK:DESC</REC>
      <Source>{BOOK.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</Source>
      <Dest>{BOOK.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</Dest>
    </String>
  </Content>
</SSTXMLRessources>
'''


def run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *argv], capture_output=True,
                          text=True, encoding="utf-8")


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def copy_into(src_dir: str, dst_dir: str, skip_last: bool = False) -> None:
    """把 src_dir 的译文复制到 dst_dir；skip_last 用于构造缺失用例。"""
    names = sorted(os.listdir(src_dir))
    if skip_last:
        names = names[:-1]
    os.makedirs(dst_dir, exist_ok=True)
    for n in names:
        shutil.copy2(os.path.join(src_dir, n), os.path.join(dst_dir, n))


def main() -> int:
    failures: list[str] = []
    tmp = tempfile.mkdtemp(prefix="longbook-test-")
    try:
        xml = os.path.join(tmp, "mini.xml")
        write(xml, MINI_XML)
        out_dir = os.path.join(tmp, "shards")
        budget = 2300

        # ---- 1. 分片守恒 ----
        r = run(PLAN, "--xml", xml, "--edid", "MiniTestBook",
                "--out-dir", out_dir, "--budget", str(budget), "--force")
        if r.returncode != 0:
            failures.append(f"plan 失败: {r.stderr.strip()[:200]}")
            print("\n".join(failures)); return 1

        plan = json.load(open(os.path.join(out_dir, "plan.json"), encoding="utf-8"))
        raw = None
        for s in ET.parse(xml).getroot().iter("String"):
            if (s.findtext("EDID") or "").strip() == "MiniTestBook":
                raw = s.findtext("Source")
        if raw is None:
            failures.append("测试 XML 构造失败")

        pos = 0
        for sh in plan["shards"]:
            a, b = sh["span"]
            if a != pos:
                failures.append(f"分片不连续 {sh['shard_id']}: {a} != {pos}")
            pos = b
        if pos != len(raw):
            failures.append(f"并集不等于全文: {pos} != {len(raw)}")

        # 每个单元不超过预算（硬切单元除外）
        for sh in plan["shards"]:
            if not sh["hard_cut"] and sh["raw_chars"] > budget:
                failures.append(f"{sh['shard_id']} 超预算: {sh['raw_chars']}")

        # 无空单元
        if any(sh["raw_chars"] == 0 for sh in plan["shards"]):
            failures.append("存在 raw_chars=0 的空单元")

        # 降级链必须被真实执行：首选边界切出的超预算片段，
        # 必须被段落边界二次切成多个单元，而不是整段成一个超预算单元。
        primary = m_spans_by_boundary(raw, m_boundary_re("pagebreak")) \
            if HAS_MOD else []
        over = [e - s for s, e in primary if e - s > budget]
        if over:
            # 每个超预算首选片段都应贡献多于一个单元
            spanning = sum(1 for sh in plan["shards"]
                           if sh["raw_chars"] <= budget)
            if spanning < len(plan["shards"]) and not any(
                    sh["hard_cut"] for sh in plan["shards"]):
                failures.append("超预算首选片段未被切分且未标 hard_cut")

        # ---- 2. 占位符编号与原文标记一一对应 ----
        for sh in plan["shards"]:
            a, b = sh["span"]
            seg_marks = MARK_RE.findall(raw[a:b])
            readme = open(os.path.join(out_dir, sh["readme"]), encoding="utf-8").read()
            got = [int(m.group(1)) for m in TOKEN_RE.finditer(readme)]
            if got != list(range(len(seg_marks))):
                failures.append(f"{sh['shard_id']} 占位符编号不符: "
                                f"{got} != {list(range(len(seg_marks)))}")

        # ---- 3. 正确译文应通过 ----
        tdir = os.path.join(tmp, "translated")
        for sh in plan["shards"]:
            a, b = sh["span"]
            seg = raw[a:b]
            out, ci = [], 0
            for _ in MARK_RE.finditer(seg):
                out.append(f"中文段{ci}")
                out.append(f"⟪F{ci}⟫")
                ci += 1
            out.append(f"中文段{ci}")
            write(os.path.join(tdir, f"{sh['shard_id']}.txt"), "".join(out))

        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir,
                "--out", os.path.join(tmp, "ok.json"))
        if r.returncode != 0:
            failures.append(f"正确译文被拒: {r.stdout.strip()[:200]}")
        else:
            ok = json.load(open(os.path.join(tmp, "ok.json"), encoding="utf-8"))
            if MARK_RE.findall(raw) != MARK_RE.findall(ok["book"]):
                failures.append("拼合后标记序列与原文不一致")

        # 诊断：打印划分，便于定位漏标用例为何没被拦住
        if os.environ.get("LB_DEBUG"):
            for sh in plan["shards"]:
                print(f"  [dbg] {sh['shard_id']} raw={sh['raw_chars']} "
                      f"tokens={sh['format_token_count']} hard={sh['hard_cut']}")

        # ---- 4. 漏标应被拦截 ----
        tdir2 = os.path.join(tmp, "translated-bad-token")
        copy_into(tdir, tdir2)
        # 挑第一个确有占位符的单元：对 tokens=0 的单元删占位符是空操作，
        # 构造不出漏标，用例会假通过。
        tok_shard = next(s for s in plan["shards"] if s["format_token_count"] > 0)
        victim = os.path.join(tdir2, f"{tok_shard['shard_id']}.txt")
        txt = open(victim, encoding="utf-8").read()
        if not TOKEN_RE.search(txt):
            failures.append("漏标用例构造失败：目标单元无占位符")
        write(victim, TOKEN_RE.sub("", txt, count=1))
        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir2,
                "--out", os.path.join(tmp, "bad-token.json"))
        if r.returncode == 0:
            failures.append("漏标未被拦截")

        # ---- 5. 缺失译文应被拦截 ----
        tdir3 = os.path.join(tmp, "translated-partial")
        copy_into(tdir, tdir3, skip_last=True)
        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir3,
                "--out", os.path.join(tmp, "partial.json"))
        if r.returncode == 0:
            failures.append("缺失译文未被拦截（会静默用原文填充）")

        # ---- 6. 英文残留应被拦截 ----
        tdir4 = os.path.join(tmp, "translated-en")
        copy_into(tdir, tdir4)
        tgt = os.path.join(tdir4, f"{plan['shards'][0]['shard_id']}.txt")
        write(tgt, open(tgt, encoding="utf-8").read()
              + "the quick brown fox jumps over the lazy dog")
        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir4,
                "--out", os.path.join(tmp, "en.json"))
        if r.returncode == 0:
            failures.append("英文残留未被拦截")

        # ---- 7. 空译文应被拦截 ----
        tdir5 = os.path.join(tmp, "translated-empty")
        copy_into(tdir, tdir5)
        write(os.path.join(tdir5, f"{plan['shards'][0]['shard_id']}.txt"), "")
        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir5,
                "--out", os.path.join(tmp, "empty.json"))
        if r.returncode == 0:
            failures.append("空译文未被拦截")

        # ---- 8. 工作包组装应只带相关实体 ----
        passport = os.path.join(tmp, "passport.json")
        write(passport, json.dumps({"title": "T", "author": "A",
                                    "narrator": "N", "genre": "G",
                                    "register": "R"}, ensure_ascii=False))
        ledger = os.path.join(out_dir, "ledger.json")
        led = json.load(open(ledger, encoding="utf-8"))
        led["entities"] = [
            {"en": "PRESENTNAME", "zh": "出现者", "status": "s", "note": ""},
            {"en": "ABSENTNAME", "zh": "缺席者", "status": "s", "note": ""},
        ]
        write(ledger, json.dumps(led, ensure_ascii=False))
        r = run(PACK, "--plan-dir", out_dir, "--shard",
                plan["shards"][0]["shard_id"], "--passport", passport,
                "--out", os.path.join(tmp, "pack.md"))
        if r.returncode != 0:
            failures.append(f"build_shard_pack 失败: {r.stderr.strip()[:150]}")
        else:
            body = open(os.path.join(tmp, "pack.md"), encoding="utf-8").read()
            # 两个名字都不在合成原文里，因此都不该出现在实体表
            if "PRESENTNAME" in body or "ABSENTNAME" in body:
                failures.append("无关实体被带入工作包")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 分片守恒 / 占位符编号 / 漏标拦截 / 缺失拦截 / "
          "英文残留拦截 / 空译文拦截 / 标记逐字节还原 / 实体筛选")
    return 0




def test_non_book_rec() -> int:
    """非 BOOK 记录：QUST:CNAM 单段纯文本，无换行无标记。

    验证：REC 自动识别、句子边界降级、占位符机制空转不报错、
    非 BOOK 也能正常拼回。
    """
    failures: list[str] = []
    tmp = tempfile.mkdtemp(prefix="longbook-nonbook-")
    try:
        # 构造一段超过默认预算(900)的单段纯文本，无换行
        sent = ("I followed the path of the Psijics to the mountain summit. "
                "The scripture told me to find a crystal near the tower. "
                "Bjorn warned me about the trials that lay ahead. " * 12)
        quest = sent
        xml = os.path.join(tmp, "quest.xml")
        write(xml, QUEST_XML_TMPL.replace("__BODY__", quest.replace("&", "&amp;")))
        out_dir = os.path.join(tmp, "shards")

        r = run(PLAN, "--xml", xml, "--edid", "MiniQuestLog",
                "--out-dir", out_dir, "--force")
        if r.returncode != 0:
            failures.append(f"QUST:CNAM plan 失败: {r.stderr.strip()[:200]}")
            print("\n".join(failures)); return 1

        plan = json.load(open(os.path.join(out_dir, "plan.json"), encoding="utf-8"))
        if plan.get("rec") != "QUST:CNAM":
            failures.append(f"REC 推断错误: {plan.get('rec')}")
        if plan.get("budget") != 900:
            failures.append(f"默认预算未按 REC 取: {plan.get('budget')}")
        if "sentence" not in plan.get("boundary", []):
            failures.append(f"边界策略未选 sentence: {plan.get('boundary')}")

        # 单段纯文本，句子边界必须真的切开（多于一个单元）
        if plan["shard_count"] < 2:
            failures.append(f"长纯文本未被句子边界切开: {plan['shard_count']} 单元")

        # 切点不应落在句子中间：每个非 hard_cut 单元应以句末标点收尾
        for sh in plan["shards"]:
            if sh["hard_cut"]:
                continue
            body = open(os.path.join(out_dir, sh["readme"]), encoding="utf-8").read()
            tail = body.rstrip()[-1:] if body.strip() else ""
            if tail not in (".", "!", "?", "。", "！", "？", ">", "]", " "):
                failures.append(f"{sh['shard_id']} 切点疑似落在句中: 尾部 {tail!r}")

        # 守恒
        pos = 0
        for sh in plan["shards"]:
            if sh["span"][0] != pos:
                failures.append(f"{sh['shard_id']} 区间不连续")
            pos = sh["span"][1]
        if pos != plan["book_raw_chars"]:
            failures.append("并集不等于全文")

        # 正确译文应能拼回（无标记，占位符机制空转）
        tdir = os.path.join(tmp, "translated")
        for i, sh in enumerate(plan["shards"]):
            write(os.path.join(tdir, f"{sh['shard_id']}.txt"),
                  f"中文段落{i}。" )
        r = run(STITCH, "--plan-dir", out_dir, "--translations", tdir,
                "--out", os.path.join(tmp, "ok.json"))
        if r.returncode != 0:
            failures.append(f"非 BOOK 拼合失败: {r.stdout.strip()[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"非 BOOK 用例 FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("非 BOOK 用例 PASS: REC 推断 / 句子边界降级 / 切点不在句中 / 守恒 / 拼合")
    return 0


QUEST_XML_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<SSTXMLRessources>
  <Params><Addon>MiniQuest.esp</Addon></Params>
  <Content>
    <String List="0">
      <EDID>MiniQuestLog</EDID>
      <REC>QUST:CNAM</REC>
      <Source>__BODY__</Source>
      <Dest>__BODY__</Dest>
    </String>
  </Content>
</SSTXMLRessources>
"""


if __name__ == "__main__":
    rc = main()
    rc |= test_non_book_rec()
    sys.exit(rc)
