#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_sync.py — 批次文件与 canonical 的对账（check）、同步（apply）与重建（rebuild）。

背景与职责
==========
canonical XML 是唯一真相源；批次文件（map.json / translation.json）是它的派生视图。
对 canonical 的修正（apply_fixes / make_patch_from_maps 生成的 patch 写回）若不回写
批次文件，会产生两类次生灾害：

- 审查视图从旧值生成 → 重报已修问题（历史事故，SOP 已记录）；
- 再次 fill 时以旧 map 为源覆盖修正（map 是批次域唯一真相源）。

本模块把「同步」做进修正链的原子环节，并提供收口对账手段：

- ``check``：只读对账，报三类不一致：
    A. map.json 值 vs canonical Dest（批次落后/领先）；
    B. translation.json 值 vs canonical Dest；
    C. 批内 map vs translation。
  退出码 0 = 无漂移；1 = 有漂移（报告已出）。

- ``apply``：拉平。默认从 canonical 拉平 A/B（批内 C 由 A/B 结果自然收敛）；
  ``--patch`` 给定时从 patch 同步（patch 值 = 目标值）。

- ``rebuild``：补全批次层缺失（派生视图可从 canonical 无损重建）。map.json 文件
  缺失时从 canonical 重建（仅已译行，status=TRANSLATED）；文件存在但缺行键时只补
  缺失键——**只增不改，绝不覆盖既有值**（值漂移属 apply 职责）。translation.json
  不在此重建：其 immutable 字段（review_reasons / protected_tokens）必须经 fill 链路
  重算，重建后跑 ``consume_batch`` 重链。

库接口（apply_fixes.py / make_patch_from_maps.py 动态加载）
==========================================================
``sync_updates(updates, batches_dir, dry_run=False) -> dict``

    updates: {idx: {"translation": str, "expected_current"?: str}}
    把每个 idx 的目标值写进所有含该 idx 的批次文件（map + translation）。
    只改 translation 文本与矛盾状态（KEEP→TRANSLATED、PENDING→TRANSLATED）；
    不改任何其他字段。

用法
====
    py -3 batch_sync.py check --stem TheKalpicAnomaly
    py -3 batch_sync.py check --stem TheKalpicAnomaly --xml mods/.../translated.xml
    py -3 batch_sync.py apply --stem TheKalpicAnomaly
    py -3 batch_sync.py apply --stem X --patch .work/X/maps/X-fix-patch.json --dry-run
    py -3 batch_sync.py rebuild --stem TheKalpicAnomaly [--batch BID] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]

DETAIL_LIMIT = 40


def resolve(value: str, root: Path = PROJECT_ROOT) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p).resolve()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_batch_dirs(batches_dir: Path):
    if not batches_dir.is_dir():
        raise SystemExit(f"error: 批次目录不存在: {batches_dir}")
    return sorted((d for d in batches_dir.iterdir() if d.is_dir()), key=lambda d: d.name)


def resolve_canonical(stem: str, xml_arg: str | None) -> Path | None:
    if xml_arg:
        p = resolve(xml_arg)
        return p if p.is_file() else None
    matches = sorted((PROJECT_ROOT / "mods").glob(f"*/{stem}_english_chinese_translated.xml"))
    return matches[0] if matches else None


def load_canonical(path: Path) -> tuple[list[str], list[str]]:
    root = ET.parse(str(path)).getroot()
    strings = root.findall(".//String")
    dests = [s.findtext("Dest") or "" for s in strings]
    srcs = [s.findtext("Source") or "" for s in strings]
    return dests, srcs


def _map_units(data: dict) -> dict[int, dict]:
    """map.json 键值归一：{idx: entry}。非整数键跳过。"""
    out: dict[int, dict] = {}
    for k, v in data.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        out[idx] = v if isinstance(v, dict) else {"translation": v}
    return out


