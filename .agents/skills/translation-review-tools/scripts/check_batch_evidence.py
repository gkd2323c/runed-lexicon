#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批注实证核验：把 map.json 里 `evidence` 声明的官方查证**重跑一遍**。

为什么需要它
------------
派单卡要求 `notes` 纯中文零拉丁字母，于是 notes **物理上装不下任何证据**
（无查询词、无命中数、无命令）。子代理想在批注里表现「有据可依」时唯一可用的
形式是散文，而散文正是会被编出来的东西。实测失效形态：

    notes: "官方词典换大小写后实证祭坛，本库神龛一语已作神龛，取库内既成面"

——库内「神龛」零命中、「祭坛」十五处以上，词表明文 `forbidden: ["神龛"]`。
**散文形式的完成态断言不可核验，因此不可信。**

做法：证据从 prose 挪进结构化字段 `evidence`，允许拉丁，可被本脚本重跑复现：

    "883": {
      "translation": "精灵抽屉",
      "notes": "纯中文说明，不承担举证责任",
      "evidence": {"lookup": "Drawer", "exact_hits": 12, "zh_forms": ["抽屉"]}
    }

**只报不一致，不定罪。** 官方词典会随 MOD 更新，命中数变化可能是正常的；
这类行报出来让人看一眼即可。真正要拦的是**结构性编造**：
`zh_forms` 与官方实际译形对不上，或 `exact_hits` 与实测差得离谱。

用法：
    py -3 check_batch_evidence.py --stem summersetisles --batch NI-DIAL-015
    py -3 check_batch_evidence.py --stem summersetisles --batch-dir <dir> --xml <canonical>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_LOOKUP_DIR = HERE.parents[1] / "skyrim-xml-tools" / "scripts"
if str(_LOOKUP_DIR) not in sys.path:
    sys.path.insert(0, str(_LOOKUP_DIR))
# 复用 lookup_batch 的加载与探测，不去正则解析它的 CLI 输出（输出格式会变）
from lookup_batch import load_all, probe, renderings  # noqa: E402

# 命中数允许的偏差：词典会更新，不因个位数变化就判编造。
HITS_TOLERANCE = 2


def load_official(terms: list[str], dictionary_dir: Path | None = None) -> dict[str, dict]:
    """跑一次 exact 探测，返回 {词: {hits, forms}}。全部词共用一次词典加载。"""
    if not terms:
        return {}
    rows = load_all(dictionary_dir or Path("dictionary"))
    out: dict[str, dict] = {}
    for term in terms:
        hits, _long = probe(rows, term, contains=False, ignore_case=False)
        out[term] = {"hits": len(hits), "forms": renderings(hits, 8)}
    return out


def _forms_text(forms: str) -> str:
    return (forms or "").replace("|", "").replace("…", "")


def check_entry(idx: str, ev: dict, official: dict) -> list[str]:
    """返回不一致说明；空列表 = 通过或无法判定。"""
    problems: list[str] = []
    word = ev.get("lookup")
    if not word:
        return ["evidence 缺 lookup（无法重跑）"]
    info = official.get(word)
    if info is None:
        return [f"查不到 {word!r} 的查证结果（lookup 脚本可能未识别该行）"]
    if "exact_hits" in ev and isinstance(ev["exact_hits"], int) and info.get("hits", -1) >= 0:
        claimed, actual_hits = ev["exact_hits"], info["hits"]
        if actual_hits == 0 and claimed > 0:
            problems.append(f"声明 exact_hits={claimed} 但实测零命中（编造命中数）")
        elif abs(claimed - actual_hits) > HITS_TOLERANCE:
            problems.append(f"声明 exact_hits={claimed} 实测 {actual_hits}")
    forms = ev.get("zh_forms")
    if forms and info.get("forms"):
        actual = _forms_text(info["forms"])
        if not any(f in actual for f in forms):
            problems.append(
                f"声明译形 {forms} 与官方实际译形「{info['forms']}」无重合")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="重跑并核验 map.json 的 evidence 声明")
    ap.add_argument("--batch-dir", required=True, help="批次目录（含 map.json）")
    ap.add_argument("--xml", help="canonical 译文 XML（可选，用于库内既成面核验）")
    ap.add_argument("--dictionary", default="dictionary", help="官方词典目录（默认 dictionary）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    bd = Path(args.batch_dir)
    mp = bd / "map.json"
    if not mp.is_file():
        print(f"error: 找不到 {mp}", file=sys.stderr)
        return 2
    data = json.loads(mp.read_text(encoding="utf-8"))

    words = sorted({v["evidence"]["lookup"] for v in data.values()
                    if isinstance(v, dict) and isinstance(v.get("evidence"), dict)
                    and v["evidence"].get("lookup")})
    official = load_official(words, Path(args.dictionary))

    findings = []
    for idx, v in data.items():
        if not isinstance(v, dict) or not isinstance(v.get("evidence"), dict):
            continue
        for msg in check_entry(idx, v["evidence"], official):
            findings.append({"xml_index": int(idx) if str(idx).isdigit() else idx,
                             "lookup": v["evidence"].get("lookup"),
                             "problem": msg,
                             "notes": v.get("notes", "")[:60]})

    no_ev = sum(1 for v in data.values()
                if isinstance(v, dict) and not v.get("evidence"))
    if args.json:
        print(json.dumps({"checked": len(words), "no_evidence": no_ev,
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(f"批次 {bd.name}：{len(data)} 行，带 evidence {len(data)-no_ev}，"
              f"核验 {len(words)} 个查询词；不一致 {len(findings)}")
        for f in findings:
            print(f"  [{f['xml_index']}] {f['lookup']} — {f['problem']}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
