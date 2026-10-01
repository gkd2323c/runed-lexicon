#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启发式专名开集发现（seed scan）。

为什么需要它：`noun_consistency_scan.py` 与 `dictionary-noun-audit.py` 都是**闭集**
扫描——只覆盖已登记词与已定义模式，**规则零命中不等于收敛**。AGENTS.md 要求声明
「名词/实体收敛」必须同时完成规则扫描 + 启发式种子扩散（分块找专名种子 → 同英文
锚全文扩散 → 中文形态归组 → 裁决）。本脚本就是那条链路里缺失的「种子 + 扩散 + 归组」。

判据（无对齐假设）：对每个英文专名种子，取其全部出现行的译文，枚举出现的 CJK 串
作为候选中文形；若这些串能把出现行**互斥地**分成 ≥2 组（每组内某形稳定出现、且该形
在别组完全不出现），即判为同锚多形候选。这样不需要猜「英文第几个词对应中文第几块」——
短句上位置对齐极易错位（两句式台词里第二个专名会映射到不存在的块号），而互斥覆盖
只看共现关系，结论可解释、可复核。

**本脚本只报候选，不下结论**（发现器不带白名单、不预知正确答案）。输出需经主会话
或独立审查裁决后才能改译文或落词表。

用法：
  py -3 seed_scan.py --stem <stem> [--min-occurrences 3] [--out <json>] [--limit N]
  py -3 seed_scan.py --xml <translated.xml> --registered <inventory.json> ...

输出角色：`.work/<stem>/reports/<stem>-seed-scan.json`
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

# 种子首词若属于这些词，即使首字母大写也不是专名（句首普通词、疑问/情态/介词等）。
# 该检查对**所有**种子生效（不限单词形）：`Against Mora` / `Can I` 这类
# 组合的首词是虚词，整条应丢弃。
FUNCTION_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "along", "already",
    "also", "although", "always", "am", "among", "an", "and", "another", "any",
    "anyone", "anything", "are", "around", "as", "at", "away", "back", "be",
    "because", "been", "before", "being", "below", "beside", "best", "better",
    "between", "both", "but", "by", "call", "came", "can", "cannot", "could",
    "did", "do", "does", "doing", "done", "down", "during", "each", "either",
    "else", "enough", "even", "ever", "every", "everyone", "everything", "few",
    "for", "from", "get", "gets", "give", "given", "go", "going", "gone", "good",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "his", "how", "however", "i", "if", "in", "indeed", "inside", "instead",
    "into", "is", "it", "its", "itself", "just", "keep", "kept", "know", "less",
    "let", "like", "little", "look", "made", "make", "makes", "many", "may",
    "maybe", "me", "might", "mine", "more", "most", "much", "must", "my",
    "myself", "near", "need", "neither", "never", "next", "no", "nor", "not",
    "nothing", "now", "of", "off", "on", "once", "one", "only", "or", "other",
    "others", "our", "ours", "out", "outside", "over", "own", "perhaps", "put",
    "quite", "rather", "see", "seem", "seems", "several", "shall", "she",
    "should", "since", "so", "some", "someone", "something", "still", "such",
    "take", "taken", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "therefore", "these", "they", "thing",
    "things", "think", "this", "those", "though", "three", "through", "thus",
    "till", "to", "together", "too", "toward", "towards", "two", "under",
    "until", "up", "upon", "us", "use", "very", "want", "was", "way", "we",
    "well", "were", "what", "when", "where", "whether", "which", "while", "who",
    "whom", "whose", "why", "will", "with", "within", "without", "would", "yes",
    "yet", "you", "your", "yours", "yourself",
}

# 专名内部允许的小写连接词（Sigmar of Dawn / Sons of Hinnom）
_CONNECTORS = {"of", "the", "de", "di", "da", "von", "van", "der", "den", "la",
               "le", "du", "des", "el", "bin", "ibn", "y", "and", "for", "in"}

