#!/usr/bin/env python3
"""批次产出写前冲突守卫（只读）。

主会话在「救援生成 / 覆盖批次产出（map.json / translation.json）」或对产出做
修正性写盘之前，跑本脚本机械检查三类冲突信号：

  1. map.json 已有产出：rescue 模式下提示（map.json 只会由写者产出，存在即
     说明可能已有写者交付）；amend 模式下放行已有产出。
  2. 近期写入：目标 map.json 在活动窗口内被写入（可能有活动写者或刚交付）。
  3. 近期活动：有子代理会话文件在窗口内被修改，且其**当前指派（最后一条 user
     消息）**提及目标批次（实例可能仍在运行或刚交卷；只认当前指派，避免被顺带
     提及误伤）。

任务（作业）判定：会话文件解析失败时回退为全文子串保守判定。

输出与退出码（工具摆信号，操作者做裁决）：
  - 默认（警告模式）：信号全部输出为 WARN、exit 0（不阻断）；由调用方按输出的
    核实步骤调查后自行决定是否写盘。子代理交卷落盘必然命中窗口信号，属正常
    现象——硬拦会阻塞最正常的消费流程。
  - --strict：出现任一信号即输出 BLOCKED、exit 1（供自动化 fail-fast 场景）。

设计背景：失败通知可能滞后或错位送达（run1 的失败通知曾晚于 run2 交卷到达），
按通知直接写盘曾覆盖过已交付的 map.json。本守卫把「写前必须核实」从纪律变成
提醒；但所有信号均为启发式（不存在「确定冲突」可判），故默认不替操作者阻断
流程。

用法：
  py -3 conflict_guard.py --stem Artaeum --batches NI-MGEF-002 NI-SPEL-001
  py -3 conflict_guard.py --stem Artaeum --batches NI-CELL-001 --mode amend
  py -3 conflict_guard.py --stem Artaeum --batches NI-X --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _default_sessions_dir() -> Path:
    """子代理会话目录默认值：环境变量 SUBAGENT_SESSIONS_DIR 优先，
    未设置时回退到当前运行平台约定的用户目录会话路径。"""
    env = os.environ.get("SUBAGENT_SESSIONS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".hanako" / "agents" / "hanako" / "subagent-sessions" / "direct"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--work-root", default=".work")
    parser.add_argument("--mode", choices=("rescue", "amend"), default="rescue",
                        help="rescue=救援/新建写盘（已有 map.json 即阻断，默认）；"
                             "amend=修正已交付产出（已有产出放行，近期写入仍拦截）")
    parser.add_argument("--activity-window", type=int, default=15,
                        help="活动窗口（分钟，默认 15）")
    parser.add_argument("--strict", action="store_true",
                        help="硬拦模式：出现任一信号即 BLOCKED / exit 1（默认警告模式不阻断）")
    parser.add_argument("--sessions-dir", default=None,
                        help="子代理会话目录（默认读环境变量 SUBAGENT_SESSIONS_DIR，"
                             "未设置时用当前平台的默认会话路径）")
    args = parser.parse_args()

    work_root = Path(args.work_root)
    if not work_root.is_absolute():
        work_root = Path.cwd() / work_root
    sessions_dir = (Path(args.sessions_dir) if args.sessions_dir
                    else _default_sessions_dir())

    now = time.time()
    window_min = args.activity_window
    warnings: list[str] = []
    notes: list[str] = []

    mode_label = "strict" if args.strict else "警告模式"
    print(f"== 批产出写前冲突守卫（mode={args.mode}，活动窗口 {window_min} 分钟，{mode_label}）==")

    # 1) 目标文件检查
    for batch in args.batches:
        batch_dir = work_root / args.stem / "batches" / batch
        if not batch_dir.is_dir():
            notes.append(f"{batch}: 批次目录不存在（{batch_dir}）")
            continue
        for fname in ("map.json", "translation.json"):
            f = batch_dir / fname
            if not f.is_file():
                continue
            age_min = (now - f.stat().st_mtime) / 60
            info = f"{age_min:.1f} 分钟前写入，{f.stat().st_size}B，sha {sha8(f)}"
            if fname == "map.json":
                if args.mode == "rescue":
                    warnings.append(
                        f"{batch}: map.json 已存在（{info}）——map.json 只由写者产出，"
                        f"存在即说明可能已有交付；先核实（见下方步骤）")
                elif age_min <= window_min:
                    warnings.append(
                        f"{batch}: map.json 近期被写入（{info}）——可能有活动写者")
                else:
                    notes.append(f"{batch}: 已有 map.json（{info}），amend 模式放行")
            else:
                if age_min <= window_min:
                    notes.append(
                        f"{batch}: translation.json 近期被写入（{info}）"
                        f"——若刚跑过 repair/回填属正常")
                else:
                    notes.append(
                        f"{batch}: 已有 translation.json（{info}，备料产物属正常）")

    # 2) 会话活动扫描
    def current_assignment(sf: Path) -> str | None:
        """提取会话文件中最后一条 user 消息的文本（当前指派）。
        解析失败时返回 None（调用方回退全文子串判定）。"""
        last = None
        try:
            for line in sf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "message":
                    continue
                msg = obj.get("message") or {}
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text")
                if text.strip():
                    last = text
        except OSError:
            return None
        return last

    if sessions_dir.is_dir():
        scanned = 0
        hits = 0
        for sf in sorted(sessions_dir.glob("*.jsonl")):
            try:
                age = now - sf.stat().st_mtime
            except OSError:
                continue
            if age > window_min * 60:
                continue
            scanned += 1
            assignment = current_assignment(sf)
            if assignment is None:
                # 解析失败 → 保守回退全文子串
                try:
                    matched = [b for b in args.batches
                               if b in sf.read_text(encoding="utf-8", errors="replace")]
                except OSError:
                    matched = []
            else:
                matched = [b for b in args.batches if b in assignment]
            if matched:
                hits += 1
                warnings.append(
                    f"近期子代理活动：{sf.name}（{age / 60:.1f} 分钟前修改）"
                    f"当前指派提及 {matched}——实例可能仍在运行或刚交卷")
        if hits == 0:
            notes.append(f"活动扫描：窗口内 {scanned} 个会话文件，无当前指派指向目标批次者")
    else:
        notes.append(f"活动扫描：会话目录不存在，已跳过（{sessions_dir}）")

    # 3) 裁决（默认警告模式不阻断；--strict 时硬拦）
    for n in notes:
        print(f"  · {n}")
    if warnings:
        print()
        if args.strict:
            print(f"BLOCKED（strict）：存在 {len(warnings)} 个待核实信号，禁止写盘。")
        else:
            print(f"WARN：发现 {len(warnings)} 个待核实信号（警告模式，不阻断写盘）。")
        for w in warnings:
            print(f"  ! {w}")
        print()
        print("写盘前核实步骤（确认无活跃写者后即可继续）：")
        print("  1. current_status get subagents ——核对相关实例终态；")
        print("     失败通知可能滞后或错位（run1 通知可能晚于 run2 交卷），"
              "不得单独作为写盘依据。")
        print("  2. 读已有产出（若存在）——核对键集合与内容，判断是否已是完整交付。")
        print("  3. 和解——采纳 / 逐条合并差异；确需覆盖时须在报告中记录"
              "对被覆盖版本的比对结论。")
        if args.strict:
            return 1
        print()
        print("（警告模式：核实后直接继续写盘，无需等待窗口过期。）")
        return 0
    print()
    print("CLEAR：未发现冲突信号，可安全写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
