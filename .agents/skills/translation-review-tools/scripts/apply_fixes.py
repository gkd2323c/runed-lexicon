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
import re
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


def detect_waived_tokens(source: str, translation: str) -> list[str]:
    """译文丢失的方括号标记（源文有、译文无）。

    方括号内容（[END OF SEASON 1]、[Show Ring] 等）按官方中文惯例中文化后，
    writer 的占位符校验会报 protected token mismatch（它把方括号串当 token）。
    结果条目声明 waived_tokens 即可放行。手工补声明容易漏（实测发生过两次），
    故在生成 patch 时从 canonical 的 Source/Dest 自动算出。

    只报「整串构成单个格式 token」之外的方括号差异；重复出现按次数计。
    """
    pattern = re.compile(r"\[[A-Za-z_][A-Za-z0-9_.:=|+\- ]*\]")
    src_tokens = pattern.findall(source or "")
    dst_tokens = pattern.findall(translation or "")
    waived = []
    for token in src_tokens:
        # 按出现次数比对：源文 2 次、译文 1 次则仍需声明
        if src_tokens.count(token) > dst_tokens.count(token):
            if token not in waived:
                waived.append(token)
    return waived


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--batch", help="批次目录名。省略时只生成 canonical patch，不同步 map/translation")
    parser.add_argument("--fixes", help="Fixes JSON path")
    parser.add_argument("--find", help="Substring to replace across the batch's translations (with --replace)")
    parser.add_argument("--replace", default="", help="Replacement for --find")
    parser.add_argument("--find-file", help="从 UTF-8 文件读 --find（多行替换：诗节、段落重写）")
    parser.add_argument("--replace-file", help="从 UTF-8 文件读 --replace")
    parser.add_argument("--idx-list", help="With --find/--replace: restrict to these idx (whitespace/comma separated)")
    parser.add_argument("--work-root", default=".work")
    parser.add_argument("--translated-xml", help="Canonical XML for patch generation")
    parser.add_argument("--patch-out", help="Patch JSON output path")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge with an existing patch file")
    args = parser.parse_args()

    # 多行替换：命令行不适合传含换行的段落（诗节重写、段落改写），从文件读最稳。
    if args.find_file:
        if args.find is not None:
            print("error: --find 与 --find-file 只能给一个", file=sys.stderr)
            return 2
        find_path = resolve(args.find_file)
        if not find_path.is_file():
            print(f"error: --find-file 不存在: {find_path}", file=sys.stderr)
            return 2
        args.find = find_path.read_text(encoding="utf-8").rstrip("\n")
    if args.replace_file:
        if args.replace:
            print("error: --replace 与 --replace-file 只能给一个", file=sys.stderr)
            return 2
        repl_path = resolve(args.replace_file)
        if not repl_path.is_file():
            print(f"error: --replace-file 不存在: {repl_path}", file=sys.stderr)
            return 2
        args.replace = repl_path.read_text(encoding="utf-8").rstrip("\n")

    # 跨批修正模式：不给 --batch 时只产出 canonical patch（抗幻觉审查等跨批修订用）。
    # 机制上 patch 生成本就不依赖 batch——它只读 canonical 并对 idx 做 CAS——
    # 强行要求 batch 会迫使调用者把跨批修订拆成多次执行。
    if not args.batch and not ((args.fixes or args.find is not None and args.replace != "") and args.translated_xml and args.patch_out):
        print("error: 省略 --batch 时需提供 --fixes 或 --find/--replace，并同时给 --translated-xml / --patch-out", file=sys.stderr)
        return 2

    batch_dir = None
    if args.batch:
        batch_dir = resolve(args.work_root) / args.stem / "batches" / args.batch
        if not batch_dir.is_dir():
            print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
            return 2

    if not args.fixes and args.find is None:
        print("error: 需要 --fixes 或 --find/--replace", file=sys.stderr)
        return 2

    if args.find is not None:
        if batch_dir is not None:
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
            # 跨批子串替换：从 canonical 读现值。
            # 跨批修订（长文本审查、全库术语回正）需要按内容命中而不是按批次定位；
            # 不提供此路径时，调用者只能手写「读 canonical → 替换 → 拼 patch」的脚本。
            if not args.translated_xml:
                print("error: 跨批 --find/--replace 需同时提供 --translated-xml", file=sys.stderr)
                return 2
            xml_path = resolve(args.translated_xml)
            if not xml_path.is_file():
                print(f"error: --translated-xml 不存在: {xml_path}", file=sys.stderr)
                return 2
            nodes = list(ET.parse(str(xml_path)).getroot().iter("String"))
            subset = None
            if args.idx_list:
                subset = {s for s in args.idx_list.replace(",", " ").split() if s}
            # 合并语义：若 patch 中已有该 idx，以其 translation 为基准继续替换。
            # 否则同一 idx 的第二次替换会从 canonical 旧值重新出发，
            # 把第一次的修改覆盖掉（多次 --find/--replace 串联时必然遇到）。
            existing = {}
            if args.patch_out and not args.no_merge:
                existing_path = resolve(args.patch_out)
                if existing_path.is_file():
                    try:
                        existing = json.loads(existing_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        existing = {}
            gen = {}
            for i, node in enumerate(nodes):
                if subset is not None and str(i) not in subset:
                    continue
                prior = existing.get(str(i))
                base = prior.get("translation") if prior else (node.findtext("Dest") or "")
                if args.find in base and (args.replace != base):
                    gen[str(i)] = {"new": base.replace(args.find, args.replace),
                                   "notes": f"--find/--replace: {args.find!r} -> {args.replace!r}"}
            if not gen:
                print(f"no-op: canonical 内无 '{args.find}' 可替换", file=sys.stderr)
                return 0
            raw = gen
            print(f"跨批 --find/--replace 命中 {len(gen)} 条")
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
    map_path = batch_dir / "map.json" if batch_dir else None
    map_data = None
    map_applied = map_skipped = 0
    if map_path is not None and map_path.is_file():
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
    result_path = batch_dir / "translation.json" if batch_dir else None
    result_data = None
    result_applied = result_skipped = 0
    if result_path is not None and result_path.is_file():
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
                entry = {"expected_dest": current, "translation": fix["new"]}
                # 方括号内容中文化后需声明豁免，否则 writer 以占位符缺失拦截。
                # 已显式声明的以调用方为准（允许覆盖自动计算结果）。
                if "waived_tokens" in fix:
                    entry["waived_tokens"] = fix["waived_tokens"]
                else:
                    auto = detect_waived_tokens(nodes[idx].findtext("Source") or "", fix["new"])
                    if auto:
                        entry["waived_tokens"] = auto
                        warnings.append(f"patch[{idx}]: 自动豁免方括号标记 {auto}")
                patch[str(idx)] = entry

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
