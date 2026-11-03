#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次级 Protect：送模型前掩码行内运行时 token，收卷后按位还原并校验。

为什么在子代理边界外做
----------------------
子代理读的是 `batches/<BID>/context.json` 里的 `xml.source`，而那个字段同时是
写回基准——不能就地改写。因此掩码与还原都发生在子代理两侧：

    context.json --mask--> masked.json --子代理翻译--> {index: 译文}
                                                          |
                     map.json <--restore + 校验------------+

`fill` / `verify` / `write` 看到的始终是还原后的干净译文，整条下游链路不知道
发生过掩码，因此不存在「掩码路线与保留路线同批混用」的校验口径冲突。

为什么是全量掩码，不区分「是不是引擎标签」
------------------------------------------
项目裁决：不该翻的 token 被翻坏会破坏 MOD（硬伤），漏翻只是文本层面（软伤）。
两个方向的代价不对等，所以掩码是全量的——`<stagger or knock down>`、`[Intimidate]`
这类其实可译的片段同样进掩码、译文里原样保留，是否需要补译由后续审查环节判断，
不在本工具里做可译性判断（那是 `KNOWN_ANGLE` / GRAY 二审的职责，不是掩码层的）。

输出角色（登记于 SKILL.md 「Protect 接入」一节）
-----------------------------------------------
- `.work/<stem>/batches/<BID>/masked.json`     给子代理读的掩码输入（不含原文 token）
- `.work/<stem>/batches/<BID>/mask-state.json` 还原用哨兵映射，**不给子代理**
- `.work/<stem>/batches/<BID>/map.json`        restore 产出，供 consume 消费

用法
----
    # 派单前：context.json -> masked.json（只读 context，写 .work/ 下两个文件）
    py -3 .agents/skills/translation-batch-ops/scripts/protect_batch.py mask \
        --stem <S> --batch <BID> [--sentinel-form opaque|typed]

    # 收卷后：子代理译文 -> 还原 + 哨兵校验 -> map.json
    py -3 .agents/skills/translation-batch-ops/scripts/protect_batch.py restore \
        --stem <S> --batch <BID> --translations <raw.json> [--report <path>] [--check-order]

`--translations` 接受 `{"<index>": "译文"}` 或 `{"<index>": {"translation": "..."}}`。

退出码：0 无硬错误 / 1 有硬错误（哨兵多重集不等、缺 masked 输入）/ 2 用法或 IO 错误。
漏翻（translations 缺某个 index）报为 finding、该行不进 map.json，但**不算硬错误**：
按项目裁决，漏翻是文本层面的软伤，不该阻断收口。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS_DIR = HERE.parents[1]          # .agents/skills

sys.path.insert(0, str(SKILLS_DIR / "translation-protect" / "scripts"))
import protect_text as protect  # noqa: E402


# 项目裁决的扩展文法：在 protect 默认文法之上把方括号标签与 XML 实体也纳入掩码。
#
# 为什么不能直接用 protect 的默认文法：它的默认光将除 [pagebreak] 以外的方括号划入
# GRAY——标记待二审但**不掩码**，于是 `[Intimidate]` 仍可能被模型改写成 `[威吓]`。
# 项目裁决是「不该翻的被翻坏会破坏 MOD（硬伤），漏翻只是文本层面（软伤）」，两个方向
# 代价不对等，所以掩码必须全量：可译性判断（这个片段到底要不要翻）不进掩码层，交给
# 后续审查环节。GRAY 标记仍然保留，供二审定位。
# 类型字母：T=尖括号 P=printf B=花括号 K=分页符 R=方括号标签 X=XML 实体 E=转义 N=换行。
# K 必须排在 R 之前：`[pagebreak]` 同时命中两个模式且长度相同，同起点同长度时
# `_find_hits` 保留先出现的那个。
BATCH_INLINE = [
    {"type": "T", "pattern": r"<[^<>\r\n]+>"},
    {"type": "P", "pattern": r"%(?:\d+\$)?[-+#0']*\d*(?:\.\d+)?[diuoxXfFeEgGaAcspn%]"},
    {"type": "B", "pattern": r"\{[A-Za-z0-9_][A-Za-z0-9_.:=|+\- ]*\}"},
    {"type": "K", "pattern": r"\[pagebreak\]"},
    {"type": "R", "pattern": r"\[[A-Za-z][^\]\r\n]*\]"},
    {"type": "X", "pattern": r"&(?:[A-Za-z][A-Za-z0-9]*|#\d+|#x[0-9A-Fa-f]+);"},
    {"type": "E", "pattern": r"\\[nrt\\]"},
    {"type": "N", "pattern": r"\r\n|\n"},
]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def batch_dir(stem: str, bid: str, work_root: str) -> Path:
    return Path(work_root) / stem / "batches" / bid


