#!/usr/bin/env python3
"""conflict_guard.py 冒烟测试（合成目录，不碰真实项目文件）。

语义（2026-09-21 警告模式重设计）：
- 默认警告模式：信号全部输出为 WARN、exit 0（不阻断）——工具摆信号，操作者裁决；
- --strict：出现任一信号即 BLOCKED、exit 1。

Run: py -3 .agents/skills/subagent-ops/scripts/test_conflict_guard.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "conflict_guard.py"


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8"
    )


def msg(role: str, text: str) -> str:
    return json.dumps({"type": "message",
                       "message": {"role": role,
                                   "content": [{"type": "text", "text": text}]}},
                      ensure_ascii=False) + "\n"


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="conflict-guard-test-"))
    try:
        work = tmp / "work"
        sessions = tmp / "sessions"
        sessions.mkdir(parents=True)

        now = time.time()
        old = now - 7200  # 2 小时前

        b_ok = work / "T" / "batches" / "B-OK"          # 干净：无文件
        b_has = work / "T" / "batches" / "B-HAS"        # 已有旧 map.json
        b_fresh = work / "T" / "batches" / "B-FRESH"    # 已有新 map.json
        b_act = work / "T" / "batches" / "B-ACT"        # 无文件，当前指派提及
        b_inc = work / "T" / "batches" / "B-INC"        # 无文件，仅顺带提及（不应触发）
        for d in (b_ok, b_has, b_fresh, b_act, b_inc):
            d.mkdir(parents=True)

        draft = {"1": {"translation": "测试", "status": "TRANSLATED", "confidence": "HIGH", "notes": ""}}
        for b in (b_has, b_fresh):
            p = b / "map.json"
            p.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        os.utime(b_has / "map.json", (old, old))
        # b_fresh 保持 now（新）

        # 会话文件
        # fresh_act: 当前指派提及 B-ACT / B-FRESH
        (sessions / "fresh_act.jsonl").write_text(
            msg("user", "任务：翻译 B-ACT 与 B-FRESH 两批") + msg("assistant", "开工"), encoding="utf-8")
        # fresh_incidental: 当前指派是 B-NONE，但历史/顺带文本里出现 B-INC（不应触发）
        (sessions / "fresh_incidental.jsonl").write_text(
            msg("user", "任务：翻译 B-NONE")
            + msg("assistant", "顺带一提，之前有人聊过 B-INC 这个批次号"), encoding="utf-8")
        # old_act: 旧文件（不入窗）
        (sessions / "old_act.jsonl").write_text(
            msg("user", "任务：翻译 B-HAS"), encoding="utf-8")
        os.utime(sessions / "old_act.jsonl", (old, old))

        common = ["--work-root", str(work), "--sessions-dir", str(sessions),
                  "--activity-window", "30"]

        # 1. rescue + 干净批 → CLEAR（rc=0）
        res = run("--stem", "T", "--batches", "B-OK", *common)
        if res.returncode != 0 or "CLEAR" not in res.stdout:
            failures.append(f"case1 clean should be CLEAR: rc={res.returncode} out={res.stdout[:200]}")

        # 2. rescue + 已有旧 map.json：默认警告不阻断；--strict 才硬拦
        res = run("--stem", "T", "--batches", "B-HAS", *common)
        if res.returncode != 0 or "WARN" not in res.stdout or "已存在" not in res.stdout:
            failures.append(f"case2a existing map should WARN but pass: rc={res.returncode} out={res.stdout[:240]}")
        res = run("--stem", "T", "--batches", "B-HAS", "--strict", *common)
        if res.returncode != 1 or "BLOCKED（strict）" not in res.stdout:
            failures.append(f"case2b existing map strict should BLOCK: rc={res.returncode} out={res.stdout[:240]}")

        # 3. rescue + 当前指派提及：默认警告不阻断；--strict 硬拦
        res = run("--stem", "T", "--batches", "B-ACT", *common)
        if res.returncode != 0 or "当前指派提及" not in res.stdout or "WARN" not in res.stdout:
            failures.append(f"case3a assignment mention should WARN but pass: rc={res.returncode} out={res.stdout[:240]}")
        res = run("--stem", "T", "--batches", "B-ACT", "--strict", *common)
        if res.returncode != 1 or "BLOCKED（strict）" not in res.stdout:
            failures.append(f"case3b assignment mention strict should BLOCK: rc={res.returncode} out={res.stdout[:240]}")

        # 4. rescue + 新 map.json：默认警告不阻断；--strict 硬拦
        res = run("--stem", "T", "--batches", "B-FRESH", *common)
        if res.returncode != 0 or "WARN" not in res.stdout:
            failures.append(f"case4 fresh map should WARN but pass: rc={res.returncode} out={res.stdout[:240]}")

        # 5. amend + 旧 map.json（无近期活动）→ CLEAR rc=0（amend 模式放行）
        res = run("--stem", "T", "--batches", "B-HAS", "--mode", "amend", *common)
        if res.returncode != 0 or "amend 模式放行" not in res.stdout:
            failures.append(f"case5 amend old map should pass: rc={res.returncode} out={res.stdout[:240]}")

        # 6. amend + 新 map.json → 默认警告不阻断；--strict 硬拦
        res = run("--stem", "T", "--batches", "B-FRESH", "--mode", "amend", *common)
        if res.returncode != 0 or "近期被写入" not in res.stdout or "不阻断写盘" not in res.stdout:
            failures.append(f"case6a amend fresh map should WARN but pass: rc={res.returncode} out={res.stdout[:240]}")
        res = run("--stem", "T", "--batches", "B-FRESH", "--mode", "amend", "--strict", *common)
        if res.returncode != 1 or "BLOCKED（strict）" not in res.stdout:
            failures.append(f"case6b amend fresh map strict should BLOCK: rc={res.returncode} out={res.stdout[:240]}")

        # 7. 会话目录缺失 + 干净批 → CLEAR（带跳过注记）
        res = run("--stem", "T", "--batches", "B-OK",
                  "--work-root", str(work), "--sessions-dir", str(tmp / "nonexistent"),
                  "--activity-window", "30")
        if res.returncode != 0 or "已跳过" not in res.stdout:
            failures.append(f"case7 missing sessions dir should CLEAR with note: rc={res.returncode} out={res.stdout[:240]}")

        # 8. 多批混合（干净+提及）：默认警告 rc=0；--strict rc=1
        res = run("--stem", "T", "--batches", "B-OK", "B-ACT", *common)
        if res.returncode != 0 or "WARN" not in res.stdout:
            failures.append(f"case8a mixed should WARN but pass: rc={res.returncode}")
        res = run("--stem", "T", "--batches", "B-OK", "B-ACT", "--strict", *common)
        if res.returncode != 1:
            failures.append(f"case8b mixed strict should BLOCK: rc={res.returncode}")

        # 9. 精度：仅顺带提及（当前指派无关）→ 完全无信号 CLEAR
        res = run("--stem", "T", "--batches", "B-INC", *common)
        if res.returncode != 0 or "CLEAR" not in res.stdout or "WARN" in res.stdout:
            failures.append(f"case9 incidental mention should NOT trigger: rc={res.returncode} out={res.stdout[:240]}")

        # 10. 警告模式提示语：核实后可继续、无需等待窗口
        res = run("--stem", "T", "--batches", "B-FRESH", "--mode", "amend", *common)
        if "无需等待窗口过期" not in res.stdout:
            failures.append(f"case10 warn mode should note no-wait: out={res.stdout[:240]}")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("conflict guard smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
