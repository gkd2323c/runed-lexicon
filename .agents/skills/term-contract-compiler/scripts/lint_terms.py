# -*- coding: utf-8 -*-
"""lint_terms.py — 词表机械 lint（确定性，无语义判断）。

给术语数据做纯机械体检：LLM 管「约根该怎么译」，本工具管「引号有没有长歪」。
检查对象为机器侧词表（非人类文档）：
  - terms.json（MOD 词表，canonical 机器源）
  - global-forbidden-words.json（项目全局禁用词库）

检查项：
  FAIL（确定性错误，可硬拦）：
    - 不可见/控制字符：零宽空格/连接符、BOM、软连字符、C0/C1 控制符、行/段分隔符
    - 引号/括号不配对（栈式）：未闭合、多余闭合、错序 —— “” ‘’ （） 【】 《》 〔〕 ［］ ｛｝
    - 直角引号「」『』〈〉（项目禁用形态；译文层 CHAR001 已拦，词表源头同步拦）
    - 半角双引号 " 紧邻中文（中文语境应用弯引号）
    - 繁体/异体字混入（opencc 或 zhconv 可用时；纯确定性字形检查）
    - 同文件 english 重复；forbidden 含空项或与自身目标形相同（自禁）
  WARN（可疑，不拦，人工看）：
    - 半角单引号 ' 紧邻中文（音译撇号可能合法，如 杰'扎格）
    - 全半角标点混用（中文紧邻半角 ,.;:!? 或半角括号）
    - zh / target 字段含连续拉丁字母
    - 说明字段（note / reason）中的繁体与 NBSP（宽松级别）

用法：
  py -3 lint_terms.py --terms mods/<plugin>/terms.json --bans global-forbidden-words.json
  py -3 lint_terms.py --terms mods/<plugin>/terms.json --json <report.json> [--quiet]

退出码：存在 FAIL → 1；仅 WARN 或无问题 → 0。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 可选繁简依赖
try:  # opencc 字表最全；无则 zhconv；再无则跳过繁简检查（不误报）
    from opencc import OpenCC as _OpenCC

    _T2S = _OpenCC("t2s")

    def to_simplified(ch: str) -> str:
        return _T2S.convert(ch)
except ImportError:
    try:
        import zhconv

        def to_simplified(ch: str) -> str:
            return zhconv.convert(ch, "zh-cn")
    except ImportError:
        def to_simplified(ch: str) -> str:  # type: ignore[misc]
            return ch

# ---------------------------------------------------------------- 字符集
CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_RE = re.compile(f"[{CJK_RANGE}]")

INVISIBLE_MAP = {
    "\u200b": "U+200B 零宽空格",
    "\u200c": "U+200C 零宽非连接符",
    "\u200d": "U+200D 零宽连接符",
    "\u2060": "U+2060 词连接符",
    "\ufeff": "U+FEFF BOM/零宽不换行空格",
    "\u00ad": "U+00AD 软连字符",
    "\u2028": "U+2028 行分隔符",
    "\u2029": "U+2029 段分隔符",
    "\u00a0": "U+00A0 不换行空格",
}
FULLWIDTH_SPACE = "\u3000"
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

PAIR_OPEN = {"\u201c": "\u201d", "\u2018": "\u2019",
             "\uff08": "\uff09", "\u3010": "\u3011", "\u300a": "\u300b",
             "\u3014": "\u3015", "\uff3b": "\uff3d", "\uff5b": "\uff5d",
             "\u300c": "\u300d", "\u300e": "\u300f", "\u3008": "\u3009"}
PAIR_CLOSE = {v: k for k, v in PAIR_OPEN.items()}
CORNER_QUOTES = "\u300c\u300d\u300e\u300f\u3008\u3009"  # 「」『』〈〉

RE_ASCII_DQ_CJK = re.compile(f"(?<=[{CJK_RANGE}])\"|\"(?=[{CJK_RANGE}])")
RE_ASCII_SQ_CJK = re.compile(f"(?<=[{CJK_RANGE}])'|'(?=[{CJK_RANGE}])")
RE_HALF_PUNCT_CJK = re.compile(
    f"(?<=[{CJK_RANGE}])[,.;:!?]|[,.;:!?](?=[{CJK_RANGE}])")
RE_HALF_PAREN_CJK = re.compile(f"(?<=[{CJK_RANGE}])[()]|[()](?=[{CJK_RANGE}])")
RE_LATIN_SEQ = re.compile(r"[A-Za-z]{2,}")

DESC_LABELS = {"\u201c": "左引号", "\u201d": "右引号", "\u2018": "左单引号",
               "\u2019": "右单引号", "\uff08": "左括号", "\uff09": "右括号",
               "\u3010": "左方括号", "\u3011": "右方括号", "\u300a": "左书名号",
               "\u300b": "右书名号", "\u3014": "左六角括号", "\u3015": "右六角括号",
               "\uff3b": "左方括号", "\uff3d": "右方括号", "\uff5b": "左花括号",
               "\uff5d": "右花括号", "\u300c": "左直角引号", "\u300d": "右直角引号",
               "\u300e": "左双直角引号", "\u300f": "右双直角引号",
               "\u3008": "左尖括号", "\u3009": "右尖括号"}


def _ctx(s: str, pos: int, width: int = 14) -> str:
    lo = max(0, pos - width)
    hi = min(len(s), pos + width + 1)
    raw = s[lo:hi]
    shown = "".join(
        INVISIBLE_MAP.get(c) or (f"\\u{ord(c):04X}" if ord(c) < 0x20 else c)
        for c in raw)
    return f"…{shown}…" if (lo > 0 or hi < len(s)) else shown


def check_pairs(s: str):
    """栈式配对检查（含直角引号对；直角引号的「禁用」由 CORNER_QUOTE 单独报告）。
    返回 [(code, pos, detail)]。"""
    out = []
    stack = []  # (open_char, pos)
    for pos, ch in enumerate(s):
        if ch in PAIR_OPEN:
            stack.append((ch, pos))
        elif ch in PAIR_CLOSE:
            want = PAIR_CLOSE[ch]
            if stack and stack[-1][0] == want:
                stack.pop()
            elif stack:
                prev_ch, prev_pos = stack.pop()
                out.append(("QUOTE_ORDER", pos,
                            f"关闭符“{DESC_LABELS.get(ch, ch)}”与最近的未闭合"
                            f"“{DESC_LABELS.get(prev_ch, prev_ch)}”（位置 {prev_pos}）不匹配"))
            else:
                out.append(("QUOTE_UNPAIRED", pos,
                            f"多余的关闭符“{DESC_LABELS.get(ch, ch)}”"))
    for ch, pos in stack:
        out.append(("QUOTE_UNPAIRED", pos,
                    f"未闭合的“{DESC_LABELS.get(ch, ch)}”"))
    return out


def check_traditional(s: str):
    """返回 [(pos, char, simplified)]；依赖可用时才有结果。"""
    out = []
    for pos, ch in enumerate(s):
        o = ord(ch)
        if 0x3400 <= o <= 0x9fff or 0xf900 <= o <= 0xfaff:
            simp = to_simplified(ch)
            if simp != ch:
                out.append((pos, ch, simp))
    return out


def scan_string(s: str, *, strict: bool, field: str, entry: str, file: str,
                check_visible: bool = True):
    """对一个字符串字段做全量机械检查。strict=True 为译文形态字段（zh / forbidden）。"""
    issues = []

    def add(severity, code, detail, pos=None, width=14):
        issues.append({
            "severity": severity, "file": file, "entry": entry, "field": field,
            "code": code, "detail": detail,
            "context": _ctx(s, pos if pos is not None else 0, width) if pos is not None else None,
        })

    # 1) 不可见 / 控制字符（所有字段都查，含 english）
    for pos, ch in enumerate(s):
        if ch in INVISIBLE_MAP:
            if ch == "\u00a0" and not strict:
                add("warn", "INVISIBLE", f"含 {INVISIBLE_MAP[ch]}", pos)
            else:
                add("fail", "INVISIBLE", f"含 {INVISIBLE_MAP[ch]}", pos)
        elif ch == FULLWIDTH_SPACE:
            add("warn", "INVISIBLE", "含全角空格 U+3000", pos)
    for m in CTRL_RE.finditer(s):
        add("fail", "CONTROL_CHAR", f"含控制字符 U+{ord(m.group()):04X}", m.start())

    if not check_visible:
        return issues
    if not strict and field in ("note", "reason", "notes"):
        # 说明字段：note 里「」为既有术语标记惯例，出现本身不报；配对错照报
        for code, pos, detail in check_pairs(s):
            add("fail", code, detail, pos)
        for pos, ch, simp in check_traditional(s):
            add("warn", "TRADITIONAL", f"繁体/异体字“{ch}”应作“{simp}”", pos)
        for m in RE_ASCII_DQ_CJK.finditer(s):
            add("warn", "ASCII_QUOTE_CJK", "半角双引号紧邻中文", m.start())
        for m in RE_HALF_PUNCT_CJK.finditer(s):
            add("warn", "HALFWIDTH_PUNCT", "半角标点紧邻中文", m.start())
        return issues

    # 2) 引号/括号配对
    for code, pos, detail in check_pairs(s):
        add("fail", code, detail, pos)

    # 3) 直角引号（项目禁用形态）
    for pos, ch in enumerate(s):
        if ch in CORNER_QUOTES:
            add("fail", "CORNER_QUOTE", f"含直角引号“{ch}”（项目禁用形态，应用弯引号）", pos)

    # 4) 半角引号贴中文
    for m in RE_ASCII_DQ_CJK.finditer(s):
        add("fail", "ASCII_QUOTE_CJK", "半角双引号紧邻中文", m.start())
    for m in RE_ASCII_SQ_CJK.finditer(s):
        add("warn", "ASCII_APOSTROPHE_CJK", "半角单引号紧邻中文（音译撇号可能合法）", m.start())

    # 5) 繁简混杂（目标形 zh/target 必须纯简体：FAIL；坏形列表条目可能为刻意防护形：WARN）
    for pos, ch, simp in check_traditional(s):
        if strict and field in ("zh", "target"):
            add("fail", "TRADITIONAL", f"繁体/异体字“{ch}”应作“{simp}”", pos)
        else:
            add("warn", "TRADITIONAL",
                f"繁体/异体字“{ch}”应作“{simp}”（坏形列表条目可保留为刻意防护形）", pos)

    # 6) 全半角混用
    for m in RE_HALF_PUNCT_CJK.finditer(s):
        add("warn", "HALFWIDTH_PUNCT", "半角标点紧邻中文", m.start())
    for m in RE_HALF_PAREN_CJK.finditer(s):
        add("warn", "HALFWIDTH_PAREN", "半角括号紧邻中文", m.start())

    # 7) zh 字段含拉丁字母
    if strict and field in ("zh", "target"):
        for m in RE_LATIN_SEQ.finditer(s):
            add("warn", "LATIN_IN_ZH", f"含拉丁字母序列 {m.group()!r}", m.start())

    return issues


def lint_terms_doc(data: dict, path: str):
    issues = []
    entries = data.get("terms") or []
    seen = {}
    for i, t in enumerate(entries):
        label = (t.get("english") or f"#{i}").strip()
        seen.setdefault(label.lower(), []).append(i)
        issues += scan_string(t.get("english") or "", strict=True, field="english",
                              entry=label, file=path, check_visible=False)
        for fname, strict in (("zh", True), ("note", False), ("notes", False)):
            val = t.get(fname)
            if isinstance(val, str) and val:
                issues += scan_string(val, strict=strict, field=fname,
                                      entry=label, file=path)
        forbidden = t.get("forbidden") or []
        for j, item in enumerate(forbidden):
            if not isinstance(item, str):
                continue
            if not item.strip():
                issues.append({"severity": "fail", "file": path, "entry": label,
                               "field": f"forbidden[{j}]", "code": "FORBIDDEN_EMPTY",
                               "detail": "forbidden 含空项", "context": None})
            elif item == (t.get("zh") or ""):
                issues.append({"severity": "fail", "file": path, "entry": label,
                               "field": f"forbidden[{j}]", "code": "FORBIDDEN_SELF",
                               "detail": f"坏形与目标形相同（自禁）：{item!r}",
                               "context": None})
            else:
                issues += scan_string(item, strict=True, field=f"forbidden[{j}]",
                                      entry=label, file=path)
        for fname in ("additional_accepted",):
            for j, item in enumerate(t.get(fname) or []):
                if isinstance(item, str) and item:
                    issues += scan_string(item, strict=True, field=f"{fname}[{j}]",
                                          entry=label, file=path)
    for label, idxs in seen.items():
        if len(idxs) > 1:
            issues.append({"severity": "fail", "file": path,
                           "entry": label or f"#{idxs[0]}", "field": "-",
                           "code": "DUP_ENGLISH",
                           "detail": f"english 重复 {len(idxs)} 次（索引 {idxs}）",
                           "context": None})
    return issues


def lint_bans_doc(data: dict, path: str):
    issues = []
    for i, b in enumerate(data.get("bans") or []):
        label = (b.get("english") or f"#{i}").strip()
        issues += scan_string(b.get("english") or "", strict=False, field="english",
                              entry=label, file=path, check_visible=False)
        for fname, strict in (("target", True), ("reason", False)):
            val = b.get(fname)
            if isinstance(val, str) and val:
                issues += scan_string(val, strict=strict, field=fname,
                                      entry=label, file=path)
        for j, item in enumerate(b.get("forbidden") or []):
            if not isinstance(item, str):
                continue
            if not item.strip():
                issues.append({"severity": "fail", "file": path, "entry": label,
                               "field": f"forbidden[{j}]", "code": "FORBIDDEN_EMPTY",
                               "detail": "forbidden 含空项", "context": None})
            elif item == (b.get("target") or ""):
                issues.append({"severity": "fail", "file": path, "entry": label,
                               "field": f"forbidden[{j}]", "code": "FORBIDDEN_SELF",
                               "detail": f"坏形与目标形相同（自禁）：{item!r}",
                               "context": None})
            else:
                issues += scan_string(item, strict=True, field=f"forbidden[{j}]",
                                      entry=label, file=path)
    for i, k in enumerate(data.get("keep") or []):
        label = (k.get("english") if isinstance(k, dict) else str(k)) or f"keep#{i}"
        if isinstance(k, dict):
            for fname in ("english", "target", "reason"):
                val = k.get(fname)
                if isinstance(val, str) and val:
                    issues += scan_string(val, strict=False, field=fname,
                                          entry=label, file=path,
                                          check_visible=True)
    return issues


def lint_path(kind: str, path: str):
    p = Path(path)
    if not p.is_file():
        return [{"severity": "fail", "file": path, "entry": "-", "field": "-",
                 "code": "FILE_MISSING", "detail": f"文件不存在: {path}", "context": None}]
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [{"severity": "fail", "file": path, "entry": "-", "field": "-",
                 "code": "JSON_INVALID", "detail": f"JSON 解析失败: {exc}", "context": None}]
    if kind == "terms":
        return lint_terms_doc(data, path)
    if kind == "bans":
        return lint_bans_doc(data, path)
    raise ValueError(f"unknown kind: {kind}")


def lint_targets(targets, *, quiet: bool = False):
    issues = []
    for kind, path in targets:
        issues += lint_path(kind, path)
    fails = [x for x in issues if x["severity"] == "fail"]
    warns = [x for x in issues if x["severity"] == "warn"]

    if not quiet:
        print("== 词表机械 lint ==")
        cur_file = None
        for x in issues:
            if x["file"] != cur_file:
                cur_file = x["file"]
                print(f"-- {cur_file}")
            sev = "FAIL" if x["severity"] == "fail" else "warn"
            print(f"  [{sev}] [{x['entry']}].{x['field']} {x['code']}: {x['detail']}")
            if x.get("context"):
                print(f"        上下文: {x['context']}")
        print()
    return fails, warns, issues


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="词表机械 lint（确定性检查）")
    ap.add_argument("--terms", action="append", default=[], metavar="PATH",
                    help="terms.json 路径（可重复）")
    ap.add_argument("--bans", action="append", default=[], metavar="PATH",
                    help="global-forbidden-words.json 路径（可重复）")
    ap.add_argument("--json", default=None, metavar="PATH", help="写出 JSON 报告")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = ap.parse_args()

    targets = [("terms", p) for p in args.terms] + [("bans", p) for p in args.bans]
    if not targets:
        print("error: 至少需要一个 --terms 或 --bans", file=sys.stderr)
        return 2

    fails, warns, issues = lint_targets(targets, quiet=args.quiet)
    print(f"fail={len(fails)} warn={len(warns)}（检查 {len(targets)} 个文件）")
    if args.json:
        report = {"schema_version": 1,
                  "targets": [{"kind": k, "path": p} for k, p in targets],
                  "counts": {"fail": len(fails), "warn": len(warns)},
                  "issues": issues}
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report -> {args.json}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
