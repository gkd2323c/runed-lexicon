#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次计划覆盖缺口扫描：报出「未译且不在任何批次计划内」的行，并可切补遗批次。

背景（结构盲区）：
- INFO 批次计划（split-info-lines/plan-info-batches）只收「链接到对话结构的 NAM1 行」，
  INFO:RNAM 与非链接 INFO 行从不进批次；
- 非 INFO 计划（plan_noninfo_batches）明确排除 INFO 前缀；
- 两条流水线之间的行没有任何计划认领，且常规覆盖率工具（check_batch_coverage
  只查计划内批次）看不到它们。
本工具把「未译行集合」与「全部计划 idx 集合」对账，让遗漏在任意时点可见。

用法：
  # 只扫描（默认）：报缺口，按 REC 分组
  py -3 scan_plan_gaps.py --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
      --plan .work/<plugin>/context/<plugin>-info-batches.json \
      --plan .work/<plugin>/context/<plugin>-noninfo-batches.json

  # 切补遗批次：把缺口写成 <stem>-gaps-batches.json + 每批 index.txt
  py -3 scan_plan_gaps.py --xml <...> --plan <...> --plan <...> --write-plan

  # 收敛声明卡点：缺口不为零时退出码 1
  py -3 scan_plan_gaps.py --xml <...> --plan <...> --fail-on-gaps

约定：
- 补遗批次 id 用 `GAP-<FAM>-NNN` 前缀：与主计划（INFO-/NI-）编号空间隔离，
  主计划重生成不会与补遗编号冲突。
- 已存在的 <stem>-gaps-batches.json 会被自动纳入「已认领」集合（重复扫描显示
  剩余缺口）；--write-plan 遇到已存在计划时拒绝覆盖（--force 强写）。
- 装批规则与 noninfo-batch-planner 一致：同源不拆、贪心装批、族内有序、批内 idx 升序。
- 本工具只读 MOD XML；--write-plan 只写 .work/ 下的计划与批次索引。

输出角色登记：
- 补遗计划 `.work/<stem>/context/<stem>-gaps-batches.json`（新角色）
- 批次索引 `.work/<stem>/batches/<GAP-batch>/index.txt`（既有角色，同路径约定）