NAME_TOKEN = r"[A-Z][A-Za-z'’\-]*"
_CONNECTOR_ALT = "|".join(sorted(_CONNECTORS))
# 连续的专名 token，允许中间夹**一串**小写连接词（Order of the Blue），
# 或直接相邻的另一个大写 token；整体允许尾部所有格 's
SEED_RE = re.compile(
    rf"{NAME_TOKEN}"
    rf"(?:\s+(?:{_CONNECTOR_ALT})(?:\s+(?:{_CONNECTOR_ALT}))*\s+{NAME_TOKEN}"
    rf"|\s+{NAME_TOKEN})*(?:'s)?"
)
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
# 候选中文形：从译文的 CJK 连续块里枚举 2~5 字 n-gram。
# 不用「最大连续块」——一句里「子爵伊斯加在等。」最大块是 7 字整块，取不到「子爵」这种真形。
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
MAX_FORM = 5


def cjk_forms(dest: str) -> set[str]:
    out: set[str] = set()
    for run in CJK_RUN.findall(dest):
        n = len(run)
        for size in range(2, min(MAX_FORM, n) + 1):
            for i in range(n - size + 1):
                out.add(run[i:i + size])
    return out


def cjk_prefix_forms(dest: str) -> set[str]:
    """出现在 CJK 块**起始**位置的形。

    中文里译名多作前缀（「铁匠布兰特」「老爷布兰特」），而跨词边界的 n-gram
    （如「匠布」）同样满足互斥却不是名字；用「是否贴块首」把二者分开。
    """
    out: set[str] = set()
    for run in CJK_RUN.findall(dest):
        n = len(run)
        for size in range(2, min(MAX_FORM, n) + 1):
            out.add(run[:size])
    return out



def load_rows(xml_path: Path) -> list[tuple[int, str, str]]:
    rows = []
    for idx, s in enumerate(ET.parse(xml_path).getroot().iter("String")):
        src = s.findtext("Source") or ""
        dst = s.findtext("Dest") or ""
        if not dst or dst == src:
            continue  # 只在已译行里发现：未译行没有中文形可比
        rows.append((idx, src, dst))
    return rows


def collect_seeds(rows, max_occurrences: int = 60,
                  min_capitalized_ratio: float = 0.9) -> dict[str, dict]:
    """英文专名种子 → {display, occurrences:[idx...]}。

    三道过滤决定信噪比：
    ① 首词是虚词（FUNCTION_WORDS）的一律丢——`Against Mora`、`Can I` 这类组合整条
       不是专名（首词检查对多词种子同样生效）。
    ② 出现次数超过 `max_occurrences` 的一律丢——在数万行语料里出现上百次的「专名」
       是常用词（`Can I` ×142 实测）。
    ③ 单词种子在语料里若常以**小写**形态出现，就是被句首大写的普通名词，不是专名：
       `Calm`/`Corruption`/`Defeat` 这类靠这一条剔掉（实测剔除后候选腰斩）。
       多词种子不做此检查（`Daughter of Kyne` 的末词是普通名词，整条仍是专名）。
    """
    lower_count: Counter = Counter()
    for _idx, src, _dst in rows:
        for t in LATIN_RE.findall(src):
            if t[:1].islower():
                lower_count[t.lower()] += 1

    seeds: dict[str, dict] = {}
    for idx, src, _dst in rows:
        for m in SEED_RE.finditer(src):
            span = m.group(0).strip()
            if not span:
                continue
            first = span.split()[0].lower().strip("'’-")
            if first in FUNCTION_WORDS:
                continue
            if " " not in span:
                if len(span) < 3:
                    continue
                total = len(seeds.setdefault(
                    span.lower(), {"display": span, "occurrences": [],
                                   "_cap": 0})["occurrences"]) + 1
                seeds[span.lower()]["_cap"] = total
            key = span.lower()
            entry = seeds.setdefault(key, {"display": span, "occurrences": [],
                                           "_cap": 0})
            entry["occurrences"].append(idx)

    out = {}
    for k, v in seeds.items():
        occ = len(v["occurrences"])
        if occ > max_occurrences:
            continue
        if occ < 2:
            continue
        if " " not in k:
            lc = lower_count.get(k, 0)
            if lc + occ and lc / (lc + occ) > 1 - min_capitalized_ratio:
                continue  # 小写形态常见 → 普通名词
        v.pop("_cap", None)
        out[k] = v
    return out


