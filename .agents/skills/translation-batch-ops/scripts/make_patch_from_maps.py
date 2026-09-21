# -*- coding: utf-8 -*-
"""make_patch_from_maps.py — 把审查修正 map 合并成 canonical patch JSON。

使用场景（见 xtranslator-xml-writer / translation-batch-ops）：
对**已写回 canonical** 的条目做审查修正时，用 result 模式重放会触发增量模式的
`original_dest` 软保护（`original_dest mismatch`）而整批拒写；必须改用 `--patch`，
其 `expected_dest` 取 canonical 的当前 Dest。

本脚本读 canonical 现值，为每条修正补上 `expected_dest` / `source`，产出可直接被
`write_translations.py --patch` 消费的补丁文件。

用法:
  py -3 .agents/skills/translation-batch-ops/scripts/make_patch_from_maps.py <map.json> [<map.json> ...]
  py -3 .../make_patch_from_maps.py .work/<mod>/maps/<mod>-fix-map-blockA.json
  py -3 .../make_patch_from_maps.py --canonical <xml> --out <patch.json> <map.json> ...

规范:
- 输入 map 为扁平 JSON：{ "<xml_index>": { "translation": ..., 可选 status/confidence/notes } }
- 键是 **xml_index**（ElementTree `findall('.//String')` 的 0-based 序号），不是文件行号
- 同一 idx 在多个 map 中重复出现且译文不同 → 报错退出，不静默取后者
- 输出 patch 条目形如 { "expected_dest": <canonical 现值>, "translation": <新译文>, "source": <源文> }

只读 canonical；只写 `--out` 指定的补丁文件。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def _load_batch_sync():
    """同目录动态加载 batch_sync（不依赖 sys.path；与 apply_fixes 同模式）。"""
    spec = importlib.util.spec_from_file_location("_batch_sync", HERE / "batch_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取 map {path}: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="由审查修正 map 生成 canonical patch（只读 XML）")
    ap.add_argument("maps", nargs="+", help="一个或多个修正 map.json")
    ap.add_argument(
        "--canonical",
        default=None,
        help="canonical 译文 XML；默认取第一个 map 路径中的 .work/<mod>/maps/ 上推到 mods/<mod>/<mod>_english_chinese_translated.xml",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="输出 patch 路径；默认 <canonical 同级的 maps 目录>/<mod>-fix-patch.json",
    )
    ap.add_argument(
        "--no-sync-batches",
        action="store_true",
        help="默认把 patch 值同步进批次文件（map/translation）；本开关关闭该同步",
    )
    ap.add_argument(
        "--work-root",
        default=".work",
        help="批次文件所在的 .work 根（同步用；相对路径按项目根解析）",
    )
    args = ap.parse_args(argv)

    first_map = Path(args.maps[0]).resolve()

    mod_stem: str | None = None
    if args.canonical:
        canonical = Path(args.canonical).resolve()
    else:
        # .work/<stem>/maps/xxx.json -> mods/<目录>/<stem>_english_chinese_translated.xml
        # 目录名可能带后缀（如 TheKalpicAnomaly.esp），故按文件名前缀匹配而非拼目录名
        parts = first_map.parts
        if ".work" not in parts:
            raise SystemExit("无法从 map 路径推断 canonical，请显式传 --canonical")
        mod_stem = parts[parts.index(".work") + 1]
        stem = mod_stem
        candidates = sorted(PROJECT_ROOT.glob(f"mods/*/{stem}_english_chinese_translated.xml"))
        if not candidates:
            raise SystemExit(f"未找到 {stem}_english_chinese_translated.xml，请显式传 --canonical")
        canonical = candidates[0].resolve()

    if not canonical.is_file():
        raise SystemExit(f"canonical 不存在: {canonical}")

    entries: dict[str, dict] = {}
    for name in args.maps:
        data = load_json(Path(name).resolve())
        if not isinstance(data, dict):
            raise SystemExit(f"map 不是扁平对象: {name}")
        for key, val in data.items():
            if not isinstance(val, dict) or not isinstance(val.get("translation"), str):
                raise SystemExit(f"map {name} 的键 {key} 缺少字符串 translation")
            if key in entries and entries[key]["translation"] != val["translation"]:
                raise SystemExit(
                    f"键 {key} 在多个 map 中译文冲突："
                    f"{entries[key]['translation']!r} vs {val['translation']!r}"
                )
            entries[key] = {"translation": val["translation"]}

    strings = ET.parse(canonical).getroot().findall(".//String")
    patch: dict[str, dict] = {}
    for key in sorted(entries, key=int):
        i = int(key)
        if i < 0 or i >= len(strings):
            raise SystemExit(f"xml_index {key} 越界（canonical 共 {len(strings)} 行）")
        node = strings[i]
        patch[key] = {
            "expected_dest": node.find("Dest").text or "",
            "translation": entries[key]["translation"],
            "source": node.find("Source").text or "",
        }

    if args.out:
        out = Path(args.out).resolve()
    elif mod_stem:
        out = (PROJECT_ROOT / ".work" / mod_stem / "maps" / (mod_stem + "-fix-patch.json")).resolve()
    else:
        mod_stem = canonical.name[: -len("_english_chinese_translated.xml")]
        out = (PROJECT_ROOT / ".work" / mod_stem / "maps" / (mod_stem + "-fix-patch.json")).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {len(patch)} patch entries -> {out}")

    # ---- 批次文件同步（机制环节）----
    # canonical 修正必须回写批次文件，否则审查视图从旧值生成、后续 fill 以旧 map 覆盖。
    sync_failed = False
    if not args.no_sync_batches:
        stem_for_sync = mod_stem or canonical.name[: -len("_english_chinese_translated.xml")]
        work_root = Path(args.work_root)
        if not work_root.is_absolute():
            work_root = PROJECT_ROOT / work_root
        batches_dir = work_root / stem_for_sync / "batches"
        if batches_dir.is_dir():
            try:
                bs = _load_batch_sync()
                updates = {
                    key: {"translation": val["translation"], "expected_current": val["expected_dest"]}
                    for key, val in patch.items()
                }
                rep = bs.sync_updates(updates, batches_dir)
                print(f"批次同步: 改动 {rep['applied']}，已就位 {rep['already']}，{len(rep['batches'])} 批"
                      + (f"，不在任何批次 {len(rep['missing'])}" if rep["missing"] else ""))
                for w in rep["warnings"]:
                    print(f"  WARN {w}")
                for m in rep["mismatch"][:10]:
                    print(f"  WARN 批次现值不符 [{m['batch']}] {m['idx']} ({m['where']}): "
                          f"{m['current'][:60]!r}（仍同步为 patch 值）")
                for e in rep["errors"]:
                    print(f"  ERROR 批次同步失败: {e}", file=sys.stderr)
                if rep["errors"]:
                    sync_failed = True
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR 批次同步异常: {exc}", file=sys.stderr)
                sync_failed = True

    return 1 if sync_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
