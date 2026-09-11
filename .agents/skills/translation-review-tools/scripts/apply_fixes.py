#!/usr/bin/env python3
"""把修正清单应用到批次文件（map.json / translation.json），并可生成 canonical patch。

fixes JSON 格式（idx 为键）：
  {
    "<idx>": "新译文",
    "<idx>": {
      "new": "新译文",
      "expected_current": "修正前的值（可选；校验失败即中止，防错批/手滑）",
      "notes": "可选，覆盖 map/result 的 notes",
      "status": "可选，覆盖 status（默认保持不变；KEEP 改译时自动转 TRANSLATED）",
      "confidence": "可选，覆盖 confidence"
    }
  }

行为：
  1. map.json：逐条校验并更新（存在时）。
  2. translation.json：按 xml_index 同步（存在时）。
  3. --translated-xml + --patch-out：生成 canonical patch（expected_dest 取自 XML 当前
     Dest 的 CAS 守卫）；patch 文件已存在时默认合并（同 idx 以新值为准）。

先全部校验、后统一写盘；任何校验失败则不写任何文件。

Usage:
  py -3 apply_fixes.py --stem Artaeum --batch NI-CELL-002 --fixes _tmp/data/fix.json
  py -3 apply_fixes.py --stem Artaeum --batch NI-CELL-002 --fixes fix.json \
      --translated-xml mods/Artaeum.esp/Artaeum_english_chinese_translated.xml \
      --patch-out .work/Artaeum/maps/Artaeum-fix-patch.json
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


# 可随修正一起改写的非译文字段（不改 translation 本身）。
# waived_tokens：R16 占位符豁免（方括号中文化，如 [END OF SEASON 1]→[第一季结束]），
# gate PLACEHOLDER001 与 executor validate 均读该字段，须与 translation 一同落盘。
EXTRA_FIELDS = ("notes", "status", "confidence", "waived_tokens")


def normalize_fixes(raw: dict) -> dict[int, dict]:
    """规范化 fixes。`new` 可选：缺省时只改 status/notes/confidence 等字段
    （消除“只想把 REVIEW 转 TRANSLATED 也得写临时脚本”的常规需求）。"""
    fixes: dict[int, dict] = {}
    for key, value in raw.items():
        try:
            idx = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"fixes 键必须是整数 idx: {key!r}") from exc
        if isinstance(value, str):
            fixes[idx] = {"new": value}
        elif isinstance(value, dict):
            if not any(k in value for k in EXTRA_FIELDS + ("new",)):
                raise ValueError(f"fixes[{key}]: 对象形式至少需含 new/status/notes/confidence/waived_tokens 之一")
            fixes[idx] = dict(value)
        else:
            raise ValueError(f"fixes[{key}]: 值必须是字符串或对象")
    return fixes


def plan_entry(entry: dict, fix: dict, target_label: str, idx: int) -> tuple[str, str]:
    """Return (action, new_translation). action ∈ apply/skip/error-msg.

    `new` 缺省时保留现值（status/notes-only 修正）。"""
    current = entry.get("translation", "")
    new = fix.get("new", current)
    expected = fix.get("expected_current")
    if current == new:
        return "skip", new
    if expected is not None and current != expected:
        return f"error: {target_label}[{idx}] 现值不符：期望 {expected!r}，实际 {current!r}", new
    return "apply", new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--fixes", help="Fixes JSON path")
    parser.add_argument("--find", help="Substring to replace across the batch's translations (with --replace)")
    parser.add_argument("--replace", default="", help="Replacement for --find")
    parser.add_argument("--idx-list", help="With --find/--replace: restrict to these idx (whitespace/comma separated)")
    parser.add_argument("--work-root", default=".work")
    parser.add_argument("--translated-xml", help="Canonical XML for patch generation")
    parser.add_argument("--patch-out", help="Patch JSON output path")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge with an existing patch file")
    args = parser.parse_args()

    batch_dir = resolve(args.work_root) / args.stem / "batches" / args.batch
    if not batch_dir.is_dir():
        print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
        return 2

    if not args.fixes and args.find is None:
        print("error: 需要 --fixes 或 --find/--replace", file=sys.stderr)
        return 2

    if args.find is not None:
        # 批量子串替换（同型多行）：读现值 → 替换 → 转成 fixes，走同一条写盘链路。
        target = batch_dir / "map.json"
        if not target.is_file():
            print(f"error: map.json 不存在: {target}", file=sys.stderr)
            return 2
        mp = json.loads(target.read_text(encoding="utf-8"))
        subset = None
        if args.idx_list:
            subset = {s for s in args.idx_list.replace(",", " ").split() if s}
        gen = {}
        for key, entry in mp.items():
            if subset is not None and str(key) not in subset:
                continue
            cur = entry.get("translation", "")
            if args.find in cur and (args.replace != cur):
                gen[str(key)] = {"new": cur.replace(args.find, args.replace),
                                 "expected_current": cur,
                                 "notes": f"--find/--replace: {args.find!r} -> {args.replace!r}"}
        if not gen:
            print(f"no-op: 批次内无 '{args.find}' 可替换", file=sys.stderr)
            return 0
        raw = gen
        print(f"--find/--replace 命中 {len(gen)} 条")
    else:
        try:
            raw = json.loads(resolve(args.fixes).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: 无法读取 fixes: {exc}", file=sys.stderr)
            return 2
    try:
        fixes = normalize_fixes(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []

    # ---- map.json ----
    map_path = batch_dir / "map.json"
    map_data = None
    map_applied = map_skipped = 0
    if map_path.is_file():
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
        for idx, fix in sorted(fixes.items()):
            key = str(idx)
            if key not in map_data:
                errors.append(f"map.json 中不存在 idx {idx}")
                continue
            action, new = plan_entry(map_data[key], fix, "map.json", idx)
            if action.startswith("error"):
                errors.append(action)
                continue
            extras = [f for f in EXTRA_FIELDS if f in fix]
            if action == "skip":
                if extras:
                    for field in extras:
                        map_data[key][field] = fix[field]
                    map_applied += 1
                else:
                    map_skipped += 1
                continue
            entry = map_data[key]
            if str(entry.get("status", "")).upper() == "KEEP" and "status" not in fix:
                entry["status"] = "TRANSLATED"
                warnings.append(f"map.json[{idx}]: KEEP 条目改译，status 自动转 TRANSLATED")
            entry["translation"] = new
            for field in EXTRA_FIELDS:
                if field in fix:
                    entry[field] = fix[field]
            map_applied += 1

    # ---- translation.json ----
    result_path = batch_dir / "translation.json"
    result_data = None
    result_applied = result_skipped = 0
    if result_path.is_file():
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
        units = {u.get("xml_index"): u for u in result_data.get("translations", [])}
        for idx, fix in sorted(fixes.items()):
            unit = units.get(idx)
            if unit is None:
                warnings.append(f"translation.json 无此 unit（跳过）: {idx}")
                continue
            action, new = plan_entry(unit, fix, "translation.json", idx)
            if action.startswith("error"):
                errors.append(action)
                continue
            extras = [f for f in EXTRA_FIELDS if f in fix]
            if action == "skip":
                if extras:
                    for field in extras:
                        unit[field] = fix[field]
                    result_applied += 1
                else:
                    result_skipped += 1
                continue
            if unit.get("status", "").upper() == "KEEP" and "status" not in fix:
                unit["status"] = "TRANSLATED"
                warnings.append(f"translation.json[{idx}]: KEEP unit 改译，status 自动转 TRANSLATED")
            unit["translation"] = new
            if unit.get("status", "").upper() == "PENDING":
                unit["status"] = "TRANSLATED"
            for field in EXTRA_FIELDS:
                if field in fix:
                    unit[field] = fix[field]
            result_applied += 1

    # ---- patch ----
    patch: dict[str, dict] = {}
    patch_skipped = 0
    if args.patch_out and not args.translated_xml:
        errors.append("--patch-out 需要同时提供 --translated-xml")
    if args.patch_out and args.translated_xml:
        xml_path = resolve(args.translated_xml)
        if not xml_path.is_file():
            errors.append(f"--translated-xml 不存在: {xml_path}")
        else:
            root = ET.parse(str(xml_path)).getroot()
            nodes = list(root.iter("String"))
            for idx, fix in sorted(fixes.items()):
                if not (0 <= idx < len(nodes)):
                    errors.append(f"patch: idx 越界 {idx}")
                    continue
                current = nodes[idx].findtext("Dest") or ""
                if current == fix["new"]:
                    patch_skipped += 1
                    continue
                patch[str(idx)] = {"expected_dest": current, "translation": fix["new"]}

    if errors:
        print("== 校验失败，未写任何文件 ==", file=sys.stderr)
        for msg in errors:
            print(f"  {msg}", file=sys.stderr)
        return 2

    # ---- write ----
    if map_data is not None and map_applied:
        map_path.write_text(json.dumps(map_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result_data is not None and result_applied:
        result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    patch_desc = ""
    if args.patch_out:
        patch_path = resolve(args.patch_out)
        existing: dict = {}
        if patch_path.is_file() and not args.no_merge:
            try:
                existing = json.loads(patch_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                warnings.append(f"现有 patch 无法解析，忽略: {patch_path}")
        merged = dict(existing)
        overwritten = 0
        for key, value in patch.items():
            if key in merged:
                overwritten += 1
            merged[key] = value
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        patch_desc = (f"patch: +{len(patch)}（覆盖已有 {overwritten}）"
                      f"{f'，跳过已就位 {patch_skipped}' if patch_skipped else ''} -> {patch_path}")

    print(f"== apply fixes: {args.stem} / {args.batch} ==")
    print(f"map.json: 更新 {map_applied}，跳过(已就位) {map_skipped}"
          + ("（不存在）" if map_data is None else ""))
    print(f"translation.json: 更新 {result_applied}，跳过 {result_skipped}"
          + ("（不存在）" if result_data is None else ""))
    if patch_desc:
        print(patch_desc)
    for msg in warnings:
        print(f"  WARN {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
