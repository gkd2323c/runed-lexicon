# -*- coding: utf-8 -*-
"""rebuild_context.py — 批次 context 重建 + translation.json 骨架重生成（单批模式）。

背景（2026-09-20 GAP 战役沉淀）：备料 context 必须用 --batch-size 0（单批全量）。
多批结构（batch-size=1 逐条拆批）会让 translation_result.validate 只认 batch_index=0
那一批，其余全部报 unknown translation_unit_id。本命令把「重建 context → 重生成
translation.json 骨架（保留已有译文与 immutable 同步）」收敛为一个命令。

行为：
  1. build_translation_context.py --batch-size 0 重建 context.json（--force）；
  2. 按新 context 重生成 translation.json 骨架；
  3. 已有 translation.json 中的译文（status=TRANSLATED 且 translation 非空）按
     xml_index 保留；immutable 字段一律以新 context 重算（translation_result 原函数）。

用法：
  py -3 rebuild_context.py --stem <stem> --batch <BID> [--mod <mods dir name>] \
      [--work-root .work]

退出码：0 成功；2 用法/前置缺失。
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parent.parent
BUILDER = SKILLS / "translation-context-builder" / "scripts" / "build_translation_context.py"
TR = SKILLS / "translation-executor" / "scripts" / "translation_result.py"

DEFAULT_XML = "mods/{mod}/{stem}_english_chinese.xml"
DEFAULT_DC = "{work}/{stem}/context/{stem}-dialogue-context.json"
DEFAULT_TERMS = "mods/{mod}/terms.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stem", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--mod", default=None, help="mods/ 下目录名；缺省取 --stem 同名")
    ap.add_argument("--xml", default=None, help="源 XML；缺省 mods/<mod>/<mod>_english_chinese.xml")
    ap.add_argument("--mod-terms", default=None, help="terms.json；缺省 mods/<mod>/terms.json")
    ap.add_argument("--work-root", default=".work")
    args = ap.parse_args()

    mod = args.mod or args.stem
    # mods 目录名常带 .esp 后缀而 stem 不带：缺省时两种目录名都试
    candidates = [mod, f"{mod}.esp", mod.removesuffix(".esp")]
    mod_dir = next((m for m in candidates if (Path("mods") / m).is_dir()), mod)
    xml = args.xml or DEFAULT_XML.format(mod=mod_dir, stem=args.stem)
    terms = args.mod_terms or DEFAULT_TERMS.format(mod=mod_dir)
    work = Path(args.work_root)
    stem = args.stem
    dc = DEFAULT_DC.format(work=work, stem=stem)

    bdir = work / stem / "batches" / args.batch
    idx_p = bdir / "index.txt"
    if not idx_p.is_file():
        return fail(f"index.txt 缺失: {idx_p}")
    for p in (xml, terms, dc):
        if not Path(p).is_file():
            return fail(f"输入缺失: {p}")

    # 1) 重建 context（单批全量）
    ctx_out = bdir / "context.json"
    r = subprocess.run(
        [sys.executable, str(BUILDER), "--xml", xml, "--dialogue-context", str(dc),
         "--mod-terms", str(terms), "--index-file", str(idx_p), "--batch-size", "0",
         "--output", str(ctx_out), "--force"],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print((r.stderr or r.stdout)[-400:], file=sys.stderr)
        return 1
    ctx = json.loads(ctx_out.read_text(encoding="utf-8"))
    entries = ctx.get("batches") or []
    if len(entries) != 1:
        return fail(f"重建后的 context 仍非单批（{len(entries)} 批），builder 行为异常")

    # 2) 旧译文保留（按 xml_index）
    tp = bdir / "translation.json"
    old = {}
    if tp.is_file():
        try:
            for u in json.loads(tp.read_text(encoding="utf-8")).get("translations", []):
                if u.get("status") == "TRANSLATED" and (u.get("translation") or "").strip():
                    old[u["xml_index"]] = u
        except json.JSONDecodeError:
            print("warning: 旧 translation.json 解析失败，按无译文处理", file=sys.stderr)

    # 3) 骨架重生成 + immutable 同步（translation_result 原函数）
    import importlib.util
    spec = importlib.util.spec_from_file_location("_tr", TR)
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)

    translations = []
    restored = 0
    for e in entries[0]["entries"]:
        x = e["xml"]
        prev = old.get(x["index"])
        u = {
            "translation_unit_id": e["translation_unit_id"],
            "xml_index": x["index"],
            "edid": x["edid"],
            "rec": x["rec"],
            "source": x["source"],
            "original_dest": x["dest"],
            "protected_tokens": tr.protected_tokens(str(x["source"])),
            "review_reasons": tr.review_reasons(e),
            "translation": "",
            "status": "PENDING",
            "confidence": "",
            "notes": "",
            "terminology_decisions": [],
        }
        if prev:
            u["translation"] = prev["translation"]
            u["status"] = "TRANSLATED"
            u["confidence"] = prev.get("confidence") or "HIGH"
            u["notes"] = prev.get("notes", "")
            restored += 1
        translations.append(u)

    prov_path = f"{work}/{stem}/batches/{args.batch}/context.json".replace("\\", "/")
    payload = {
        "schema_version": 1,
        "purpose": "agent_translation_draft_no_xml_writeback",
        "context": {
            "path": prov_path,
            "sha256": sha(ctx_out),
            "batch_index": 0,
            "xtranslator_xml": {"path": xml.replace("/", "\\"), "sha256": sha(Path(xml))},
            "mod_terms": {"path": terms.replace("/", "\\"), "sha256": sha(Path(terms))},
        },
        "translations": translations,
    }
    tp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    total = len(translations)
    print(f"{args.batch}: context 单批重建 {total} units；译文保留 {restored}，PENDING {total - restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
