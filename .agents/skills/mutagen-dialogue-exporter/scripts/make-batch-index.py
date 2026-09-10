# -*- coding: utf-8 -*-
"""make-batch-index.py — 从批次计划抽取单批次的 XML 索引清单。

translation-context-builder 的 `--index-file` 需要一份每行一个整数 XML 索引的
文本文件；本脚本从 `.work/<stem>-info-batches.json` 按批次 id 提取，落到批次目录
`.work/<stem>-batches/<BATCH-ID>/index.txt`。

用法:
  python make-batch-index.py <plugin-stem> <BATCH-ID> [batch-dir]
  例: python make-batch-index.py Artaeum INFO-001
      → 读 .work/Artaeum-info-batches.json
      → 写 .work/artaeum-batches/INFO-001/index.txt

输出契约（确定性）：批次目录与 index.txt 同名覆盖；批次 id 是稳定契约 ID。
"""
import json
import sys
from pathlib import Path

stem = sys.argv[1] if len(sys.argv) > 1 else "Druadach"
batch_id = sys.argv[2] if len(sys.argv) > 2 else "INFO-001"
batch_dir_name = sys.argv[3] if len(sys.argv) > 3 else f"{stem.lower()}-batches"

root = Path(".").resolve()
plan_path = root / f".work/{stem}-info-batches.json"

with plan_path.open("r", encoding="utf-8") as f:
    plan = json.load(f)

batch = next((b for b in plan["batches"] if b["id"] == batch_id), None)
if batch is None:
    ids = [b["id"] for b in plan["batches"][:10]]
    raise SystemExit(f"批次 {batch_id} 不存在；可用批次（前 10）：{ids}")

outdir = root / f".work/{batch_dir_name}/{batch_id}"
outdir.mkdir(parents=True, exist_ok=True)
outpath = outdir / "index.txt"
with outpath.open("w", encoding="utf-8") as f:
    for i in batch["idx"]:
        f.write(f"{i}\n")

print(f"{batch_id} [{batch['line']}] idx {len(batch['idx'])} 行 -> {outpath.relative_to(root)}")
