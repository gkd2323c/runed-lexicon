#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Protect 阶段：行内运行时 token 的掩码、按位还原与多重集校验。

为什么掩码而不是要求模型原样保留
--------------------------------
要求模型「原样保留占位符」把 token 完整性押在模型的概率行为上：模型会漏标、
改标、重排，而这些错误在译文读起来通顺时完全不可见。掩码把每个 token 换成
稳定哨兵符，模型视野里只剩纯文本；还原时按哨兵符索引取回原 token。token
完整性就从模型的自觉变成程序的断言。

掩码不取消校验，只是让校验更便宜：原文标签形态多变（大小写、空格、属性
顺序），逐字符比对脆弱；掩码后译文里只剩稳定哨兵符，比对退化成多重集相等。
还原后仍做多重集严格相等校验；顺序漂移单列并同样按硬错误处理（见 SKILL.md §5）。

不许直接删除 token：删除会连位置与语序信息一起丢掉，模型拿到残缺句，还原时
也无从判断该塞回哪里。

三桶
----
- MASK    行内运行时 token：掩码 -> 译 -> 还原
- EXCLUDE 整条即代码标识符（资源路径 / FormID）：不进模型
- GRAY    灰区（性别 / 语境变量、非引擎方括号标签、非引擎尖括号标签）：
          掩码照做，同时记 gray_reasons 待二审

输入 / 输出 JSON 结构
---------------------
mask --input:
    {"units": [{"id": "813", "source": "Take <Alias=Target>'s sword, %s.\\n"}]}
mask --out:
    {"sentinel_form": "opaque",
     "units": [{"id": "813", "bucket": "MASK",
                "masked": "Take ⟪0⟫'s sword, ⟪1⟫⟪2⟫",
                "tokens": ["<Alias=Target>", "%s", "\\n"],
                "gray_reasons": []}]}
    EXCLUDE 桶的 masked 为 null（不进模型）。

restore --translations:
    {"813": "拿走 ⟪0⟫ 的剑，⟪1⟫⟪2⟫"}
restore --out:
    {"ok": true,
     "units": [{"id": "813", "restored": "拿走 <Alias=Target> 的剑，%s\\n",
                "findings": []}]}

只读：不写 MOD XML。中间产物落 _tmp/。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# ------------------------------------------------------------------ 文法

# 行内运行时 token（bucket A）。type 字母只在 typed 哨兵符里出现。
DEFAULT_INLINE = [
    {"type": "T", "pattern": r"<[^<>\r\n]+>"},
    {"type": "P", "pattern": r"%(?:\d+\$)?[-+#0']*\d*(?:\.\d+)?[diuoxXfFeEgGaAcspn%]"},
    {"type": "B", "pattern": r"\{[A-Za-z0-9_][A-Za-z0-9_.:=|+\- ]*\}"},
    {"type": "K", "pattern": r"\[pagebreak\]"},
    {"type": "E", "pattern": r"\\[nrt\\]"},
    {"type": "N", "pattern": r"\r\n|\n"},
]

# 整条即代码标识符（bucket B）：命中即不进模型。
DEFAULT_EXCLUDE = [
    r"(?i)^[^<>\s]+\.(?:nif|dds|tga|esp|esm|esl|bsa|ba2|pex|psc|wav|xwm|hkx|tri|kf)$",
    r"^[0-9A-Fa-f]{8}$",
    r"^\[[0-9A-Fa-f]{8}\]$",
]

# 已知引擎尖括号标签前缀；不在此列且带 < > 的形态进 GRAY（二审），
# 因为 MOD 自造标签与可译文本在形态上无法机械区分。
KNOWN_ANGLE = re.compile(
    r"(?i)^</?(?:alias|global|mag|dur|area|font|p|br|b|i|u|img|div|span|showkey|reaction)\b"
)

# 灰区（bucket C）：掩码照做，同时标记二审。
DEFAULT_GRAY = [
    {"reason": "gender_or_context_var",
     "pattern": r"(?i)\{[^}]*\b(?:he|she|his|her|him|they|gender|male|female|sir|madam)\b[^}]*\}"},
    {"reason": "non_engine_bracket_tag",
     "pattern": r"\[(?!pagebreak\])[A-Za-z][^\]]*\]"},
]

