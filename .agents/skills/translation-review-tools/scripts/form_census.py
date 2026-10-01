#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""form_census.py — 同锚词形普查：全库 vs 批内分布对照。

## 为什么需要这个工具

裁决「同锚分裂该往哪个形收敛」时，**方向必须按全库多数，不能按批内多数**。
这是本项目用两次错误代价换来的硬纪律（见 PROGRESS.md §5.1o）：

  · INFO-352+353 的 reviewer 要把 `institution` 并向「组织」——套了 `the order`
    这个**专名锚**的口径，而 `institution` 是普通名词锚，两者不可互推。
  · INFO-356+357 的 reviewer 要把 `institution` 并向「制度」——取的是**本批内**
    27660 的既有形，而全库是 机构 83 : 制度 2 : 组织 1。
  · 两条方向都错，正确方向是全库的「机构」。

根因是**审查实例只看得到自己批内的分布**。本工具把全库分布与批内分布并排打
出来，并在两者背离时直接告警，让「批内多数 ≠ 全库多数」变成可见的事实而不是
需要靠人记得的纪律。

## 用法

```text
# 基本普查（形态从契约词条自动取，也可手工指定）
py -3 form_census.py --stem <plugin> --anchor institution --anchor rescue
py -3 form_census.py --stem <plugin> --anchor restraint --forms 克制 拘束 约束

# 带上批次归属，输出批内分布并检测背离
py -3 form_census.py --stem <plugin> --anchor institution --batches-dir .work/<plugin>/batches

# 落盘 JSON 供后续程序消费
py -3 form_census.py --stem <plugin> --anchor rescue --out .work/<plugin>/reports/<plugin>-form-census.json
```

## 输出

每个锚一段：

  1. 已译行数 / 各形态计数（按行数降序，**标出全库主形**）
  2. `未归类` 桶：不含任何已知形态的行——这些是词表没登记的新形或副词/形容词
     变体，必须逐条看，默认打印 idx 与译形
  3. 批内分布（给了 `--batches-dir` 才有）：每个批次的形态计数
  4. **背离告警**：某批次内的主形与全库主形不同，且该批次行数 ≥ 2 时告警

## 安全边界

* 只读：只解析源 XML 与 canonical，不写任何翻译产物。
* `--out` 是唯一写入口，只写普查结果 JSON。
* 锚按正则匹配（大小写不敏感），**不做词形还原**；想查某个词请把它的所有
  变体一起用 `|` 串起来（例：`confiden(ce|t)`）。
* 批内分布依赖 `batches/*/index.txt`；缺失时该段整体跳过并说明，不猜归属。
* **本工具不下结论**。「哪个形该保留」是语义裁决，由主会话做；工具只负责把
  分布和背离如实摆出来。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _strings(path: Path):
    """返回 [(source, dest)]，按 xml_index 顺序。"""
    root = ET.parse(path).getroot()
    out = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "String":
            src = dest = ""
            for child in node:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "Source":
                    src = child.text or ""
                elif tag == "Dest":
                    dest = child.text or ""
            out.append((src, dest))
    return out


