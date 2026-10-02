#!/usr/bin/env python3
"""读取单个翻译批次的源文/译文对照（只读）。

数据源优先级：
  1. <batch>/translation.json 的 translations[]（含 source/translation/status）
  2. <batch>/map.json + --xml（map 仅有译文，源文从 XML 取）

Usage:
  py -3 read_batch.py --stem Artaeum --batch NI-CELL-002
  py -3 read_batch.py --stem Artaeum --batch INFO-023 --status WAITING,KEEP
  py -3 read_batch.py --stem Artaeum --batch NI-CELL-002 --out _tmp/data/readout.txt
  py -3 read_batch.py --stem Artaeum --batch INFO-023 --no-digest-check
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]

# ------------------------------------------------- term-digest 新鲜度
# 为什么要有：2026-10-02 全库普查发现 585 批 digest 缺 `== 读法 ==` 图例
# （本 MOD 551 + VIGILANT 34），全部是判据型 note 机制上线前生成的。图例和
# 〔判据:…〕是同一批改动的产物，而那批改动正是为了根治整轮术语漂移
# （machinery / virtue / cause / curse / vessel）——译者看不到判据就等于
# 把分域判据藏回词表。派审查实例时手写「先重生成 digest」靠人记，
# 记一次漏一次，所以把检查塞进派单必经的 read_batch。
#
# 判定方式刻意只看「图例在不在」，不比对正文：正文每次跑都可能因为词表
# 变动而变，逐字比对会让这个检查退化成「永远陈旧」的噪声源。
DIGEST_NAME = "term-digest.md"
DIGEST_LEGEND_HEAD = "== 读法 =="
DIGEST_LEGEND_PROBE = "判据"
_DIGEST_HEAD_CHARS = 800


def digest_is_fresh(digest_path: Path) -> bool:
    """digest 是否带当前图例（图例 + 判据说明缺一不可）。"""
    try:
        head = digest_path.read_text(encoding="utf-8", errors="replace")[:_DIGEST_HEAD_CHARS]
    except OSError:
        return False
    return head.lstrip().startswith(DIGEST_LEGEND_HEAD) and DIGEST_LEGEND_PROBE in head


def ensure_fresh_digest(batch_dir: Path) -> tuple[str, str]:
    """确保 <batch>/term-digest.md 带图例；缺图例或缺失就从 context.json 重生成。

    返回 (状态, 说明)。状态取值：fresh / regenerated / skipped / no-context /
    failed。readout 本身照常产出——digest 不新鲜是警告，不是阻断，
    因为审查者宁可看带陈旧提示的读本，也不要拿不到读本。
    """
    digest_path = batch_dir / DIGEST_NAME
    context_path = batch_dir / "context.json"

    if digest_path.is_file() and digest_is_fresh(digest_path):
        return "fresh", ""
    if not context_path.is_file():
        if digest_path.is_file():
            return "no-context", f"{DIGEST_NAME} 缺图例，且无 context.json 可重生成"
        return "no-context", f"无 {DIGEST_NAME} 且无 context.json"

    # 复用 term_digest 的同一条构建路径，不在这里复写注册表发现逻辑
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import term_digest  # noqa: PLC0415
    except ImportError as exc:
        return "failed", f"无法导入 term_digest: {exc}"
    finally:
        sys.path.pop(0)

    try:
        text = term_digest.build_digest(context_path)
    except Exception as exc:  # digest 是辅助材料，构建失败不该打断读本
        return "failed", f"重生成 {DIGEST_NAME} 失败: {exc}"

    if not digest_is_fresh_text(text):
        # 生成物仍无图例（例如 term_digest 被以 --no-judge-notes 语义改过），
        # 写回只会制造下一轮「不新鲜」，不写。
        return "failed", "重生成结果仍缺图例，判定 term_digest 不可用，未覆盖原文件"

    existed = digest_path.is_file()
    digest_path.write_text(text, encoding="utf-8")
    verb = "重生成" if existed else "生成"
    return "regenerated", f"{verb} {DIGEST_NAME}（原缺图例，判据型 note 已恢复）"


def digest_is_fresh_text(text: str) -> bool:
    head = text[:_DIGEST_HEAD_CHARS]
    return head.lstrip().startswith(DIGEST_LEGEND_HEAD) and DIGEST_LEGEND_PROBE in head


def resolve(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_xml_sources(xml_path: Path) -> dict[int, str]:
    root = ET.parse(str(xml_path)).getroot()
    return {i: (node.findtext("Source") or "") for i, node in enumerate(root.iter("String"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", required=True, help="Work stem under the work root (e.g. Artaeum)")
    parser.add_argument("--batch", required=True, help="Batch id (e.g. NI-CELL-002)")
    parser.add_argument("--work-root", default=".work", help="Work root directory (default: .work)")
    parser.add_argument("--xml", help="Source XML; required only when the batch has map.json but no translation.json")
    parser.add_argument("--status", help="Only show these statuses, comma-separated (e.g. WAITING,KEEP)")
    parser.add_argument("--out", help="Write the readout to a file instead of stdout")
    parser.add_argument("--no-digest-check", action="store_true",
                        help="跳过 term-digest 新鲜度检查/重生成（默认开检查：派审查前必跑本脚本就是为拿新鲜料）")
    args = parser.parse_args()

    batch_dir = resolve(args.work_root) / args.stem / "batches" / args.batch
    if not batch_dir.is_dir():
        print(f"error: 批次目录不存在: {batch_dir}", file=sys.stderr)
        return 2

    if not args.no_digest_check:
        state, detail = ensure_fresh_digest(batch_dir)
        if state == "regenerated":
            print(f"digest: {detail}")
        elif state in ("no-context", "failed"):
            print(f"digest WARN [{state}]: {detail}", file=sys.stderr)

    rows: list[dict] = []
    result_path = batch_dir / "translation.json"
    map_path = batch_dir / "map.json"

    if result_path.is_file():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        for unit in data.get("translations", []):
            rows.append({
                "idx": unit.get("xml_index"),
                "src": unit.get("source", ""),
                "dst": unit.get("translation", ""),
                "status": unit.get("status", ""),
            })
        source_desc = "translation.json"
    elif map_path.is_file():
        if not args.xml:
            print("error: 批次只有 map.json，需要 --xml 取源文", file=sys.stderr)
            return 2
        sources = load_xml_sources(resolve(args.xml))
        data = json.loads(map_path.read_text(encoding="utf-8"))
        for key, value in data.items():
            idx = int(key)
            rows.append({
                "idx": idx,
                "src": sources.get(idx, ""),
                "dst": value.get("translation", ""),
                "status": value.get("status", ""),
            })
        source_desc = "map.json"
    else:
        print(f"error: 批次目录里既没有 translation.json 也没有 map.json: {batch_dir}", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: r["idx"])
    if args.status:
        wanted = {s.strip().upper() for s in args.status.split(",") if s.strip()}
        rows = [r for r in rows if str(r["status"]).upper() in wanted]

    lines: list[str] = []
    for row in rows:
        flag = f" [{row['status']}]" if row["status"] and row["status"] != "TRANSLATED" else ""
        lines.append(f"[{row['idx']}]{flag} {row['src']}")
        lines.append(f"  -> {row['dst']}")
    output = f"== {args.batch} ({source_desc}, {len(rows)} 条) ==\n" + "\n".join(lines) + "\n"

    if args.out:
        out_path = resolve(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {len(rows)} rows -> {out_path}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
