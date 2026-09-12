#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成长文本源译对照读本，供抗幻觉语义精读分片。

为什么按 `[idx]` 头分块而不是按空行
------------------------------------
BOOK:DESC / MESG:DESC 的 Dest 含 <font> 标记与空行，按空行分块会把一条切成残片，
阅读者看似整段漏译。历史事故：某轮审校分片用空行分块，1249 条里有 83 条没打印
Dst，审校代理回查 canonical 才发现是切片缺陷（`.work/<plugin>/notes/` 审计报告
§5.4）。本工具按 `[idx] ...` 头切，不按空行。

分片策略
--------
- 同一 EDID 的行不拆开（保持一本书 / 一条任务线的上下文完整）；
- 组间按组内最长行降序（高风险的长文本先审）；
- 按源文字符量贪心装片，单片不超过 --cap。

用法
----
    py -3 longtext_readout.py --xml <canonical.xml> --out-dir _tmp/data/longtext-shards \
        --min-src 300 --cap 17000

输出：`longtext-N.txt`（每片含【源文】【译文】对照）+ `manifest.json`。
只读 canonical，输出到调用方给定目录（约定放 _tmp/）。
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET


def main():
    ap = argparse.ArgumentParser(description="生成长文本源译对照读本分片")
    ap.add_argument("--xml", required=True, help="canonical 译文 XML")
    ap.add_argument("--out-dir", required=True, help="输出目录（约定 _tmp/ 下）")
    ap.add_argument("--min-src", type=int, default=300, help="只收录源文 >= 该长度的行")
    ap.add_argument("--cap", type=int, default=17000, help="单片源文字符上限")
    a = ap.parse_args()

    ss = list(ET.parse(a.xml).getroot().iter("String"))
    rows = []
    for i, s in enumerate(ss):
        src = s.findtext("Source") or ""
        dst = s.findtext("Dest") or ""
        if not src or src == dst or len(src) < a.min_src:
            continue
        rows.append({
            "idx": i,
            "rec": s.findtext("REC") or "",
            "edid": s.findtext("EDID") or "",
            "src": src,
            "dst": dst,
        })

    groups = {}
    for r in rows:
        groups.setdefault(r["edid"], []).append(r)
    ordered = sorted(groups.values(), key=lambda g: -max(len(x["src"]) for x in g))

    shards, cur, cur_len = [], [], 0
    for g in ordered:
        g_len = sum(len(x["src"]) for x in g)
        if cur and cur_len + g_len > a.cap:
            shards.append(cur)
            cur, cur_len = [], 0
        cur.extend(g)
        cur_len += g_len
    if cur:
        shards.append(cur)

    os.makedirs(a.out_dir, exist_ok=True)
    manifest = []
    for n, shard in enumerate(shards, 1):
        shard.sort(key=lambda r: r["idx"])
        path = os.path.join(a.out_dir, "longtext-%d.txt" % n)
        parts = []
        for r in shard:
            parts.append("=" * 78)
            parts.append(f"[{r['idx']}] {r['rec']} | {r['edid']} | src={len(r['src'])}")
            parts.append("-" * 78)
            parts.append("【源文】")
            parts.append(r["src"])
            parts.append("")
            parts.append("【译文】")
            parts.append(r["dst"])
            parts.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts) + "\n")
        manifest.append({
            "shard": n,
            "file": path,
            "rows": len(shard),
            "src_chars": sum(len(r["src"]) for r in shard),
            "idx_range": [shard[0]["idx"], shard[-1]["idx"]],
            "edids": sorted({r["edid"] for r in shard}),
        })
        print(f"  {path}: {len(shard)} 行 | {sum(len(r['src']) for r in shard):,} 源文字符")

    with open(os.path.join(a.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"total_rows": len(rows), "min_src": a.min_src, "cap": a.cap,
                   "shards": manifest}, fh, ensure_ascii=False, indent=1)
    print(f"\n共 {len(shards)} 片 / {len(rows)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