def units_from_context(context: dict) -> list[dict]:
    """从 context.json 抽 (index, unit_id, source)。context 可含多批。"""
    units: list[dict] = []
    for batch in context.get("batches") or []:
        for entry in batch.get("entries") or []:
            xml = entry.get("xml") or {}
            source = xml.get("source")
            if not isinstance(source, str) or not source.strip():
                continue
            index = xml.get("index")
            if index is None:
                continue
            units.append({
                "index": int(index),
                "unit_id": entry.get("translation_unit_id") or "",
                "source": source,
            })
    return units


def cmd_mask(args: argparse.Namespace) -> int:
    out_dir = batch_dir(args.stem, args.batch, args.work_root)
    context_path = out_dir / "context.json"
    if not context_path.is_file():
        print(f"error: 缺 context.json（备料未完成？）: {context_path}", file=sys.stderr)
        return 2

    units = units_from_context(load_json(context_path))
    if not units:
        print(f"error: context 里没有可掩码的条目: {context_path}", file=sys.stderr)
        return 2

    giving: list[dict] = []      # 给子代理的输入
    state: list[dict] = []       # 还原用状态，不给子代理
    counts = {"MASK": 0, "EXCLUDE": 0, "GRAY": 0}

    for unit in units:
        result = protect.mask_unit(unit["source"], args.sentinel_form,
                                   inline=BATCH_INLINE)
        bucket = result["bucket"]
        counts[bucket] = counts.get(bucket, 0) + 1

        state.append({
            "index": unit["index"],
            "unit_id": unit["unit_id"],
            "bucket": bucket,
            "masked": result["masked"],
            "tokens": result["tokens"],
            "gray_reasons": result["gray_reasons"],
            "source": unit["source"],
        })

        if bucket == "EXCLUDE":
            # 整条即代码标识符：不进模型，restore 阶段按 KEEP 直接落回原文。
            giving.append({
                "index": unit["index"],
                "unit_id": unit["unit_id"],
                "bucket": "EXCLUDE",
                "source_masked": None,
                "do_not_translate": True,
            })
        else:
            giving.append({
                "index": unit["index"],
                "unit_id": unit["unit_id"],
                "bucket": bucket,
                "source_masked": result["masked"],
                "gray_reasons": result["gray_reasons"],
            })

    masked_doc = {
        "schema_version": 1,
        "stem": args.stem,
        "batch": args.batch,
        "sentinel_form": args.sentinel_form,
        "note": (
            "行内运行时 token 已替换为哨兵符（形如 ⟪0⟫、⟪T0⟫）。"
            "译文里原样保留这些哨兵符——数量、位置、形态都不许改；"
            "不要把它还原成文本、不要新增或删除、不要用它替换词。"
            "bucket=EXCLUDE 的条目整条是代码标识符，source_masked 为 null，不产出译文。"
            "译文按 {\"<index>\": \"译文\"} 输出，index 原样抄。"
        ),
        "unit_count": len(giving),
        "units": giving,
    }
    state_doc = {"schema_version": 1, "stem": args.stem, "batch": args.batch,
                 "sentinel_form": args.sentinel_form, "units": state}

    dump_json(out_dir / "masked.json", masked_doc)
    dump_json(out_dir / "mask-state.json", state_doc)

    print(f"mask: {len(units)} units -> MASK {counts['MASK']} / "
          f"EXCLUDE {counts['EXCLUDE']} / GRAY {counts['GRAY']}")
    print(f"  -> {out_dir / 'masked.json'}（子代理读这个）")
    print(f"  -> {out_dir / 'mask-state.json'}（还原用，不给子代理）")
    gray = [u for u in state if u["gray_reasons"]]
    if gray:
        print(f"  GRAY {len(gray)} 条待二审（仍已掩码，二审判它是不是真 token）：")
        for u in gray[:8]:
            print(f"    [{u['index']}] {','.join(u['gray_reasons'])} | {u['source'][:48]!r}")
        if len(gray) > 8:
            print(f"    ... 另 {len(gray) - 8} 条见 mask-state.json")
    return 0