# 哨兵符还原正则：同时认 ⟪0⟫ / ⟪T0⟫ / ⟪F0⟫（与 long-book-translation 兼容）。
SENT_RE = re.compile(r"⟪([A-Z]?)(\d+)⟫")

HARD_FINDINGS = {"sentinel_multiset_mismatch", "sentinel_reordered", "missing_translation"}


# ------------------------------------------------------------------ 内部

def _sentinel(form: str, typ: str, idx: int) -> str:
    return f"⟪{typ}{idx}⟫" if form == "typed" else f"⟪{idx}⟫"


def _find_hits(text: str, inline: list[dict]) -> list[tuple[int, int, str, str]]:
    """收集全部行内 token，按位置排序并解重叠（先到先得，同起点取更长）。"""
    hits: list[tuple[int, int, str, str]] = []
    for spec in inline:
        rx = re.compile(spec["pattern"])
        for m in rx.finditer(text):
            hits.append((m.start(), m.end(), spec["type"], m.group(0)))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    chosen: list[tuple[int, int, str, str]] = []
    last = -1
    for h in hits:
        if h[0] >= last:
            chosen.append(h)
            last = h[1]
    return chosen


def _gray_reasons(text: str, hits: list[tuple[int, int, str, str]],
                  gray_specs: list[dict]) -> list[str]:
    reasons: set[str] = set()
    for spec in gray_specs:
        if re.search(spec["pattern"], text):
            reasons.add(spec["reason"])
    for _start, _end, typ, tok in hits:
        if typ == "T" and not KNOWN_ANGLE.match(tok):
            reasons.add("non_engine_angle_tag")
            break
    return sorted(reasons)


# ------------------------------------------------------------------ 对外 API

def mask_unit(source: str, form: str = "opaque", inline=None, exclude=None,
              gray_specs=None) -> dict:
    """把一条 Source 分桶并掩码。EXCLUDE 桶 masked 为 None。"""
    inline = DEFAULT_INLINE if inline is None else inline
    exclude = DEFAULT_EXCLUDE if exclude is None else exclude
    gray_specs = DEFAULT_GRAY if gray_specs is None else gray_specs

    for pat in exclude:
        if re.match(pat, source):
            return {"bucket": "EXCLUDE", "masked": None, "tokens": [],
                    "gray_reasons": []}

    hits = _find_hits(source, inline)
    out: list[str] = []
    cursor = 0
    for i, (start, end, typ, _tok) in enumerate(hits):
        out.append(source[cursor:start])
        out.append(_sentinel(form, typ, i))
        cursor = end
    out.append(source[cursor:])
    masked = "".join(out)

    reasons = _gray_reasons(source, hits, gray_specs)
    return {
        "bucket": "GRAY" if reasons else "MASK",
        "masked": masked,
        "tokens": [h[3] for h in hits],
        "gray_reasons": reasons,
    }


def restore_unit(masked: str, translated: str, tokens: list[str]) -> str:
    """按哨兵符索引把译文里的哨兵符换回原 token。越界哨兵符原样保留。"""
    def repl(m: re.Match) -> str:
        n = int(m.group(2))
        return tokens[n] if 0 <= n < len(tokens) else m.group(0)

    return SENT_RE.sub(repl, translated)


def verify_unit(masked: str, translated: str, check_order: bool = False) -> list[dict]:
    """哨兵符多重集严格相等（硬断言）。

    顺序默认不比：翻译会合法地重排句内 token——中文语序常把参数提到别名之前，
    「Kill <Alias=Target> with %s.」译成「用 %s 杀死 <Alias=Target>。」时两个
    哨兵符的顺序必然颠倒。拿顺序相等当硬断言会把一批正确译文整批拒收。
    多重集相等才是「token 没丢、没多、没被改形」的判据。

    check_order=True 供「顺序即语义」的场景（[pagebreak]、成对排版标签）单独启用。
    """
    src = [m.group(0) for m in SENT_RE.finditer(masked)]
    dst = [m.group(0) for m in SENT_RE.finditer(translated)]
    findings: list[dict] = []
    if Counter(src) != Counter(dst):
        findings.append({
            "kind": "sentinel_multiset_mismatch",
            "missing": sorted((Counter(src) - Counter(dst)).elements()),
            "extra": sorted((Counter(dst) - Counter(src)).elements()),
        })
    elif check_order and src != dst:
        findings.append({
            "kind": "sentinel_reordered",
            "source_order": src,
            "translation_order": dst,
        })
    return findings