def _result_units(data: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for u in data.get("translations", []):
        idx = u.get("xml_index")
        if isinstance(idx, int):
            out[idx] = u
    return out


# --------------------------------------------------------------------------
# 库接口：同步
# --------------------------------------------------------------------------

def sync_updates(updates: dict, batches_dir: Path, dry_run: bool = False) -> dict:
    """把 {idx: {"translation": .., "expected_current"?: ..}} 同步进批次文件。

    返回报告 dict：
      applied     实际改动的「idx×文件」条目数
      already     已就位的 idx 数（值已等于目标）
      missing     [idx] 不属于任何批次
      mismatch    [{idx, batch, where, current, expected}] 现值与 expected_current 不符（仍同步）
      warnings    [str]
      batches     {batch: 改动条目数}
      files       [str] 实际写盘的文件路径（dry_run 时为空）
      errors      [str]
    """
    report: dict = {
        "applied": 0, "already": 0, "missing": [], "mismatch": [],
        "warnings": [], "batches": {}, "files": [], "errors": [],
    }

    targets: dict[int, dict] = {}
    for k, v in updates.items():
        idx = int(k) if not isinstance(k, int) else k
        targets[idx] = {"translation": v} if isinstance(v, str) else dict(v)

    if not targets:
        return report

    # 阶段 1：定位——全量扫键（只读，不保留数据），构建 idx -> 批次集
    located: dict[int, set[str]] = {}
    for bdir in iter_batch_dirs(batches_dir):
        mp = bdir / "map.json"
        if mp.is_file():
            try:
                for idx in _map_units(load_json(mp)):
                    if idx in targets:
                        located.setdefault(idx, set()).add(bdir.name)
            except (OSError, json.JSONDecodeError) as exc:
                report["errors"].append(f"读取失败 {mp}: {exc}")
        rp = bdir / "translation.json"
        if rp.is_file():
            try:
                for idx in _result_units(load_json(rp)):
                    if idx in targets:
                        located.setdefault(idx, set()).add(bdir.name)
            except (OSError, json.JSONDecodeError) as exc:
                report["errors"].append(f"读取失败 {rp}: {exc}")

    if report["errors"]:
        return report

    # 阶段 2：应用——只加载命中批次，先全量计算，后统一写盘
    cache: dict[Path, dict] = {}
    dirty: set[Path] = set()

    def get(path: Path):
        if path not in cache:
            cache[path] = load_json(path) if path.is_file() else None
        return cache[path]

    for idx in sorted(targets):
        tgt = targets[idx]
        target_val = tgt["translation"]
        expected = tgt.get("expected_current")
        names = located.get(idx)
        if not names:
            report["missing"].append(idx)
            continue
        idx_touched = False
        for bname in sorted(names):
            bdir = batches_dir / bname
            touched_here = False

            mp_path = bdir / "map.json"
            mp = get(mp_path)
            if mp is not None:
                entry = mp.get(str(idx))
                if isinstance(entry, dict):
                    cur = entry.get("translation", "")
                    if cur == target_val:
                        pass
                    else:
                        if expected is not None and cur != expected:
                            report["mismatch"].append(
                                {"idx": idx, "batch": bname, "where": "map",
                                 "current": cur, "expected": expected})
                        if str(entry.get("status", "")).upper() == "KEEP":
                            entry["status"] = "TRANSLATED"
                            report["warnings"].append(
                                f"map.json[{idx}] @{bname}: KEEP 条目改译，status 自动转 TRANSLATED")
                        entry["translation"] = target_val
                        dirty.add(mp_path)
                        touched_here = True
                        report["applied"] += 1
                elif isinstance(entry, str):
                    if entry != target_val:
                        entry_new = {"translation": target_val, "status": "TRANSLATED", "confidence": "HIGH"}
                        mp[str(idx)] = entry_new
                        dirty.add(mp_path)
                        touched_here = True
                        report["applied"] += 1

            rp_path = bdir / "translation.json"
            rd = get(rp_path)
            if rd is not None:
                for u in rd.get("translations", []):
                    if u.get("xml_index") != idx:
                        continue
                    cur = u.get("translation", "")
                    if cur != target_val:
                        if expected is not None and cur != expected:
                            report["mismatch"].append(
                                {"idx": idx, "batch": bname, "where": "result",
                                 "current": cur, "expected": expected})
                        status = str(u.get("status", "")).upper()
                        if status in ("KEEP", "PENDING"):
                            if status == "KEEP":
                                report["warnings"].append(
                                    f"translation.json[{idx}] @{bname}: KEEP unit 改译，status 自动转 TRANSLATED")
                            u["status"] = "TRANSLATED"
                        u["translation"] = target_val
                        dirty.add(rp_path)
                        touched_here = True
                        report["applied"] += 1
                    break

            if touched_here:
                idx_touched = True
                report["batches"][bname] = report["batches"].get(bname, 0) + 1

        if not idx_touched:
            report["already"] += 1

    if not dry_run and not report["errors"]:
        for path in sorted(dirty):
            dump_json(path, cache[path])
            report["files"].append(str(path))

    return report


# --------------------------------------------------------------------------
# CLI：check
# --------------------------------------------------------------------------

def collect_fix_sources(maps_dir: Path) -> set[int]:
    """扫 maps/ 下的修正集，收集其中出现的 idx。

    用途：漂移方向判定。批次值若来自修正集（fix-map / fix-patch），说明它比
    canonical 新，应当写回（make_patch_from_maps）；否则 canonical 新，拉平即可。
    拉平会把未写回的修正冲掉，方向判错就是数据丢失级事故。
    """
    found: set[int] = set()
    if not maps_dir.is_dir():
        return found
    for p in sorted(maps_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for k in data:
            try:
                found.add(int(k))
            except (TypeError, ValueError):
                continue
    return found


def cmd_check(args) -> int:
    stem = args.stem
    batches_dir = (resolve(args.batches_dir) if args.batches_dir
                   else (resolve(args.work_root) / stem / "batches"))
    canonical = resolve_canonical(stem, args.xml)
    if canonical is None:
        print(f"error: 未找到 canonical（mods/*/{stem}_english_chinese_translated.xml）；用 --xml 显式指定",
              file=sys.stderr)
        return 2
    dests, srcs = load_canonical(canonical)

    drifts: dict[str, list] = {"map": [], "result": [], "inner": []}
    n_batches = 0
    for bdir in iter_batch_dirs(batches_dir):
        n_batches += 1
        mp_path = bdir / "map.json"
        rp_path = bdir / "translation.json"
        map_units = _map_units(load_json(mp_path)) if mp_path.is_file() else {}
        result_units = _result_units(load_json(rp_path)) if rp_path.is_file() else {}

        for idx, entry in sorted(map_units.items()):
            if idx >= len(dests):
                continue
            cval = dests[idx]
            if not cval or cval == srcs[idx]:
                continue
            bval = entry.get("translation", "")
            if bval and bval != cval:
                drifts["map"].append((bdir.name, idx, bval, cval))
        for idx, u in sorted(result_units.items()):
            if idx >= len(dests):
                continue
            cval = dests[idx]
            if not cval or cval == srcs[idx]:
                continue
            bval = u.get("translation", "")
            if bval and bval != cval:
                drifts["result"].append((bdir.name, idx, bval, cval))
        for idx in sorted(set(map_units) & set(result_units)):
            mv = map_units[idx].get("translation", "")
            rv = result_units[idx].get("translation", "")
            if mv and rv and mv != rv:
                drifts["inner"].append((bdir.name, idx, mv, rv))

    total = sum(len(v) for v in drifts.values())
    print(f"== 批次对账: {stem} ==")
    print(f"canonical: {canonical}")
    print(f"批次: {n_batches}")
    print(f"A. map vs canonical:         {len(drifts['map'])} 条")
    print(f"B. translation vs canonical: {len(drifts['result'])} 条")
    print(f"C. 批内 map vs translation:  {len(drifts['inner'])} 条")
    if total == 0:
        print("零漂移")
        return 0

    # 方向判定：A/B 类漂移中，批次值来自修正集（maps/）的 idx → 修正未写回，
    # 应写回 canonical；否则 canonical 新，apply 拉平即可。
    # C 类（批内 map vs translation）单独处置：重链（consume_batch / fill）。
    # maps 目录从 batches_dir 的父目录推（--batches-dir 显式指定时同根，保持自洽）。
    fix_sources = collect_fix_sources(batches_dir.parent / "maps")
    to_canonical: list = []
    to_batches: list = []
    for kind in ("map", "result"):
        for item in drifts[kind]:
            idx = item[1]
            (to_canonical if idx in fix_sources else to_batches).append((kind, item))

    if drifts["inner"]:
        print(f"\n-- 批内不一致（map vs translation，共 {len(drifts['inner'])} 条） --")
        for bname, idx, a, b in drifts["inner"][: args.limit]:
            print(f"[C] {bname} [{idx}]")
            print(f"      map:         {a[:80]}")
            print(f"      translation: {b[:80]}")
        print("处置: 重链该批（consume_batch --stem <stem> --batch <BID> ...），map 为批次内真相源")

    if to_canonical:
        print(f"\n-- 修正未写回 canonical（批次新，共 {len(to_canonical)} 条；maps/ 中存在修正集） --")
        for kind, (bname, idx, a, b) in to_canonical[: args.limit]:
            print(f"[{kind.upper()[0]}] {bname} [{idx}]")
            print(f"      批次(修正): {a[:80]}")
            print(f"      canonical:  {b[:80]}")
        if len(to_canonical) > args.limit:
            print(f"... 其余 {len(to_canonical) - args.limit} 条略")
        print("处置: 用对应 fix-map 生成 patch 并写回（make_patch_from_maps + write_translations --patch）；"
              "勿用 apply 拉平（会冲掉修正）")

    if to_batches:
        print(f"\n-- canonical 新，批次落后（共 {len(to_batches)} 条） --")
        for kind, (bname, idx, a, b) in to_batches[: args.limit]:
            print(f"[{kind.upper()[0]}] {bname} [{idx}]")
            print(f"      批次: {a[:80]}")
            print(f"      canonical: {b[:80]}")
        if len(to_batches) > args.limit:
            print(f"... 其余 {len(to_batches) - args.limit} 条略")
        print(f"处置: py -3 batch_sync.py apply --stem {stem}（以 canonical 为准拉平批次）")

    print(f"\n漂移 {total} 条（待写回 {len(to_canonical)} / 待拉平 {len(to_batches)} / 批内 {len(drifts['inner'])}）")
    return 1


# --------------------------------------------------------------------------
# CLI：apply
# --------------------------------------------------------------------------

def cmd_apply(args) -> int:
    stem = args.stem
    batches_dir = (resolve(args.batches_dir) if args.batches_dir
                   else (resolve(args.work_root) / stem / "batches"))

    if args.patch:
        patch = load_json(resolve(args.patch))
        if not isinstance(patch, dict):
            print(f"error: patch 不是对象: {args.patch}", file=sys.stderr)
            return 2
        updates = {k: (v if isinstance(v, str) else dict(v)) for k, v in patch.items()}
        source_desc = f"patch: {args.patch}"
    else:
        canonical = resolve_canonical(stem, args.xml)
        if canonical is None:
            print(f"error: 未找到 canonical；用 --xml 显式指定", file=sys.stderr)
            return 2
        dests, srcs = load_canonical(canonical)
        updates: dict = {}
        for bdir in iter_batch_dirs(batches_dir):
            mp_path = bdir / "map.json"
            rp_path = bdir / "translation.json"
            map_units = _map_units(load_json(mp_path)) if mp_path.is_file() else {}
            result_units = _result_units(load_json(rp_path)) if rp_path.is_file() else {}
            for units in (map_units, result_units):
                for idx, entry in units.items():
                    if idx >= len(dests):
                        continue
                    cval = dests[idx]
                    if not cval or cval == srcs[idx]:
                        continue
                    bval = entry.get("translation", "")
                    if bval and bval != cval:
                        updates[idx] = {"translation": cval}
        source_desc = f"canonical: {canonical}"
        if not updates:
            print("零漂移，无需拉平")
            return 0

    report = sync_updates(updates, batches_dir, dry_run=args.dry_run)

    head = "dry-run 预演" if args.dry_run else "同步完成"
    print(f"== 批次同步: {stem} （{head}） ==")
    print(f"来源: {source_desc}")
    print(f"目标条目: {len(updates)}")
    print(f"改动: {report['applied']} 条 / {len(report['batches'])} 批")
    if report["missing"]:
        print(f"不在任何批次: {len(report['missing'])} 条 {report['missing'][:12]}"
              + (" ..." if len(report["missing"]) > 12 else ""))
    for m in report["mismatch"][:20]:
        print(f"  WARN 现值不符 [{m['batch']}] {m['idx']} ({m['where']}): "
              f"现值 {m['current'][:50]!r} != expected {m['expected'][:50]!r}（仍同步）")
    if len(report["mismatch"]) > 20:
        print(f"  ... 其余 mismatch {len(report['mismatch']) - 20} 条")
    for w in report["warnings"][:20]:
        print(f"  WARN {w}")
    for e in report["errors"]:
        print(f"  ERROR {e}", file=sys.stderr)
    if report["errors"]:
        return 1
    if not args.dry_run:
        print(f"写盘文件: {len(report['files'])}")
    return 0


# --------------------------------------------------------------------------
# CLI：rebuild
# --------------------------------------------------------------------------

def cmd_rebuild(args) -> int:
    """从 canonical 补全批次层缺失：map.json 缺文件→重建；缺行键→补键（只增不改）。"""
    stem = args.stem
    batches_dir = (resolve(args.batches_dir) if args.batches_dir
                   else (resolve(args.work_root) / stem / "batches"))
    canonical = resolve_canonical(stem, args.xml)
    if canonical is None:
        print(f"error: 未找到 canonical；用 --xml 显式指定", file=sys.stderr)
        return 2
    dests, srcs = load_canonical(canonical)

    all_dirs = iter_batch_dirs(batches_dir)
    if args.batch:
        wanted = list(dict.fromkeys(args.batch))
        targets = [d for d in all_dirs if d.name in set(wanted)]
        for name in sorted(set(wanted) - {d.name for d in targets}):
            print(f"  WARN 批次目录不存在: {name}", file=sys.stderr)
    else:
        targets = all_dirs

    created, extended, intact, skipped = 0, 0, 0, 0
    added_total = 0
    rechain: list[str] = []
    for bdir in targets:
        ip = bdir / "index.txt"
        if not ip.is_file():
            print(f"  SKIP {bdir.name}: 无 index.txt（无法确定批次行集）")
            skipped += 1
            continue
        idxs: list[int] = []
        for line in ip.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.isdigit():
                idxs.append(int(line))
        available: dict[str, dict] = {}
        for idx in idxs:
            if idx >= len(dests):
                continue
            cval = dests[idx]
            if not cval or cval == srcs[idx]:
                continue
            available[str(idx)] = {"translation": cval, "status": "TRANSLATED",
                                   "confidence": "HIGH"}

        mp = bdir / "map.json"
        added_keys: set[str] = set()
        if not mp.is_file():
            if not available:
                print(f"  SKIP {bdir.name}: canonical 无已译行可重建")
                skipped += 1
                continue
            if not args.dry_run:
                dump_json(mp, available)
            print(f"  {'[dry-run] ' if args.dry_run else ''}重建 map.json: {bdir.name}（{len(available)} 行）")
            created += 1
            added_total += len(available)
            added_keys = set(available)
        else:
            mp_data = load_json(mp)
            missing = [k for k in available if k not in mp_data]
            if missing:
                if not args.dry_run:
                    for k in missing:
                        mp_data[k] = available[k]
                    dump_json(mp, mp_data)
                print(f"  {'[dry-run] ' if args.dry_run else ''}补键 map.json: {bdir.name}（+{len(missing)} 行）")
                extended += 1
                added_total += len(missing)
                added_keys = set(missing)
            else:
                intact += 1

        # 重链判定：translation.json 缺失，或新补行未被其有效译文覆盖
        tp = bdir / "translation.json"
        if not tp.is_file():
            rechain.append(bdir.name)
        elif added_keys:
            have = {str(u.get("xml_index")) for u in load_json(tp).get("translations", [])
                    if u.get("translation")}
            if not added_keys <= have:
                rechain.append(bdir.name)

    head = "dry-run 预演" if args.dry_run else "重建完成"
    print(f"== 批次重建: {stem} （{head}） ==")
    print(f"来源: canonical: {canonical}")
    print(f"重建文件 {created} / 补键 {extended} / 完好 {intact} / 跳过 {skipped}"
          f"（补入 {added_total} 行）")
    if rechain:
        print(f"后续重链（translation.json 缺行或缺失，经 consume_batch 重算 immutable）：{len(rechain)} 批")
        print(f"  {rechain[:12]}" + (" ..." if len(rechain) > 12 else ""))
        print("  py -3 .agents/skills/translation-batch-ops/scripts/consume_batch.py "
              "--stem <stem> --batch <BID> --xml <source-xml> --contract <compiled.json>")
    return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = dict()
    p_check = sub.add_parser("check", help="只读对账，报批次文件 vs canonical 漂移")
    p_check.add_argument("--stem", required=True)
    p_check.add_argument("--xml", default=None, help="canonical XML；缺省按 mods/*/<stem>_english_chinese_translated.xml 推断")
    p_check.add_argument("--batches-dir", default=None)
    p_check.add_argument("--work-root", default=".work")
    p_check.add_argument("--limit", type=int, default=DETAIL_LIMIT, help="每类明细上限（默认 40）")
    p_check.set_defaults(func=cmd_check)

    p_apply = sub.add_parser("apply", help="把漂移拉平（canonical 或 patch → 批次文件）")
    p_apply.add_argument("--stem", required=True)
    p_apply.add_argument("--xml", default=None)
    p_apply.add_argument("--patch", default=None, help="从 patch 同步（缺省从 canonical 拉平）")
    p_apply.add_argument("--batches-dir", default=None)
    p_apply.add_argument("--work-root", default=".work")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_rb = sub.add_parser("rebuild", help="从 canonical 补全缺失的批次文件（map 缺文件/缺键，只增不改）")
    p_rb.add_argument("--stem", required=True)
    p_rb.add_argument("--xml", default=None)
    p_rb.add_argument("--batch", action="append", default=None, metavar="BID",
                      help="限定批次（可重复；缺省处理全部批次目录）")
    p_rb.add_argument("--batches-dir", default=None)
    p_rb.add_argument("--work-root", default=".work")
    p_rb.add_argument("--dry-run", action="store_true")
    p_rb.set_defaults(func=cmd_rebuild)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
