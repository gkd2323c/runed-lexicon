#!/usr/bin/env python3
"""从子代理会话 JSONL 提取报告内嵌的 map JSON（救援工具）。

失败模式「报告-盘面脱节」的救援路径：子代理在最终报告文本里贴出了完整 map
JSON（围栏代码块），但从未调用 write 落盘。本工具扫描 childSessionPath 全部
assistant 文本中的围栏 JSON 块，用目标批次 index.txt 的键集**精确比对**，
命中即写出候选文件供主会话核验。

边界与纪律：
- 只读会话文件；仅向 --out 目录写候选，绝不直接写批次目录（转正由主会话经
  conflict_guard 后执行）。
- 提取出的候选与正常交付同样走验收链（verify_subagent_batch → fill → gate），
  不因"救回来的"降标准。
- 只做机械提取与键集比对，不做质量判断。

用法：
  py -3 extract_session_maps.py --session <childSessionPath.jsonl> \
      --stem KW-Kaidan --batches INFO-007 INFO-008 INFO-009 --out <dir>

  --work-root 默认 .work，index 路径为 <work-root>/<stem>/batches/<BID>/index.txt。

退出码：0 = 全部批次命中；2 = 存在未命中批次（已写出的不受影响）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.S)


def collect_blocks(session: Path) -> list[tuple[int, dict]]:
    """扫描会话所有 assistant 文本，返回 [(序号, dict)]（围栏 JSON 块）。"""
    blocks: list[tuple[int, dict]] = []
    seq = 0
    for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "message":
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or []
        texts: list[str] = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text") or "")
        elif isinstance(content, str):
            texts.append(content)
        for text in texts:
            for m in FENCE.finditer(text):
                raw = m.group(1).strip()
                if not raw.startswith("{"):
                    continue
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d:
                    seq += 1
                    blocks.append((seq, d))
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", required=True, help="子代理会话 JSONL（childSessionPath）")
    ap.add_argument("--stem", required=True, help="工作 stem（.work/<stem>/batches/...）")
    ap.add_argument("--batches", nargs="+", required=True, help="目标批次 id 列表")
    ap.add_argument("--out", required=True, help="候选文件输出目录（必须显式指定）")
    ap.add_argument("--work-root", default=".work", help="工作根目录（默认 .work）")
    args = ap.parse_args()

    session = Path(args.session)
    if not session.is_file():
        print(f"error: 会话文件不存在: {session}", file=sys.stderr)
        return 2

    blocks = collect_blocks(session)
    print(f"扫描到 {len(blocks)} 个围栏 JSON 块（dict 类型）")
    if not blocks:
        print("error: 会话中未找到任何 JSON 块", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    failed = False
    for bid in args.batches:
        idx_path = Path(args.work_root) / args.stem / "batches" / bid / "index.txt"
        if not idx_path.is_file():
            print(f"{bid}: !! index 文件不存在: {idx_path}")
            failed = True
            continue
        idx_set = {l.strip() for l in idx_path.read_text(encoding="utf-8").splitlines() if l.strip()}

        hits = [(n, d) for n, d in blocks if set(d.keys()) == idx_set]
        if hits:
            n, d = hits[-1]  # 取最后一次出现（报告可能多轮迭代）
            dest = out / f"{bid}.map.json"
            dest.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{bid}: 命中块#{n}，keys={len(d)} == idx {len(idx_set)}，已写出 -> {dest}")
        else:
            best = max(blocks, key=lambda nd: len(set(nd[1].keys()) & idx_set))
            inter = len(set(best[1].keys()) & idx_set)
            print(f"{bid}: MISS（最接近块#{best[0]} 交集 {inter}/{len(idx_set)}）")
            failed = True

    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
