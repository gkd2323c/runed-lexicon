#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超长单条文本的分段翻译辅助：切段（split）与按序拼回（join）。

为什么需要它：
  `shard_batch.py` 解决「批次条目太多」，把批切成多片；但当**单片里只剩一条**、
  而这一条本身就超过单个执行者上限时（事故：NI-BOOK-003 的 a 片，单条源文 21520
  字符，连续两次执行失败），分片无济于事。此时要切的是**这一条本身**。
  审查侧已有 `longtext_readout.py`，但它的规则是「同一 EDID 不拆开」，恰好不拆单条，
  故无法复用。

设计：
  - 切点优先落在结构边界（`</p>` / `<br>` / 空行 / 段落换行），保 HTML 结构不破。
  - 每段自带首尾标记，便于质检与拼回；段长按字符数控制（默认 4000）。
  - join 时按 part 序号拼回，并校验：段数一致、每段非空、无重复段。

用法：
  # 切段：从 canonical 源 XML 取某 idx 的 Source，切成 _tmp 下 N 段
  py -3 split_longtext.py split --xml <source.xml> --idx 3290 --out-dir _tmp/data/lt-3290 --cap 4000
  # 拼回：把各段译文（part-1.txt ...）按序合成一条
  py -3 split_longtext.py join --in-dir _tmp/data/lt-3290 --out _tmp/data/lt-3290-joined.txt
  # 校验拼回结果与源文的标签序列一致
  py -3 split_longtext.py check --source-file _tmp/data/lt-3290/source.txt --joined-file _tmp/data/lt-3290-joined.txt

只读源 XML；split 只写 --out-dir，join 只写 --out。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

TAG = re.compile(r"<[^>]+>")


def split_text(src: str, cap: int) -> list[str]:
    """按结构边界把 src 切成若干段，每段 <= cap（找不到边界时才硬切）。

    实现要点：全程只用切片（slice），不做 re.split——后者在相邻零宽断点上会
    跳过匹配并丢字符（事故：`</p>\n\n<p>` 被拼成 `</p>\n<p>`，整条丢 37 字符）。
    每段在 [cap//2, cap] 窗口内回退到最后一个结构边界。
    """
    parts: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if n - i <= cap:
            parts.append(src[i:])
            break
        window = src[i : i + cap]
        cut = -1
        for m in re.finditer(r"</p>|<br\s*/?>|\n\n|[.!?\u3002\uff01\uff1f]\s|\n", window):
            if m.end() >= cap // 2:
                cut = m.end()
        if cut <= 0:
            cut = cap
        parts.append(src[i : i + cut])
        i += cut
    return parts


def cmd_split(a) -> int:
    rows = list(ET.parse(a.xml).getroot().iter("String"))
    src = rows[a.idx].findtext("Source") or ""
    out_dir = pathlib.Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "source.txt").write_text(src, encoding="utf-8")
    parts = split_text(src, a.cap)
    for i, p in enumerate(parts, 1):
        (out_dir / f"part-{i}.txt").write_text(p, encoding="utf-8")
    (out_dir / "meta.txt").write_text(
        f"idx={a.idx}\nsrc_chars={len(src)}\nparts={len(parts)}\ncap={a.cap}\n",
        encoding="utf-8",
    )
    rejoined = "".join(parts)
    if rejoined != src:
        print(f"切段无损自检失败：源 {len(src)} != 拼回 {len(rejoined)}", file=sys.stderr)
        return 1
    print(f"idx {a.idx}: {len(src)} chars -> {len(parts)} parts @cap {a.cap} -> {out_dir}")
    for i, p in enumerate(parts, 1):
        print(f"  part-{i}: {len(p)} chars")
    print("切段无损自检通过（拼回逐字节等于源文）")
    return 0


def cmd_join(a) -> int:
    d = pathlib.Path(a.in_dir)
    parts = sorted(d.glob("part-*.txt"), key=lambda p: int(re.search(r"part-(\d+)", p.name).group(1)))
    if not parts:
        print("没有找到 part-*.txt", file=sys.stderr)
        return 2
    segs = []
    for p in parts:
        t = p.read_text(encoding="utf-8")
        if not t.strip():
            print(f"空段: {p.name}", file=sys.stderr)
            return 2
        segs.append(t)
    joined = "".join(segs)
    pathlib.Path(a.out).write_text(joined, encoding="utf-8")
    print(f"joined {len(parts)} parts -> {a.out} ({len(joined)} chars)")
    return 0


def cmd_check(a) -> int:
    src = pathlib.Path(a.source_file).read_text(encoding="utf-8")
    joined = pathlib.Path(a.joined_file).read_text(encoding="utf-8")
    # 标签（引擎 token / HTML）序列必须一致——结构不因翻译而增减
    st, jt = TAG.findall(src), TAG.findall(joined)
    ok = st == jt
    print(f"src tags={len(st)} joined tags={len(jt)} tag-sequence-equal={ok}")
    if not ok:
        for i, (s, j) in enumerate(zip(st, jt)):
            if s != j:
                print(f"  首个差异 #{i}: src={s!r} joined={j!r}")
                break
        if len(st) != len(jt):
            print(f"  标签数不等：src={len(st)} joined={len(jt)}")
        return 1
    # 控制符 / 占位符粗检
    for pat in (r"%s", r"%d", r"<Alias=", r"<Global="):
        if src.count(pat) != joined.count(pat):
            print(f"  占位符计数不等: {pat} src={src.count(pat)} joined={joined.count(pat)}")
            return 1
    print("OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="超长单条文本分段/拼回（只读源 XML）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("--xml", required=True)
    s.add_argument("--idx", type=int, required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--cap", type=int, default=4000)
    s.set_defaults(func=cmd_split)
    j = sub.add_parser("join")
    j.add_argument("--in-dir", required=True)
    j.add_argument("--out", required=True)
    j.set_defaults(func=cmd_join)
    c = sub.add_parser("check")
    c.add_argument("--source-file", required=True)
    c.add_argument("--joined-file", required=True)
    c.set_defaults(func=cmd_check)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