def forms_from_contract(contract_path: Path, anchor: str) -> list[str]:
    """从编译契约里取该锚的已知形态（target + additional_accepted）。

    取不到返回空列表——调用方回退到「未归类」全量模式，这比猜一个形可靠。
    """
    if not contract_path or not contract_path.is_file():
        return []
    try:
        data = json.loads(contract_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    terms = data.get("terms", data)
    if not isinstance(terms, dict):
        return []
    forms: list[str] = []
    for entry in terms.values():
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source", ""))
        if not source:
            continue
        # 锚本身是前缀匹配：契约条目 source 以锚开头即视为同一锚
        if not source.lower().startswith(anchor.lower().rstrip(r"\w").rstrip("(")):
            continue
        tgt = str(entry.get("target", "")).strip()
        if tgt:
            forms.append(tgt)
        for extra in entry.get("match", {}).get("accepted", []) or []:
            extra = str(extra).strip()
            if extra:
                forms.append(extra)
    # 长形优先，避免「等级」先于「等级体系」吃掉长形
    return sorted(set(forms), key=len, reverse=True)


def load_batch_owner(batches_dir: Path) -> dict[int, str]:
    """idx -> 批次 ID。读全部 <BID>/index.txt。"""
    owner: dict[int, str] = {}
    if not batches_dir.is_dir():
        return owner
    for it in sorted(batches_dir.glob("*/index.txt")):
        bid = it.parent.name
        try:
            toks = it.read_text(encoding="utf-8").split()
        except OSError:
            continue
        for tok in toks:
            try:
                owner[int(tok)] = bid
            except ValueError:
                continue
    return owner


CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def auto_forms(dest_list, top=12, min_len=2, max_len=4):
    """开集发现候选中文形态（词表无该锚时的兜底）。

    词表缺条目恰恰是最需要查分布的情形（要裁决的词往往还没进词表），
    此时全落「未归类」等于工具不可用。本模式统计该锚各行译文里的 CJK
    n-gram 频次，概念自身的形态通常排在前列——因为同一锚的行高度平行
    （同一说话人、同一语域），同一个词反复出现。

    **只产候选，不下结论**：高频 n-gram 里混着「一个」「没有」这类通用词，
    由主会话挑。`--forms` 一旦给出，本模式不再运行。
    """
    counter: collections.Counter = collections.Counter()
    for d in dest_list:
        # 去重作用域是**整行**：同一行里「同意」出现三次只计一次，
        # 否则长句/并列句会把高频形态刷成虚高。作用域若放在 CJK 段内，
        # 被标点切开的重复（「同意…同意」）会漏计（踩过一次）。
        seen = set()
        for run in CJK_RUN.findall(d or ""):
            for n in range(min_len, max_len + 1):
                for k in range(len(run) - n + 1):
                    seen.add(run[k:k + n])
        for g in seen:
            counter[g] += 1
    return [(g, c) for g, c in counter.most_common(top)]


def census(rows, anchor, forms):
    """返回该锚的普查结果。

    形态一律按**长度降序**匹配：否则「等级」会先于「等级体系」命中，把
    复合形的行错记成短形的行（`hierarchy` 那个锚正是这个形状）。
    排序放在这里而不是只放在 `forms_from_contract`，是为了让 `--forms`
    直传与契约自动取两条路径行为一致。
    """
    ordered = sorted(set(forms), key=len, reverse=True)
    pat = re.compile(anchor, re.I)
    hits = [(i, s, d) for i, (s, d) in enumerate(rows)
            if pat.search(s) and d and d != s]
    counter: collections.Counter = collections.Counter()
    per_row = []
    unclassified = []
    for i, s, d in hits:
        found = [f for f in ordered if f in d]
        if found:
            key = found[0]
            counter[key] += 1
        else:
            key = None
            counter[None] += 1
            unclassified.append((i, d))
        per_row.append((i, key))
    return {
        "anchor": anchor,
        "translated_rows": len(hits),
        "forms": [(k, v) for k, v in counter.most_common() if k],
        "unclassified": len(unclassified),
        "sites": [d for _, _, d in hits],
        "_unclassified_sites": unclassified,
        "_per_row": per_row,
    }


def batch_breakdown(per_row, owner):
    """批内分布 + 背离检测。"""
    by_batch: dict[str, collections.Counter] = {}
    for idx, form in per_row:
        bid = owner.get(idx)
        if bid is None:
            continue
        by_batch.setdefault(bid, collections.Counter())[form] += 1
    out = []
    for bid in sorted(by_batch):
        c = by_batch[bid]
        ranked = [(k, v) for k, v in c.most_common() if k]
        out.append({
            "batch": bid,
            "rows": sum(c.values()),
            "forms": ranked,
            "local_major": ranked[0][0] if ranked else None,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同锚词形普查（全库 vs 批内）")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--xml", help="canonical XML（默认 mods/<mod>/<stem>_english_chinese_translated.xml）")
    ap.add_argument("--source-xml", help="源 XML（默认同名 _english_chinese.xml）")
    ap.add_argument("--contract", help="编译契约（默认 .work/<stem>/contracts/<stem>.compiled.json）")
    ap.add_argument("--batches-dir", help="批次目录，给了才输出批内分布与背离告警")
    ap.add_argument("--anchor", action="append", required=True,
                    help="英文锚（正则，大小写不敏感），可重复")
    ap.add_argument("--forms", nargs="*", default=None,
                    help="已知中文形态；省略则从契约词条自动取")
    ap.add_argument("--auto-forms", type=int, default=8, metavar="N",
                    help="契约无该锚时，打印前 N 个开集候选形态（默认 8；0 关闭）")
    ap.add_argument("--show-sites", action="store_true", help="打印未归类站点")
    ap.add_argument("--out", help="普查结果 JSON 落盘路径")
    args = ap.parse_args(argv)

    mod = args.stem + ".esp"
    xml = Path(args.xml) if args.xml else Path("mods") / mod / "{}_english_chinese_translated.xml".format(args.stem)
    src = Path(args.source_xml) if args.source_xml else Path("mods") / mod / "{}_english_chinese.xml".format(args.stem)
    contract = Path(args.contract) if args.contract else Path(".work") / args.stem / "contracts" / "{}.compiled.json".format(args.stem)
    batches_dir = Path(args.batches_dir) if args.batches_dir else None

    for p, what in ((xml, "canonical"), (src, "源 XML")):
        if not p.is_file():
            print("error: {} 不存在: {}".format(what, p), file=sys.stderr)
            return 2

    srcs = [s for s, _ in _strings(src)]
    dests = [d for _, d in _strings(xml)]
    if len(srcs) != len(dests):
        print("error: 源 XML {} 条与 canonical {} 条不一致".format(len(srcs), len(dests)),
              file=sys.stderr)
        return 2
    pairs = list(zip(srcs, dests))
    owner = load_batch_owner(batches_dir) if batches_dir else {}

    print("anchor 源: {} ({} rows)".format(src, len(pairs)))
    if batches_dir:
        print("批次归属: {} ({} idx)".format(
            batches_dir, len(owner) if owner else "无/为空"))
    print()

    results, divergences = [], []
    for anchor in args.anchor:
        forms = list(args.forms) if args.forms else forms_from_contract(contract, anchor)
        res = census(pairs, anchor, forms)
        print("=" * 74)
        print("锚 /{s}/   已译 {n} 行".format(s=anchor, n=res["translated_rows"]))
        if not forms:
            print("  （未取到已知形态：契约无该锚或未给 --forms）")
            cands = auto_forms(res["sites"], top=args.auto_forms) if args.auto_forms else []
            if cands:
                print("  开集候选形态（n-gram 频次，**只产候选不下结论**，"
                      "请主会话挑选后用 --forms 复跑）：")
                for g, c in cands:
                    print("    {:<12s} {:>4d}  ({:.0f}% of {} 行)".format(
                        g, c, c / res["translated_rows"] * 100
                        if res["translated_rows"] else 0, res["translated_rows"]))
        else:
            print("  已知形态（长形优先，避免子串吃掉长形）: " + " / ".join(forms))
            total = res["translated_rows"]
            for k, v in res["forms"]:
                share = v / total * 100 if total else 0
                print("    {:<14s} {:>4d}  {:>5.1f}%".format(k, v, share))
            if res["forms"]:
                top = res["forms"][0][0]
                runner = res["forms"][1][0] if len(res["forms"]) > 1 else None
                rv = res["forms"][1][1] if len(res["forms"]) > 1 else 0
                tv = res["forms"][0][1]
                print("  全库主形: {}（{}/{}）".format(top, tv, total))
                if runner and rv > 0:
                    print("  次形:     {}（{}/{}）  ** 收敛方向应取主形 **".format(
                        runner, rv, total))
        print("  未归类: {} 行（词表未登记的新形，或副词/形容词变体）".format(
            res["unclassified"]))

        if args.show_sites and res["_unclassified_sites"]:
            for idx, d in res["_unclassified_sites"][:40]:
                print("    [{}] {}".format(idx, d[:70]))
            if len(res["_unclassified_sites"]) > 40:
                print("    ... 其余 {} 条省略".format(
                    len(res["_unclassified_sites"]) - 40))

        entry = {k: v for k, v in res.items() if not k.startswith("_")}

        if owner:
            bb = batch_breakdown(res["_per_row"], owner)
            entry["batches"] = bb
            if bb:
                print("  批内分布: {} 个批次有该锚".format(len(bb)))
                for b in bb[:12]:
                    forms_s = " ".join("{}={}".format(k, v) for k, v in b["forms"]) or "(全未归类)"
                    print("    {:<12s} {} 行  {}".format(b["batch"], b["rows"], forms_s))
                if len(bb) > 12:
                    print("    ... 其余 {} 个批次省略".format(len(bb) - 12))
                # 背离检测
                top = res["forms"][0][0] if res["forms"] else None
                if top:
                    for b in bb:
                        if b["rows"] >= 2 and b["local_major"] and b["local_major"] != top:
                            msg = ("批次 {} 的主形 {} 与全库主形 {} 背离"
                                   "（{} 行；全库 {}={}）".format(
                                       b["batch"], b["local_major"], top,
                                       b["rows"], top,
                                       dict(res["forms"]).get(top, 0)))
                            print("  ⚠ " + msg)
                            divergences.append({"anchor": anchor, "message": msg,
                                               "batch": b["batch"],
                                               "local_major": b["local_major"],
                                               "corpus_major": top,
                                               "batch_rows": b["rows"]})
        print()
        results.append(entry)

    if divergences:
        print("=" * 74)
        print("⚠ 背离告警 {} 条——批内多数不等于全库多数，".format(len(divergences)))
        print("  收敛方向必须按全库主形裁定（PROGRESS §5.1o）。")
    else:
        print("无背离告警。")

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"stem": args.stem, "canonical": str(xml),
             "anchors": results, "divergences": divergences},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("\n-> {}".format(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
