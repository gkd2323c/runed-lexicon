#!/usr/bin/env python3
"""子代理会话追踪：读取 childSessionPath 的 JSONL，返回会话原始内容（只读）。

用途：子代理 failed/aborted 时按纪律「第一步读会话文件归因」。本工具只做机械还原，
不下判断、不贴结论标签：内容原样呈现（默认宽度截断；--width 0 全文不截断；
--raw 逐行原文直出）。归因由读者根据原始内容自行得出，不采信任何摘要或标签。

用法：
  py -3 trace_subagent_session.py --path <childSessionPath.jsonl>
  py -3 trace_subagent_session.py --path <...jsonl> --tail 30
  py -3 trace_subagent_session.py --path <...jsonl> --grep "落盘|error" --tail 50
  py -3 trace_subagent_session.py --path <...jsonl> --head 10     # 看开局
  py -3 trace_subagent_session.py --path <...jsonl> --width 0 --tail 5   # 不截断
  py -3 trace_subagent_session.py --path <...jsonl> --raw --tail 5       # 原文直出

输出：默认每行 [时间] 类型 角色 [in/out tokens]，缩进为内容（宽度截断）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


def shorten(text: str, width: int) -> str:
    text = str(text)
    if width <= 0:
        return text
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[:width] + "…"


def brief_event(obj: dict, width: int) -> list[str]:
    ts = (obj.get("timestamp", "") or "")[11:19]
    etype = obj.get("type", "?")
    lines: list[str] = []
    if etype != "message":
        detail = {k: v for k, v in obj.items() if k not in ("type", "id", "parentId", "timestamp")}
        if detail:
            lines.append(f"[{ts}] {etype} {shorten(json.dumps(detail, ensure_ascii=False), width)}")
        return lines

    msg = obj.get("message", {}) or {}
    role = msg.get("role", "")
    content = msg.get("content", [])
    usage = msg.get("usage") or {}
    token_info = ""
    if usage:
        token_info = f" [in={usage.get('input')} out={usage.get('output')}]"

    parts: list[str] = []
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            ct = c.get("type")
            if ct == "text":
                parts.append("TEXT: " + shorten(c.get("text") or "", width))
            elif ct == "thinking":
                parts.append("THINK: " + shorten(c.get("thinking") or "", width))
            elif ct == "toolCall":
                args = c.get("arguments", {})
                arg_brief = ""
                if isinstance(args, dict):
                    for key in ("path", "file_path", "cmd", "command", "text", "pattern"):
                        if key in args:
                            arg_brief = shorten(str(args[key]).replace("\n", " "), width - 40)
                            break
                parts.append(f"TOOL: {c.get('name')} {arg_brief}")
            elif ct == "toolResult":
                tc = c.get("content")
                txt = ""
                if isinstance(tc, list):
                    for item in tc[:2]:
                        if isinstance(item, dict):
                            txt += str(item.get("text", "")) + " "
                        else:
                            txt += str(item) + " "
                elif isinstance(tc, str):
                    txt = tc
                parts.append(f"RESULT[{c.get('toolName')}]: " + shorten(txt, width))
    elif isinstance(content, str):
        parts.append("STR: " + shorten(content, width))

    if not parts:
        parts.append("[content empty]")

    header = f"[{ts}] {etype} {role}{token_info}"
    lines.append(header)
    for part in parts:
        lines.append("    " + part)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="子代理会话 JSONL 路径（childSessionPath）")
    parser.add_argument("--tail", type=int, default=20, help="显示末尾 N 个事件（默认 20；0=全部）")
    parser.add_argument("--head", type=int, default=0, help="改为显示开头 N 个事件")
    parser.add_argument("--grep", help="只显示匹配此正则的事件（含前后 0 行）")
    parser.add_argument("--width", type=int, default=160, help="内容截断宽度（默认 160；0=不截断全文）")
    parser.add_argument("--raw", action="store_true", help="原文直出：逐行返回 JSONL 原始内容（不解析、不截断、不加任何标记）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"error: 会话文件不存在: {path}", file=sys.stderr)
        return 2

    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    if args.raw:
        sel = raw_lines
        if args.grep:
            try:
                pattern = re.compile(args.grep)
            except re.error as exc:
                print(f"error: 非法正则: {exc}", file=sys.stderr)
                return 2
            sel = [l for l in raw_lines if pattern.search(l)]
        elif args.head and args.head > 0:
            sel = raw_lines[: args.head]
        elif args.tail and args.tail > 0:
            sel = raw_lines[-args.tail :]
        for l in sel:
            print(l)
        return 0

    events: list[dict] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "unparsable", "raw": line[:120]})

    age_min = (time.time() - path.stat().st_mtime) / 60
    print(f"== {path.name}：{len(events)} 个事件｜文件最后修改 {age_min:.1f} 分钟前 ==")
    if args.grep:
        try:
            pattern = re.compile(args.grep)
        except re.error as exc:
            print(f"error: 非法正则: {exc}", file=sys.stderr)
            return 2
        selected = [
            e for e in events
            if pattern.search(json.dumps(e, ensure_ascii=False))
        ]
    elif args.head and args.head > 0:
        selected = events[: args.head]
    elif args.tail and args.tail > 0:
        selected = events[-args.tail :]
    else:
        selected = events

    for obj in selected:
        if obj.get("type") == "unparsable":
            print(f"  [unparsable] {obj.get('raw')}")
            continue
        for line in brief_event(obj, args.width):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
