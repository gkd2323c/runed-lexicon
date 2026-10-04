#!/usr/bin/env python3
"""trace_subagent_session.py 的冒烟测试（合成 JSONL）。

Run: py -3 .agents/skills/subagent-ops/scripts/test_trace_session.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "trace_subagent_session.py"


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="trace-session-test-"))
    try:
        events = [
            {"type": "session", "id": "abc", "timestamp": "2026-09-11T15:00:00.000Z",
             "cwd": "C:\\x"},
            {"type": "message", "timestamp": "2026-09-11T15:00:01.000Z",
             "message": {"role": "user", "content": [{"type": "text", "text": "任务：翻译..."}]}},
            {"type": "message", "timestamp": "2026-09-11T15:00:05.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "thinking", "thinking": "先读背景文档，然后查先例。"},
                 {"type": "toolCall", "name": "read", "arguments": {"path": "C:\\x\\CONTEXT.md"}},
             ], "usage": {"input": 1000, "output": 100}}},
            {"type": "message", "timestamp": "2026-09-11T15:00:06.000Z",
             "message": {"role": "toolResult", "toolCallId": "1", "toolName": "read",
                         "content": [{"type": "text", "text": "# CONTEXT.md 内容"}]}},
            {"type": "message", "timestamp": "2026-09-11T15:01:00.000Z",
             "message": {"role": "assistant", "content": [],
                         "usage": {"input": 0, "output": 0}}},
        ]
        session = tmp / "session.jsonl"
        session.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                           encoding="utf-8")

        # 1. tail run
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--tail", "3"],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            failures.append(f"tail run failed: {res.stderr.strip()}")
        else:
            out = res.stdout
            if "THINK:" not in out or "TOOL: read" not in out:
                failures.append(f"tail output missing key parts: {out[:300]}")
            if "[content empty]" not in out:
                failures.append("empty-content marker missing")
            if "EMPTY-RESPONSE" in out:
                failures.append("conclusion label leaked back (EMPTY-RESPONSE must not appear)")

        # 2. head run
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--head", "2"],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or "任务：翻译" not in res.stdout:
            failures.append(f"head run failed: {res.stdout[:200]} {res.stderr.strip()}")

        # 3. grep run
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--grep", "CONTEXT"],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or "CONTEXT.md" not in res.stdout:
            failures.append(f"grep run failed: {res.stdout[:200]}")

        # 4. missing file -> rc 2
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(tmp / "nope.jsonl")],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 2:
            failures.append(f"missing-file rc expected 2, got {res.returncode}")

        # 5. full count line
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--tail", "0"],
                             capture_output=True, text=True, encoding="utf-8")
        if "5 个事件" not in res.stdout:
            failures.append(f"event count wrong: {res.stdout.splitlines()[0] if res.stdout else 'empty'}")

        # 6. raw run returns source JSONL lines verbatim
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--raw", "--tail", "1"],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or '"usage"' not in res.stdout:
            failures.append(f"raw run failed: {res.stdout[:200]}")

        # 7. width 0 keeps full thinking text
        res = subprocess.run([sys.executable, str(SCRIPT), "--path", str(session), "--tail", "3", "--width", "0"],
                             capture_output=True, text=True, encoding="utf-8")
        if "先读背景文档，然后查先例。" not in res.stdout:
            failures.append("width-0 full text missing")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("trace session smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