_NAME_FIELDS = ("english", "source", "term", "en", "name", "label", "surface")


def _harvest(obj, out: set[str]) -> None:
    """递归收集任何看起来像英文专名的字符串值。

    proper-noun-index 清单用 `names[].name`，编译契约用 `terms[tid].source`——
    两种结构都收，否则已登记专名会被当成新种子重复报警（实测 Artaeum 等 95 条全漏）。
    """
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            out.add(s.lower())
            out.add(s.lower().rstrip("'s"))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (str, list, dict)) and str(k).lower() in _NAME_FIELDS:
                _harvest(v, out)
            elif isinstance(v, (list, dict)):
                _harvest(v, out)
        return
    if isinstance(obj, list):
        for v in obj:
            _harvest(v, out)


def registered_keys(inventory_paths: list[Path]) -> set[str]:
    """从 proper-noun-index 清单与 terms.json/编译契约收集已登记英文形。"""
    out: set[str] = set()
    for p in inventory_paths:
        if not p or not Path(p).is_file():
            continue
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        _harvest(data, out)
    return out


def _group_form(rows: set[int], forms, cover, at_start) -> str | None:
    """一个原子组的代表形，按可信度降序取：

    1. 本组每行都贴块首出现、且别组一个都没有（最可信：前缀式译名）；
    2. 只出现在本组（别组没有），不要求贴块首；
    3. 能覆盖本组全部行（可能含共有的词内片段，仅作兜底）。
    """
    for c in sorted(forms, key=lambda x: (len(x), x)):
        if at_start.get(c) == rows and cover[c] == rows:
            return c
    for c in sorted(forms, key=lambda x: (len(x), x)):
        if cover[c] == rows:
            return c
    for c in sorted(forms, key=lambda x: (len(x), x)):
        if rows <= cover[c]:
            return c
    return None


def _split(rows: set[int], forms, cover, at_start, min_group: int) -> list[tuple[set[int], str | None]]:
    """最平衡二分：找能把 rows 切成两半（两侧都 ≥ min_group）的形，反复递归。

    不按「最大覆盖」贪心——那会选中所有行共有的片段（如名字的一部分），
    把整批吞成一个组，于是永远看不到分裂。
    """
    n = len(rows)
    best = None
    for c in sorted(forms, key=lambda x: (len(x), x)):
        s = cover[c] & rows
        t = len(s)
        if t == 0 or t == n:
            continue
        if t < min_group or (n - t) < min_group:
            continue
        bal = min(t, n - t)
        if best is None or bal > best[0]:
            best = (bal, c, s)
    if best is None:
        return [(rows, _group_form(rows, forms, cover, at_start))]
    _, _c, s = best
    return (_split(s, forms, cover, at_start, min_group)
            + _split(rows - s, forms, cover, at_start, min_group))


def split_candidates(occ: list[int], row_dst: dict[int, str],
                     min_group: int) -> list[dict] | None:
    """互斥覆盖判据：能否把出现行分成 ≥2 组，每组有一个别组绝不出现的稳定中文形。

    返回 [{form, idxs}, ...]（互斥组），判不出返回 None。
    """
    occ_set = set(occ)
    forms = set()
    for i in occ_set:
        forms.update(cjk_forms(row_dst[i]))
    if not forms:
        return None
    cover = {c: {i for i in occ_set if c in row_dst[i]} for c in forms}
    cover = {c: s for c, s in cover.items() if s}
    at_start = {}
    for c in forms:
        s = {i for i in occ_set if c in cjk_prefix_forms(row_dst[i])}
        if s:
            at_start[c] = s

    groups = _split(occ_set, forms, cover, at_start, min_group)
    if len(groups) < 2 or any(form is None for _rows, form in groups):
        return None
    # 组间互斥性校验：任一组的形都不得出现在别组的行里
    for a, (_ra, ca) in enumerate(groups):
        for b, (_rb, cb) in enumerate(groups):
            if a == b:
                continue
            if _ra & cover[cb]:
                return None
    return [{"form": c, "idxs": sorted(r)} for r, c in groups]