def _normalize_translations(raw) -> dict[int, str]:
    """接受 {index: str} 或 {index: {translation: str}}。"""
    out: dict[int, str] = {}
    for key, value in (raw or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            text = value.get("translation")
        else:
            text = None
        if isinstance(text, str):
            out[index] = text
    return out


def cmd_restore(args: argparse.Namespace) -> int:
    out_dir = batch_dir(args.stem, args.batch, args.work_root)
    state_path = out_dir / "mask-state.json"
    masked_path = out_dir / "masked.json"
    if not state_path.is_file():
        print(f"error: 缺 mask-state.json（先跑 mask）: {state_path}", file=sys.stderr)
        return 2

    state = {int(u["index"]): u for u in load_json(state_path)["units"]}
    if not masked_path.is_file():
        print(f"error: 缺 masked.json（先跑 mask）: {masked_path}", file=sys.stderr)
        return 2

    translations_path = Path(args.translations)
    if not translations_path.is_file():
        print(f"error: translations 不存在: {translations_path}", file=sys.stderr)
        return 2
    translated = _normalize_translations(load_json(translations_path))

    result_map: dict[str, dict] = {}
    hard: list[dict] = []
    findings: list[dict] = []

    for index, unit in sorted(state.items()):
        if unit["bucket"] == "EXCLUDE":
            # 整条即代码标识符：不进模型，按 KEEP 落回原文（与 protect 的 EXCLUDE 语义一致）。
            result_map[str(index)] = {"translation": unit["source"], "status": "KEEP",
                                      "confidence": "HIGH", "notes": "protect:exclude"}
            continue
        if index not in translated:
            findings.append({"index": index, "kind": "missing_translation"})
            continue
        dest = translated[index]
        problems = protect.verify_unit(unit["masked"] or "", dest, args.check_order)
        if problems:
            for problem in problems:
                hard.append({"index": index, **problem})
            continue
        result_map[str(index)] = {
            "translation": protect.restore_unit(unit["masked"] or "", dest, unit["tokens"]),
            "status": "TRANSLATED",
            "confidence": "HIGH",
        }

    dump_json(out_dir / "map.json", result_map)
    report = {"schema_version": 1, "stem": args.stem, "batch": args.batch,
              "ok": not hard, "units": len(state), "written": len(result_map),
              "hard": hard, "findings": findings}
    if args.report:
        dump_json(Path(args.report), report)

    kept = sum(1 for u in state.values() if u["bucket"] == "EXCLUDE")
    print(f"restore: {len(state)} units -> map.json {len(result_map)} 条"
          f"（EXCLUDE 按 KEEP 落回 {kept} 条，漏翻 {len(findings)} 条）")
    print(f"  -> {out_dir / 'map.json'}")
    if findings:
        print(f"  漏翻 {len(findings)} 条（不进 map.json，不算硬错误）：")
        for f in findings[:8]:
            print(f"    [{f['index']}] {state[f['index']]['source'][:48]!r}")
        if len(findings) > 8:
            print(f"    ... 另 {len(findings) - 8} 条")
    if hard:
        print(f"  硬错误 {len(hard)} 条（哨兵不守恒，译文不得使用）：")
        for h in hard[:10]:
            detail = h.get("missing") or h.get("extra") or ""
            print(f"    [{h['index']}] {h['kind']} {detail}")
        if len(hard) > 10:
            print(f"    ... 另 {len(hard) - 10} 条")
        return 1
    print("  哨兵守恒：全部通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mask", help="context.json -> masked.json（派单前）")
    m.add_argument("--stem", required=True)
    m.add_argument("--batch", required=True)
    m.add_argument("--work-root", default=".work")
    m.add_argument("--sentinel-form", choices=["opaque", "typed"], default="opaque")
    m.set_defaults(func=cmd_mask)

    r = sub.add_parser("restore", help="子代理译文 -> 还原 + 校验 -> map.json（收卷后）")
    r.add_argument("--stem", required=True)
    r.add_argument("--batch", required=True)
    r.add_argument("--work-root", default=".work")
    r.add_argument("--translations", required=True)
    r.add_argument("--report", default=None)
    r.add_argument("--check-order", action="store_true",
                   help="把哨兵符顺序漂移也判为硬错误（排版标记场景；句内 token 默认可重排）")
    r.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
