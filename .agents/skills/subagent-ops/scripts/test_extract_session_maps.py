#!/usr/bin/env python3
"""extract_session_maps.py 的冒烟测试（合成 JSONL + 合成 index）。

Run: py -3 .agents/skills/subagent-ops/scripts/test_extract_session_maps.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "extract_session_maps.py"


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="extract-maps-test-"))
    try:
        # 合成批次索引：B1 键 {1,2}；B2 键 {3,4}；B3 键 {9}（会话里无对应块）
        for bid, keys in [("B1", ["1", "2"]), ("B2", ["3", "4"]), ("B3", ["9"])]:
            d = tmp / "work" / "T" / "batches" / bid
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.txt").write_text("\n".join(keys) + "\n", encoding="utf-8")

        # 合成会话：先一个错的块（键 5,6），再 B1 块、B2 块；另有一个非 JSON 围栏
        good_b1 = {"1": {"translation": "甲", "status": "TRANSLATED"}, "2": {"translation": "乙", "status": "TRANSLATED"}}
        good_b2 = {"3": {"translation": "丙", "status": "TRANSLATED"}, "4": {"translation": "丁", "status": "TRANSLATED"}}
        decoy = {"5": {"translation": "戊", "status": "TRANSLATED"}, "6": {"translation": "己", "status": "TRANSLATED"}}

        def ev(role: str, text: str, usage: dict | None = None) -> dict:
            msg = {"role": role, "content": [{"type": "text", "text": text}]}
            if usage is not None:
                msg["usage"] = usage
            return {"type": "message", "timestamp": "2026-09-11T15:00:00.000Z", "message": msg}

        report_text = (
            "报告如下：\n\n```json\n" + json.dumps(decoy, ensure_ascii=False) + "\n```\n\n"
            "```json\n" + json.dumps(good_b1, ensure_ascii=False) + "\n```\n\n"
            "```text\n这不是 JSON\n```\n\n"
            "```json\n" + json.dumps(good_b2, ensure_ascii=False) + "\n```\n"
        )
        events = [
            {"type": "session", "id": "x", "timestamp": "2026-09-11T15:00:00.000Z"},
            ev("user", "任务：翻译……"),
            ev("assistant", report_text, {"input": 100, "output": 200}),
        ]
        session = tmp / "session.jsonl"
        session.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                           encoding="utf-8")

        out = tmp / "out"

        # 1. B1+B2 命中，退出码 0
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--session", str(session), "--stem", "T",
             "--batches", "B1", "B2", "--out", str(out), "--work-root", str(tmp / "work")],
            capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            failures.append(f"hit run rc={res.returncode}: {res.stderr.strip()} {res.stdout[:300]}")
        for bid, expect in [("B1", good_b1), ("B2", good_b2)]:
            f = out / f"{bid}.map.json"
            if not f.is_file():
                failures.append(f"{bid} candidate missing")
                continue
            got = json.loads(f.read_text(encoding="utf-8"))
            if got != expect:
                failures.append(f"{bid} content mismatch")

        # 2. B3 无对应块 -> 退出码 2 且不写文件
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--session", str(session), "--stem", "T",
             "--batches", "B3", "--out", str(out), "--work-root", str(tmp / "work")],
            capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 2:
            failures.append(f"miss run rc expected 2, got {res.returncode}")
        if "MISS" not in res.stdout:
            failures.append("miss run missing MISS marker")

        # 3. 会话文件不存在 -> 退出码 2
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--session", str(tmp / "nope.jsonl"), "--stem", "T",
             "--batches", "B1", "--out", str(out), "--work-root", str(tmp / "work")],
            capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 2:
            failures.append(f"missing-session rc expected 2, got {res.returncode}")

        # 4. 多轮迭代取最后一块（同一批次两次出现，后者为准）
        revised_b1 = {"1": {"translation": "甲改", "status": "TRANSLATED"}, "2": {"translation": "乙改", "status": "TRANSLATED"}}
        events2 = events + [ev("assistant", "```json\n" + json.dumps(revised_b1, ensure_ascii=False) + "\n```", {"input": 1, "output": 1})]
        session2 = tmp / "session2.jsonl"
        session2.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events2) + "\n",
                            encoding="utf-8")
        out2 = tmp / "out2"
        res = subprocess.run(
            [sys.executable, str(SCRIPT), "--session", str(session2), "--stem", "T",
             "--batches", "B1", "--out", str(out2), "--work-root", str(tmp / "work")],
            capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            failures.append(f"revised run rc={res.returncode}: {res.stdout[:200]}")
        else:
            got = json.loads((out2 / "B1.map.json").read_text(encoding="utf-8"))
            if got != revised_b1:
                failures.append("revised run did not pick the last block")

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("extract session maps smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