# ------------------------------------------------------------------ CLI 支撑

def _load(path: str):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _dump(path: str, obj) -> None:
    target = Path(path)
    if target.parent and str(target.parent) != "":
        target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def _grammar(args):
    if not args.grammar:
        return None, None, None
    spec = _load(args.grammar)
    return (spec.get("inline"), spec.get("exclude"), spec.get("gray"))


def cmd_mask(args) -> int:
    doc = _load(args.input)
    units = doc["units"] if isinstance(doc, dict) else doc
    inline, exclude, gray_specs = _grammar(args)

    out = []
    for u in units:
        result = mask_unit(str(u["source"]), args.sentinel_form,
                           inline, exclude, gray_specs)
        out.append({"id": u["id"], **result})

    _dump(args.out, {"sentinel_form": args.sentinel_form, "units": out})
    counts = Counter(item["bucket"] for item in out)
    print(f"掩码完成：{len(out)} 条 -> "
          f"MASK {counts.get('MASK', 0)} / "
          f"EXCLUDE {counts.get('EXCLUDE', 0)} / "
          f"GRAY {counts.get('GRAY', 0)}")
    return 0


def cmd_restore(args) -> int:
    masked_doc = _load(args.masked)
    translations = _load(args.translations)

    out = []
    hard: list[dict] = []
    for u in masked_doc["units"]:
        uid = u["id"]
        masked = u.get("masked")
        if masked is None:  # EXCLUDE：不进模型，本就无译文
            out.append({"id": uid, "restored": None, "findings": []})
            continue
        if uid not in translations:
            finding = {"kind": "missing_translation"}
            out.append({"id": uid, "restored": None, "findings": [finding]})
            hard.append({"id": uid, **finding})
            continue

        translated = str(translations[uid])
        findings = verify_unit(masked, translated, args.check_order)
        restored = restore_unit(masked, translated, u.get("tokens", []))
        out.append({"id": uid, "restored": restored, "findings": findings})
        for f in findings:
            hard.append({"id": uid, **f})

    result = {"ok": not hard, "units": out}
    _dump(args.out, result)
    if args.report:
        _dump(args.report, {"ok": not hard, "findings": hard})

    if hard:
        print(f"拒绝产出：{len(hard)} 项硬错误（逐条列出，修完对应条目重跑即可）")
        for f in hard:
            detail = f.get("missing") or f.get("extra") or ''
            print(f"  - [{f['kind']}] {f['id']} {detail}")
        return 1
    if args.check_order:
        print(f"还原完成：{len(out)} 条，哨兵符多重集与顺序均守恒")
    else:
        print(f"还原完成：{len(out)} 条，哨兵符多重集守恒"
              f"（顺序未检查——翻译允许重排句内 token）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Protect 阶段：行内 token 掩码 / 还原 / 校验")
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mask", help="把 Source 掩码成哨兵符文本")
    m.add_argument("--input", required=True, help="含 units[{id,source}] 的 JSON")
    m.add_argument("--out", required=True, help="掩码结果 JSON")
    m.add_argument("--sentinel-form", choices=["opaque", "typed"], default="opaque")
    m.add_argument("--grammar", help="覆盖默认文法的 JSON（inline/exclude/gray）")
    m.set_defaults(func=cmd_mask)

    r = sub.add_parser("restore", help="把带哨兵符的译文还原并校验")
    r.add_argument("--masked", required=True, help="mask 的输出 JSON")
    r.add_argument("--translations", required=True, help="{id: 译文} 的 JSON")
    r.add_argument("--out", required=True, help="还原结果 JSON")
    r.add_argument("--report", help="可选：findings 报告 JSON")
    r.add_argument("--check-order", action="store_true",
                   help="把哨兵符顺序漂移也判为硬错误（排版标记场景；句内 token 默认可重排）")
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