退出码：0 成功（发现缺口也是成功执行）；1 --fail-on-gaps 且缺口>0；2 用法/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def text_of(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def load_untranslated(xml_path: Path) -> list[dict]:
    """Un-translated rows: Source == Dest and Source non-empty."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in {xml_path}: {exc}") from exc
    rows = []
    for index, node in enumerate(root.iter("String")):
        source = text_of(node.find("Source"))
        dest = text_of(node.find("Dest"))
        if not source.strip() or source != dest:
            continue
        rec = text_of(node.find("REC"))
        rows.append({"index": index, "rec": rec, "source": source})
    return rows


def load_planned(plan_paths: list[Path]) -> tuple[set[int], list[str]]:
    planned: set[int] = set()
    labels: list[str] = []
    for path in plan_paths:
        with path.open("r", encoding="utf-8") as f:
            plan = json.load(f)
        n = 0
        for batch in plan.get("batches", []):
            for i in batch.get("idx") or []:
                if i not in planned:
                    planned.add(i)
                    n += 1
        labels.append(f"{path.name}({len(plan.get('batches', []))} batches)")
    return planned, labels


def family_slug(rec: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", rec.split(":", 1)[0]).upper() or "GEN"


def group_by_source(rows: list[dict]) -> list[tuple[str, list[int]]]:
    """(source, [idx...]) units ordered by first occurrence; rows keep XML order."""
    order: list[str] = []
    by_source: dict[str, list[int]] = {}
    for row in rows:
        src = row["source"]
        if src not in by_source:
            order.append(src)
            by_source[src] = []
        by_source[src].append(row["index"])
    return [(src, by_source[src]) for src in order]


def pack_groups(
    groups: list[tuple[str, list[int]]],
    max_unique: int,
    max_rows: int,
) -> list[dict]:
    batches = []
    cur_srcs: list[str] = []
    cur_idx: list[int] = []

    def flush() -> None:
        nonlocal cur_srcs, cur_idx
        if cur_srcs:
            batches.append({"srcs": cur_srcs, "idx": sorted(cur_idx)})
        cur_srcs, cur_idx = [], []

    for src, idxs in groups:
        would_unique = len(cur_srcs) + 1
        would_rows = len(cur_idx) + len(idxs)
        if cur_srcs and (would_unique > max_unique or would_rows > max_rows):
            flush()
        cur_srcs.append(src)
        cur_idx.extend(idxs)
    flush()
    return batches


def build_batches(gap_rows: list[dict], max_unique: int, max_rows: int) -> list[dict]:
    """Group gap rows by REC prefix family; pack each family into GAP batches."""
    families: dict[str, list[dict]] = {}
    for row in gap_rows:
        families.setdefault(row["rec"].split(":", 1)[0], []).append(row)

    batches: list[dict] = []
    for family in sorted(families):
        rows = sorted(families[family], key=lambda r: r["index"])
        groups = group_by_source(rows)
        packed = pack_groups(groups, max_unique, max_rows)
        slug = family_slug(family)
        for seq, batch in enumerate(packed, start=1):
            batches.append({
                "id": f"GAP-{slug}-{seq:03d}",
                "line": family,
                "recs": sorted({r["rec"] for r in rows if r["index"] in set(batch["idx"])}),
                "unique_src": len(batch["srcs"]),
                "row_count": len(batch["idx"]),
                "srcs": batch["srcs"],
                "idx": batch["idx"],
            })
    return batches


def write_plan(
    stem: str,
    work_root: Path,
    xml_path: Path,
    plan_labels: list[str],
    batches: list[dict],
    max_unique: int,
    max_rows: int,
) -> Path:
    plan_path = work_root / stem / "context" / f"{stem}-gaps-batches.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    # Refuse to touch batch dirs that already have an active deliverable.
    for batch in batches:
        bdir = work_root / stem / "batches" / batch["id"]
        if (bdir / "map.json").is_file():
            raise ValueError(
                f"batch dir already has map.json (active work): {bdir}; "
                f"refusing to rewrite index.txt")

    plan = {
        "schema_version": 1,
        "source_xml": str(xml_path),
        "selection": {
            "rule": "untranslated_source_equals_dest_not_in_plans",
            "plans": plan_labels,
            "max_unique": max_unique,
            "max_rows": max_rows,
        },
        "batches": batches,
    }
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for batch in batches:
        outdir = work_root / stem / "batches" / batch["id"]
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "index.txt").write_text(
            "".join(f"{i}\n" for i in batch["idx"]), encoding="utf-8")
    return plan_path


def main() -> int:
    ap = argparse.ArgumentParser(description="批次计划覆盖缺口扫描")
    ap.add_argument("--xml", required=True,
                    help="canonical（或任意）译文 XML；以其 Source==Dest 判定未译行")
    ap.add_argument("--plan", action="append", required=True,
                    help="批次计划 JSON；可重复（info + noninfo + 其他）")
    ap.add_argument("--stem", default=None,
                    help=".work/ 下的工作目录名（默认由 XML 文件名推导）")
    ap.add_argument("--work-root", default=".work", help="工作根目录（默认 .work）")
    ap.add_argument("--write-plan", action="store_true",
                    help="把缺口切片为补遗批次并写 .work/<stem>/context/<stem>-gaps-batches.json")
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的 gaps 计划（默认为拒绝）")
    ap.add_argument("--max-unique", type=int, default=60, help="每批最大不同源文数（默认 60）")
    ap.add_argument("--max-rows", type=int, default=150, help="每批最大行数（默认 150）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    ap.add_argument("--fail-on-gaps", action="store_true",
                    help="缺口>0 时以退出码 1 结束（收敛声明卡点用）")
    args = ap.parse_args()

    try:
        xml_path = resolve_path(args.xml)
        if not xml_path.is_file():
            raise ValueError(f"XML does not exist: {xml_path}")
        stem = args.stem
        if not stem:
            name = xml_path.name
            for suffix in ("_english_chinese_translated.xml", "_english_chinese.xml"):
                if name.endswith(suffix):
                    stem = name[: -len(suffix)]
                    break
            else:
                stem = xml_path.stem
        work_root = Path(args.work_root)
        if not work_root.is_absolute():
            work_root = PROJECT_ROOT / work_root
        work_root = work_root.resolve()

        plan_paths = [resolve_path(p) for p in args.plan]
        for p in plan_paths:
            if not p.is_file():
                raise ValueError(f"plan does not exist: {p}")

        # Auto-claim: an existing gaps plan counts as planned (idempotent re-scans).
        gaps_plan_path = work_root / stem / "context" / f"{stem}-gaps-batches.json"
        claimed_by_gaps = 0
        if gaps_plan_path.is_file():
            resolved = {str(p) for p in plan_paths}
            if str(gaps_plan_path) not in resolved:
                gset, _ = load_planned([gaps_plan_path])
                claimed_by_gaps = len(gset)
                plan_paths.append(gaps_plan_path)

        planned, plan_labels = load_planned(plan_paths)
        untranslated = load_untranslated(xml_path)
        cov = [r for r in untranslated if r["index"] in planned]
        gaps = [r for r in untranslated if r["index"] not in planned]

        by_rec: dict[str, list[dict]] = {}
        for row in gaps:
            by_rec.setdefault(row["rec"] or "(none)", []).append(row)

        out = {
            "schema_version": 1,
            "xml": str(xml_path),
            "stem": stem,
            "plans": plan_labels,
            "claimed_by_gaps_plan": claimed_by_gaps,
            "total_strings": sum(1 for _ in ET.parse(str(xml_path)).getroot().iter("String")),
            "untranslated": len(untranslated),
            "covered": len(cov),
            "gap_rows": len(gaps),
            "gap_unique_sources": len({r["source"] for r in gaps}),
            "families": [
                {"rec": rec, "rows": len(rows),
                 "unique": len({r["source"] for r in rows}),
                 "idx": [r["index"] for r in rows]}
                for rec, rows in sorted(by_rec.items(), key=lambda kv: -len(kv[1]))
            ],
        }

        if args.write_plan and gaps:
            if gaps_plan_path.is_file() and not args.force:
                raise ValueError(
                    f"gaps plan already exists: {gaps_plan_path}; use --force to overwrite "
                    f"(or resolve the open batches first)")
            batches = build_batches(gaps, args.max_unique, args.max_rows)
            written = write_plan(stem, work_root, xml_path, plan_labels, batches,
                                 args.max_unique, args.max_rows)
            out["written_plan"] = str(written)
            out["written_batches"] = [
                {"id": b["id"], "unique": b["unique_src"], "rows": b["row_count"],
                 "sample": b["srcs"][0][:60]} for b in batches
            ]

        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            print(f"canonical: {xml_path} ({out['total_strings']} strings)")
            print(f"plans: {len(plan_labels)} file(s) | planned idx: {len(planned)}"
                  + (f" (+{claimed_by_gaps} claimed by gaps plan)" if claimed_by_gaps else ""))
            print(f"untranslated: {out['untranslated']} | covered: {out['covered']} "
                  f"| GAPS: {out['gap_rows']} rows / {out['gap_unique_sources']} unique sources")
            for fam in out["families"]:
                sample = next((r["source"] for r in gaps if r["rec"] == fam["rec"]), "")
                print(f"  {fam['rec']:12s} {fam['rows']:4d} rows / {fam['unique']:4d} unique"
                      f"  e.g. [{fam['idx'][0]}] {sample[:60]}")
            if out.get("written_plan"):
                print()
                print(f"gaps plan -> {out['written_plan']}")
                for b in out["written_batches"]:
                    print(f"  {b['id']}: {b['unique']} unique / {b['rows']} rows | {b['sample']}")
            if not gaps:
                print("no gaps: every untranslated row is claimed by a plan")

        if args.fail_on_gaps and gaps:
            return 1
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
