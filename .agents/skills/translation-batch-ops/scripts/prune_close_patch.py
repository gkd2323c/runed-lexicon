#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""close-round-patch.json 残留条目剪枝器。

背景（事故锚定）——`close_round.py` 产生的 `batches/<BID>/close-round-patch.json`
是**追加**而非重建。跨轮重跑时，writer 对每条做 compare-and-swap
（`expected_dest` 必须等于 canonical 现值），于是：

  ① **已就位残留**：条目的 `translation` 恰等于 canonical Dest——上一轮的修正
     实际已经写进去了，只是 patch 文件没清。这类条目会因 `expected_dest` 过期
     而让整轮 FAIL。**可安全剪除。**
  ② **老值残留**：条目的 `translation` 与 canonical 现值不同（而且
     `expected_dest` 也不等），剪掉等于丢历史，但留着必然 CAS 失败。
     **必须由人判断**，本工具默认不碰。

本项目实测两类都出现过：INFO-358 的 27737 属①（可剪），INFO-063 的 12557、
INFO-154 属②（须删整个文件重建）。SOP §6 早已写明这条处置，但长期没有工具，
每轮都在 `_tmp/` 里手搓一次性脚本——这正是「通用脚本应进 skill」的典型缺口。

用法：
  # 默认 dry-run：只报告，不写
  py -3 prune_close_patch.py --stem <stem> --batches INFO-070 INFO-073

  # 剪除已就位残留
  py -3 prune_close_patch.py --stem <stem> --batches INFO-070 --prune

  # 老值残留必须显式放行（逐条打印，人工确认后才删）
  py -3 prune_close_patch.py --stem <stem> --batches INFO-154 --drop-stale

安全边界：
  - 默认 dry-run，不写任何文件。
  - `--prune` 只删「translation == canonical Dest」这一种，其余一律保留。
  - `--drop-stale` 只删「translation != canonical Dest」这一种，已就位的不动。
  - 两者可同时给，但会逐类打印，落盘前打印删除清单。
  - 批次目录不存在或无 patch 文件时如实报告，不当作错误。
  - 不碰 canonical XML，只读写 patch JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def load_canonical_map(xml_path: Path) -> list[str]:
    """按 xml_index 顺序取每条 <String> 的 <Dest>。"""
    root = ET.parse(xml_path).getroot()
    out = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "String":
            dest = ""
            for child in node:
                if child.tag.rsplit("}", 1)[-1] == "Dest":
                    dest = child.text or ""
            out.append(dest)
    return out


def _as_items(data):
    """把 patch 文件归一成 [(key, entry_dict), ...]。

    实际观测到两种形态：{idx: {...}} 与 [{xml_index, translation, ...}]。
    归一后统一按 dict 条目处理。
    """
    if isinstance(data, dict):
        if not data:
            return []          # 空 patch：本轮无修正，属正常状态
        if all(isinstance(v, dict) for v in data.values()):
            return [(str(k), dict(v)) for k, v in data.items()]
        # {"patches": [...]} 之类包裹形态
        for key in ("patches", "items", "entries"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            raise ValueError("无法识别的 patch 结构（dict 形态）")
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("无法识别的 patch 结构：{}".format(type(data)))

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = it.get("xml_index", it.get("idx"))
        if key is None:
            continue
        out.append((str(key), {
            "expected_dest": it.get("expected_dest", ""),
            "translation": it.get("translation", it.get("new", "")),
        }))
    return out


def _rebuild(original, items):
    """按原容器形态写回。"""
    if isinstance(original, list):
        return [{"xml_index": int(k), **v} for k, v in items]
    return {int(k): v for k, v in items}


def classify(items, dests):
    """分出「已就位」与「老值」两类。

    条目 idx 越界时归入 stale 并标注，绝不静默丢弃。
    """
    applied, stale = [], []
    for key, entry in items:
        try:
            idx = int(key)
        except (TypeError, ValueError):
            stale.append((key, entry, "idx 非整数"))
            continue
        if not (0 <= idx < len(dests)):
            stale.append((key, entry, "idx 越界 (len={})".format(len(dests))))
            continue
        cur = dests[idx]
        if entry.get("translation", "") == cur:
            applied.append((key, entry, ""))
        else:
            stale.append((key, entry, "现值={!r}".format(cur[:40])))
    return applied, stale


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="剪除 close-round-patch.json 中已就位的残留条目")
    ap.add_argument("--stem", required=True, help="MOD stem")
    ap.add_argument("--batches", nargs="+", required=True, help="批次 ID 列表")
    ap.add_argument("--xml", help="canonical XML（默认 mods/<mod>/<stem>_..._translated.xml）")
    ap.add_argument("--work", help=".work 根（默认 .work）")
    ap.add_argument("--prune", action="store_true", help="剪除已就位残留")
    ap.add_argument("--drop-stale", action="store_true", help="删除老值残留（危险，需人工确认）")
    args = ap.parse_args(argv)

    if not args.prune and not args.drop_stale:
        pass  # dry-run
    elif args.prune and args.drop_stale:
        pass  # both allowed; classified separately

    mod = args.stem + ".esp"
    xml_path = Path(args.xml) if args.xml else Path(
        "mods") / mod / "{}_english_chinese_translated.xml".format(args.stem)
    if not xml_path.is_file():
        print("error: canonical 不存在: {}".format(xml_path), file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path(".work") / args.stem

    dests = load_canonical_map(xml_path)
    print("canonical: {} ({} strings)".format(xml_path, len(dests)))
    print("mode: {}".format("写盘" if (args.prune or args.drop_stale) else "dry-run"))
    print()

    total_a = total_s = 0
    for bid in args.batches:
        p = work / "batches" / bid / "close-round-patch.json"
        if not p.is_file():
            print("{}: 无 patch 文件".format(bid))
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items = _as_items(data)
        except (ValueError, json.JSONDecodeError) as exc:
            print("{}: 解析失败 {}: {}".format(bid, p, exc), file=sys.stderr)
            return 1
        applied, stale = classify(items, dests)
        total_a += len(applied)
        total_s += len(stale)
        print("{}: 共 {} 条 | 已就位 {} | 老值 {}".format(
            bid, len(items), len(applied), len(stale)))
        for k, e, _ in applied:
            print("   [已就位] idx={}  {}".format(k, e.get("translation", "")[:50]))
        for k, e, why in stale:
            print("   [老值  ] idx={}  {}  ({})".format(
                k, e.get("translation", "")[:50], why))

        # classify 返回 (key, entry, why) 三元组；过滤 items 用的是
        # (key, entry) 二元组，直接拿三元组去比对会恒不相等——踩过一次。
        drop = []
        if args.prune:
            drop += [(k, e) for k, e, _ in applied]
        if args.drop_stale:
            drop += [(k, e) for k, e, _ in stale]
        if not drop:
            continue
        keep = [(k, e) for k, e in items if (k, e) not in drop]
        if len(keep) == len(items):
            continue
        p.write_text(json.dumps(_rebuild(data, keep), ensure_ascii=False, indent=1)
                     + "\n", encoding="utf-8")
        print("   -> 已写盘，剩余 {} 条".format(len(keep)))
        print()

    print("合计: 已就位 {} 条 / 老值 {} 条".format(total_a, total_s))
    if total_s and not args.drop_stale:
        print("提示: 老值残留需 --drop-stale 才会删（这类条目剪不掉，"
              "留着必然让 writer 的 compare-and-swap 失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
