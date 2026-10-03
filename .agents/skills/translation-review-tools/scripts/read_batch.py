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


def contract_staleness_note(context_path: Path, stem: str, work_root: str) -> str:
    """批 context.json 比编译契约旧时，给出一行告警。

    digest 的 MOD 行**不是现算的**，它逐字来自 context.json 里烘焙好的
    `mod_terms_hits`——那是翻译期由 context-builder 写下的。所以此后新增的词条
    对**所有更早备料的批次一律不可见**，`MOD: -` 不代表「词表里没有这个词」。

    事故锚定（TheKalpicAnomaly_GLENMORIL 2026-10-03，RN-INFO-033）：`Artaeum→阿塔姆`
    早在 10-02 就立了 CONFIRMED/REQUIRED 词条（`kalpic.glen.artaeum`），但该批
    context.json 是 09-30 建的，digest 在 2916 行如实报 `MOD: -`，子代理据此
    报「词表缺口」并顺带质疑字首——主会话跑 `adjudicate.py contract Artaeum` 才
    看到词条一直都在。**报「词表缺口」前必须先 adjudicate 复核，别只看 DICTIONARY.md。**

    这条告警只提示、不阻断：重建全库 790 批 context 的代价远高于它省下的事。
    """
    contract = PROJECT_ROOT / work_root / stem / "contracts" / f"{stem}.compiled.json"
    try:
        c_mt = contract.stat().st_mtime
        x_mt = context_path.stat().st_mtime
    except OSError:
        return ""
    if x_mt >= c_mt:
        return ""
    gap = c_mt - x_mt
    if gap >= 86400:
        age = f"{int(gap // 86400)} 天"
    elif gap >= 3600:
        age = f"{int(gap // 3600)} 小时"
    else:
        age = f"{int(gap // 60)} 分钟"
    return (f"  ⚠ 本批 context.json 比编译契约旧 {age}（{_ts(x_mt)} < {_ts(c_mt)}）："
            f"MOD 行只含**建批当时**已存在的词条，此后新增的词条对本批一律不可见。"
            f"报「词表缺口」前先跑 "
            f"`py -3 .agents/skills/same-source-convergence/scripts/adjudicate.py "
            f"--stem {stem} contract <词>` 复核——`MOD: -` 不等于词表里没有。")


def _ts(mtime: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def ensure_fresh_digest(batch_dir: Path, stem: str = "", work_root: str = ".work") -> tuple[str, str]:
    """确保 <batch>/term-digest.md 带图例；缺图例或缺失就从 context.json 重生成。

    返回 (状态, 说明)。状态取值：fresh / regenerated / skipped / no-context /
    failed。readout 本身照常产出——digest 不新鲜是警告，不是阻断，
    因为审查者宁可看带陈旧提示的读本，也不要拿不到读本。
    """
    digest_path = batch_dir / DIGEST_NAME
    context_path = batch_dir / "context.json"
    stale = contract_staleness_note(context_path, stem, work_root) if stem else ""

    if digest_path.is_file() and digest_is_fresh(digest_path):
        if stale and stale not in digest_path.read_text(encoding="utf-8", errors="replace")[:4096]:
            text = digest_path.read_text(encoding="utf-8", errors="replace")
            digest_path.write_text(_inject_stale_note(text, stale), encoding="utf-8")
            return "fresh", f"已注入契约陈旧告警到 {DIGEST_NAME}{stale}"
        return "fresh", (stale or "")
    if not context_path.is_file():
        if digest_path.is_file():
            return "no-context", f"{DIGEST_NAME} 缺图例，且无 context.json 可重生成{stale}"
        return "no-context", f"无 {DIGEST_NAME} 且无 context.json{stale}"

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
    if stale:
        text = _inject_stale_note(text, stale)
    digest_path.write_text(text, encoding="utf-8")
    verb = "重生成" if existed else "生成"
    return "regenerated", f"{verb} {DIGEST_NAME}（原缺图例，判据型 note 已恢复）{stale}"


def _inject_stale_note(text: str, note: str) -> str:
    """把陈旧告警插进图例之后、批次数据之前，保证子代理一打开就读到。

    图例首行必须保持在最前（`digest_is_fresh_text` 靠它判新鲜），所以不前置整段。
    """
    if note.strip() and note.strip() in text:
        return text
    lines = text.splitlines(keepends=True)
    # 图例块结束于第一个空行；把告警放在其后
    for i, line in enumerate(lines):
        if not line.strip():
            lines.insert(i, note.rstrip() + "\n")
            return "".join(lines)
    lines.append("\n" + note.rstrip() + "\n")
    return "".join(lines)


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
        state, detail = ensure_fresh_digest(batch_dir, args.stem, args.work_root)
        if state == "regenerated":
            print(f"digest: {detail}")
        elif state == "fresh" and detail:
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