def scan(xml_path: Path, inventory_paths: list[Path],
         min_occurrences: int, min_group: int,
         max_occurrences: int = 60) -> dict:
    rows = load_rows(xml_path)
    row_src = {i: s for i, s, _ in rows}
    row_dst = {i: d for i, _, d in rows}
    registered = registered_keys(inventory_paths)
    seeds = collect_seeds(rows, max_occurrences=max_occurrences)

    candidates = []
    for key, entry in sorted(seeds.items()):
        if key in registered:
            continue
        occ = entry["occurrences"]
        if len(occ) < min_occurrences:
            continue
        groups = split_candidates(occ, row_dst, min_group)
        if not groups:
            continue
        if any(g["form"] in registered for g in groups):
            continue  # 中文形本身已登记 → 不是待裁决的漂移
        sample_idx = groups[0]["idxs"][0]
        candidates.append({
            "english": entry["display"],
            "occurrences": occ,
            "zh_groups": groups,
            "sample": {"idx": sample_idx,
                       "source": row_src[sample_idx],
                       "dest": row_dst[sample_idx]},
        })
    return {
        "xml": str(xml_path),
        "scanned_rows": len(rows),
        "seed_count": len(seeds),
        "registered_keys": len(registered),
        "min_occurrences": min_occurrences,
        "min_group": min_group,
        "max_occurrences": max_occurrences,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem")
    ap.add_argument("--xml", type=Path)
    ap.add_argument("--registered", type=Path, nargs="*", default=[],
                    help="已登记英文形来源（proper-noun-index 清单 / terms.json）")
    ap.add_argument("--min-occurrences", type=int, default=3,
                    help="种子最少出现次数（低于此不发候选，默认 3）")
    ap.add_argument("--min-group", type=int, default=2,
                    help="每个中文形组的最少行数，默认 2（压掉偶发共现噪声）")
    ap.add_argument("--max-occurrences", type=int, default=60,
                    help="种子出现次数上限，默认 60（超过即判为常用词而非专名）")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="最多打印 N 个候选（0 = 全部）")
    args = ap.parse_args()

    root = Path.cwd()
    if args.xml:
        xml_p = args.xml
    elif args.stem:
        xml_p = root / "mods" / f"{args.stem}.esp" / f"{args.stem}_english_chinese_translated.xml"
        if not xml_p.exists():
            xml_p = root / "mods" / args.stem / f"{args.stem}_english_chinese_translated.xml"
    else:
        print("error: 需要 --stem 或 --xml", file=sys.stderr)
        return 2
    if not xml_p.is_file():
        print(f"error: xml not found: {xml_p}", file=sys.stderr)
        return 2

    inv = list(args.registered)
    if args.stem and not inv:
        w = root / ".work" / args.stem
        inv = [w / "reports" / f"{args.stem}-noun-inventory.json",
               w / "contracts" / f"{args.stem}.compiled.json",
               root / "mods" / f"{args.stem}.esp" / "terms.json"]
        inv = [p for p in inv if p.is_file()]

    result = scan(xml_p, inv, args.min_occurrences, args.min_group,
                  max_occurrences=args.max_occurrences)
    out = args.out or (root / ".work" / args.stem / "reports" / f"{args.stem}-seed-scan.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"seed_scan: 已译 {result['scanned_rows']} 行，发现种子 {result['seed_count']} 个，"
          f"已登记英文形 {result['registered_keys']} 条，候选 {result['candidate_count']} 组")
    for c in (result["candidates"][:args.limit] if args.limit else result["candidates"]):
        forms = " / ".join(f"{g['form']}({len(g['idxs'])})" for g in c["zh_groups"])
        print(f"  [{c['english']}] ×{len(c['occurrences'])} → {forms}")
        print(f"      样例 {c['sample']['idx']}: {c['sample']['source'][:56]}")
        print(f"            → {c['sample']['dest'][:56]}")
    print(f"wrote {out}")
    print("提醒：候选需裁决后才可改译文或落词表；规则零命中不等于收敛。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
