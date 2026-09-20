# -*- coding: utf-8 -*-
"""consume_batch.py — 翻译批次一键消费链（GAP/分片救援场景沉淀，2026-09-20）。

把「翻译产出 → 验收就绪」的机械链条收敛为一个命令：
  1. 归位：maps/<BID>-map.json（扁平 {idx: str}，翻译子代理交付形态）或
     batches/<BID>/map.json（object 形态）就位到 batches/<BID>/map.json；
  2. 展平：扁平值转 {translation, status, confidence} 写 map.filled.json；
  3. 同步：用 context + translation_result.review_reasons()/protected_tokens()
     重算 translation.json 的 immutable 字段（review_reasons / protected_tokens）；
  4. fill：fill_translations.py --force --overwrite（map 为唯一真相源，已译也覆盖）；
  5. verify：verify_subagent_batch.py 全链验收（报告落 reports/）。

设计约束（与 AGENTS.md 一致）：
- 不做任何语义判断；map 值只能是字符串译文。
- context 缺失时报错退出（immutable 同步依赖 context）。
- 幂等：重复运行产出一致。

用法：
  py -3 consume_batch.py --stem <stem> --batch <BID> [--plan <gaps|info plan>] \
      [--xml <source-xml>] [--contract <compiled.json>] [--work-root .work]

--plan 缺省自动探测：batches 目录存在 GAP-INFO- 前缀时用 <stem>-gaps-batches.json，
否则用 <stem>-info-batches.json。
退出码：0 = verify PASS；1 = 任一环失败；2 = 用法/前置缺失。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parent.parent
FILL = SKILLS / "translation-executor" / "scripts" / "fill_translations.py"
TR = SKILLS / "translation-executor" / "scripts" / "translation_result.py"
VERIFY = HERE / "verify_subagent_batch.py"


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stem", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--plan", default=None, help="批次计划 JSON；缺省自动探测 gaps/info")
    ap.add_argument("--xml", required=True, help="源 xTranslator XML（verify 需要）")
    ap.add_argument("--contract", required=True, help="编译契约 JSON（verify gate 需要）")
    ap.add_argument("--work-root", default=".work")
    args = ap.parse_args()

    root = Path(args.work_root)
    bdir = root / args.stem / "batches" / args.batch
    if not bdir.is_dir():
        return fail(f"批次目录不存在: {bdir}")
    ctx_p = bdir / "context.json"
    tp = bdir / "translation.json"
    if not ctx_p.is_file() or not tp.is_file():
        return fail("前置缺失：批次目录须已有 context.json 与 translation.json（备料产物）")

    # 计划探测（verify 需要）
    plan = args.plan
    if not plan:
        if args.batch.startswith("GAP-"):
            plan = root / args.stem / "context" / f"{args.stem}-gaps-batches.json"
        else:
            plan = root / args.stem / "context" / f"{args.stem}-info-batches.json"
        if not plan.is_file():
            return fail(f"自动探测计划失败，请显式传 --plan: {plan}")

    # 1) 归位 map：优先 batches/<BID>/map.json；否则从 maps/<BID>-map.json 复制
    bmap = bdir / "map.json"
    if not bmap.is_file():
        alt = root / args.stem / "maps" / f"{args.batch}-map.json"
        if alt.is_file():
            shutil.copyfile(alt, bmap)
            print(f"map: 从 {alt.name} 归位 -> batches/{args.batch}/map.json")
        else:
            return fail(f"翻译产出缺失：{bmap} 与 {alt} 均不存在")

    # 2) 展平为 filled（值必须为非空字符串；object 形态原样通过）
    raw = json.loads(bmap.read_text(encoding="utf-8"))
    filled = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            filled[k] = v
        elif isinstance(v, str) and v.strip():
            filled[k] = {"translation": v.strip(), "status": "TRANSLATED", "confidence": "HIGH"}
        else:
            return fail(f"map[{k!r}] 值既非对象也非非空字符串")
    fp = bdir / "map.filled.json"
    fp.write_text(json.dumps(filled, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) immutable 同步：review_reasons / protected_tokens 以 context 为唯一真相
    import importlib.util
    spec = importlib.util.spec_from_file_location("_tr", TR)
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    ctx = json.loads(ctx_p.read_text(encoding="utf-8"))
    entries = ctx.get("batches") or []
    if len(entries) != 1:
        return fail(f"context 应为单批结构（batch_size=0 备料），当前 {len(entries)} 批；请先用 "
                    "build_translation_context --batch-size 0 重建")
    exp = {e["translation_unit_id"]: e for e in entries[0]["entries"]}
    t = json.loads(tp.read_text(encoding="utf-8"))
    for u in t.get("translations", []):
        e = exp.get(u.get("translation_unit_id"))
        if e is None:
            return fail(f"translation.json 含未知 unit: {u.get('translation_unit_id')}")
        u["review_reasons"] = tr.review_reasons(e)
        u["protected_tokens"] = tr.protected_tokens(str(e["xml"]["source"]))
    tp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"immutable 同步: review_reasons/protected_tokens <- context（{len(t['translations'])} units）")

    # 4) fill（--force --overwrite：map 为唯一真相源）
    r = subprocess.run(
        [sys.executable, str(FILL), "--result", str(tp), "--map", str(fp),
         "--output", str(tp), "--force", "--overwrite"],
        capture_output=True, text=True, encoding="utf-8")
    tail = (r.stdout or r.stderr).strip().splitlines()
    print("fill:", tail[-1] if tail else "(无输出)")
    if r.returncode != 0:
        return 1

    # 5) verify
    r = subprocess.run(
        [sys.executable, str(VERIFY), "--plan", str(plan), "--batch", args.batch,
         "--map", str(fp), "--xml", args.xml, "--result", str(tp),
         "--context", str(ctx_p), "--contract", args.contract],
        capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or r.stderr).strip().splitlines()
    for line in out[-3:]:
        print(line)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
