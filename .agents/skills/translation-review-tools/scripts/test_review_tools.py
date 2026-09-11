#!/usr/bin/env python3
"""Permanent regression tests for the translation review tools (read_batch / query / apply_fixes).

Synthetic mini-XML and work directories only; never touches real project files.
Run: py -3 .agents/skills/translation-review-tools/scripts/test_review_tools.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
READ_BATCH = HERE / "read_batch.py"
QUERY = HERE / "query.py"
APPLY = HERE / "apply_fixes.py"
TERM_DIGEST = HERE / "term_digest.py"


def build_xml(rows) -> str:
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<SSTXMLRessources version="2">',
        "<Params><Addon>t.esp</Addon><Source>english</Source><Dest>chinese</Dest></Params>",
        "<Content>",
    ]
    for row in rows:
        parts.append(
            '<String List="0">'
            f'<EDID>{row.get("edid", "[X]")}</EDID><REC>{row["rec"]}</REC>'
            f'<Source>{row["source"]}</Source><Dest>{row.get("dest", row["source"])}</Dest>'
            "</String>"
        )
    parts.append("</Content>")
    parts.append("</SSTXMLRessources>")
    return "\n".join(parts) + "\n"


def run(script: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *argv], capture_output=True, text=True, encoding="utf-8"
    )


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="review-tools-test-"))
    try:
        work = tmp / "work"
        batch_dir = work / "T" / "batches" / "B1"
        batch_dir.mkdir(parents=True)
        (work / "T" / "context").mkdir(parents=True)

        xml_path = tmp / "t_english_chinese.xml"
        xml_path.write_text(build_xml([
            {"rec": "CELL:FULL", "source": "Psijic Pint", "edid": "[A]"},
            {"rec": "NPC_:FULL", "source": "Thrall Forest", "edid": "[B]", "dest": "斯罗尔森林"},
            {"rec": "NPC_:FULL", "source": "Hello", "edid": "[C]", "dest": "你好"},
        ]), encoding="utf-8")

        (batch_dir / "index.txt").write_text("0\n1\n", encoding="utf-8")
        (batch_dir / "map.json").write_text(json.dumps({
            "0": {"translation": "赛伊克品脱酒馆", "status": "TRANSLATED", "confidence": "HIGH", "notes": ""},
            "1": {"translation": "斯罗尔森林", "status": "TRANSLATED", "confidence": "HIGH", "notes": ""},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (batch_dir / "translation.json").write_text(json.dumps({
            "schema_version": 1,
            "translations": [
                {"translation_unit_id": "u:0", "xml_index": 0, "source": "Psijic Pint",
                 "translation": "赛伊克品脱酒馆", "status": "TRANSLATED", "confidence": "HIGH", "notes": ""},
                {"translation_unit_id": "u:1", "xml_index": 1, "source": "Thrall Forest",
                 "translation": "斯罗尔森林", "status": "TRANSLATED", "confidence": "HIGH", "notes": ""},
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (work / "T" / "context" / "T-batches.json").write_text(json.dumps({
            "batches": [{"id": "B1", "idx": [0, 1]}],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---------- read_batch ----------
        res = run(READ_BATCH, "--stem", "T", "--batch", "B1", "--work-root", str(work))
        if res.returncode != 0 or "赛伊克品脱酒馆" not in res.stdout or "斯罗尔森林" not in res.stdout:
            failures.append(f"read_batch basic failed: rc={res.returncode} out={res.stdout[:200]}")

        res = run(READ_BATCH, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--status", "WAITING")
        if res.returncode != 0 or "0 条" not in res.stdout:
            failures.append(f"read_batch status filter failed: {res.stdout[:200]}")

        # map-only mode needs --xml
        (batch_dir / "translation.json").rename(batch_dir / "translation.json.bak")
        res = run(READ_BATCH, "--stem", "T", "--batch", "B1", "--work-root", str(work))
        if res.returncode != 2:
            failures.append(f"read_batch map-only without --xml should fail: rc={res.returncode}")
        res = run(READ_BATCH, "--stem", "T", "--batch", "B1", "--work-root", str(work), "--xml", str(xml_path))
        if res.returncode != 0 or "Thrall Forest" not in res.stdout:
            failures.append(f"read_batch map-only failed: {res.stdout[:200]}")
        (batch_dir / "translation.json.bak").rename(batch_dir / "translation.json")

        # ---------- query ----------
        res = run(QUERY, "--xml", str(xml_path), "--src", "thrall")
        if res.returncode != 0 or "[1]" not in res.stdout:
            failures.append(f"query src failed: {res.stdout[:200]}")

        res = run(QUERY, "--xml", str(xml_path), "--dst", "斯罗尔")
        if res.returncode != 0 or "斯罗尔森林" not in res.stdout:
            failures.append(f"query dst failed: {res.stdout[:200]}")

        res = run(QUERY, "--xml", str(xml_path), "--idx", "1", "--context", "1")
        if res.returncode != 0 or "[0]" not in res.stdout or "[2]" not in res.stdout:
            failures.append(f"query idx context failed: {res.stdout[:200]}")

        res = run(QUERY, "--locate", "1", "--plans", str(work / "T" / "context" / "*.json"))
        if res.returncode != 0 or "B1" not in res.stdout:
            failures.append(f"query locate failed: {res.stdout[:200]}")

        # ---------- apply_fixes ----------
        fixes = tmp / "fix1.json"
        fixes.write_text(json.dumps({
            "0": {"new": "赛伊克酒馆", "expected_current": "赛伊克品脱酒馆"},
        }, ensure_ascii=False), encoding="utf-8")
        patch_out = work / "T" / "maps" / "fix-patch.json"
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--fixes", str(fixes),
                  "--translated-xml", str(xml_path), "--patch-out", str(patch_out))
        if res.returncode != 0:
            failures.append(f"apply_fixes failed: rc={res.returncode} err={res.stderr[:200]}")
        else:
            m = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
            if m["0"]["translation"] != "赛伊克酒馆":
                failures.append(f"apply_fixes map not updated: {m['0']}")
            t = json.loads((batch_dir / "translation.json").read_text(encoding="utf-8"))
            if t["translations"][0]["translation"] != "赛伊克酒馆":
                failures.append("apply_fixes result not updated")
            p = json.loads(patch_out.read_text(encoding="utf-8"))
            if "0" not in p or p["0"]["expected_dest"] != "Psijic Pint":
                failures.append(f"apply_fixes patch wrong: {p}")

        # expected_current mismatch -> exit 2, nothing written
        fixes_bad = tmp / "fix-bad.json"
        fixes_bad.write_text(json.dumps({
            "1": {"new": "XX", "expected_current": "wrong-value"},
        }, ensure_ascii=False), encoding="utf-8")
        before = (batch_dir / "map.json").read_text(encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work), "--fixes", str(fixes_bad))
        if res.returncode != 2 or (batch_dir / "map.json").read_text(encoding="utf-8") != before:
            failures.append(f"apply_fixes mismatch guard failed: rc={res.returncode}")

        # KEEP -> TRANSLATED auto-conversion
        m = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        m["1"]["status"] = "KEEP"
        (batch_dir / "map.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        fixes_keep = tmp / "fix-keep.json"
        fixes_keep.write_text(json.dumps({
            "1": {"new": "斯罗尔森林（改）"},
        }, ensure_ascii=False), encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work), "--fixes", str(fixes_keep))
        m2 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        if res.returncode != 0 or m2["1"]["status"] != "TRANSLATED":
            failures.append(f"apply_fixes KEEP conversion failed: {m2['1']}")

        # idempotent: re-apply same fix -> skip, patch skipped (already current)
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--fixes", str(fixes_keep),
                  "--translated-xml", str(xml_path), "--patch-out", str(patch_out))
        if res.returncode != 0 or "跳过" not in res.stdout:
            failures.append(f"apply_fixes idempotent failed: {res.stdout[:200]}")

        # status-only fix (no `new`): change status, keep translation
        m3 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        m3["0"]["status"] = "REVIEW"
        (batch_dir / "map.json").write_text(json.dumps(m3, ensure_ascii=False, indent=2), encoding="utf-8")
        fixes_st = tmp / "fix-status.json"
        fixes_st.write_text(json.dumps({"0": {"status": "TRANSLATED"}}, ensure_ascii=False), encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work), "--fixes", str(fixes_st))
        m4 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        if res.returncode != 0 or m4["0"]["status"] != "TRANSLATED":
            failures.append(f"apply_fixes status-only failed: {m4['0']}")
        if m4["0"]["translation"] != m3["0"]["translation"]:
            failures.append(f"apply_fixes status-only changed translation: {m4['0']}")

        # ---------- query --anchors ----------
        anch = tmp / "anchors.txt"
        anch.write_text("Thrall\nHello\n", encoding="utf-8")
        res = run(QUERY, "--xml", str(xml_path), "--anchors", str(anch))
        if res.returncode != 0 or "[Thrall]" not in res.stdout or "[Hello]" not in res.stdout:
            failures.append(f"query --anchors failed: {res.stdout[:200]}")
        if "斯罗尔森林" not in res.stdout:
            failures.append("query --anchors 未报出全库形态")

        # ---------- term_digest ----------
        ctx = tmp / "ctx.json"
        ctx.write_text(json.dumps({
            "batches": [{
                "batch_index": 0,
                "entries": [
                    {"xml": {"index": 5, "rec": "NPC_:FULL",
                             "source": "Gynthar the Pillar", "duplicate_count": 2},
                     "terminology": {
                         "mod_terms_hits": [{"english": "Pillar", "chinese": "柱石", "status": "PROVISIONAL"}],
                         "official_dictionary_hits": [{"source": "Pillar", "dest": "柱", "rec": "ACTI:FULL"}]}},
                    {"xml": {"index": 6, "rec": "NPC_:FULL",
                             "source": "Lythee", "duplicate_count": 1},
                     "terminology": {"mod_terms_hits": [], "official_dictionary_hits": []}},
                    {"xml": {"index": 7, "rec": "DIAL:FULL", "source": "So what are you proposing."},
                     "dialogue_context": {"status": "matched", "dialogue": {
                         "quest_edid": "SummersetRebQ07", "category": "Topic",
                         "topic": "So what are you proposing."}},
                     "terminology": {"mod_terms_hits": [], "official_dictionary_hits": []}},
                ],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        res = run(TERM_DIGEST, "--context", str(ctx))
        digest_out = res.stdout
        for needle, label in [
            ("[5] NPC_:FULL | Gynthar the Pillar | dup=2", "head+dup"),
            ("Pillar→柱石 (PROVISIONAL)", "mod fmt"),
            ("Pillar=柱[ACTI:FULL]", "off fmt"),
            ("CTX: quest=SummersetRebQ07", "ctx line"),
            ("== batch 0 (3 entries) ==", "batch header"),
        ]:
            if res.returncode != 0 or needle not in digest_out:
                failures.append(f"term_digest {label} missing: {needle!r} in {digest_out[:300]!r}")
        if "dup=1" in digest_out:
            failures.append("term_digest printed dup=1")
        if digest_out.count("MOD: -") < 2:
            failures.append("term_digest empty hits not marked '-'")
        if "dialogue_context" in digest_out or "duplicate_count" in digest_out:
            failures.append("term_digest leaked raw field name")
        outfile = tmp / "digest.txt"
        res = run(TERM_DIGEST, "--context", str(ctx), "--out", str(outfile))
        if res.returncode != 0 or "Gynthar" not in outfile.read_text(encoding="utf-8"):
            failures.append("term_digest --out failed")

        # ---------- term_digest: spell-name registry ----------
        reg_md = tmp / "x-spell-name-registry.md"
        reg_md.write_text(
            "| Source (X) | 统一译名 (Y) |\n| --- | --- |\n"
            "| Throwdoll | 击倒术 |\n| Sacrifice | 祭品 |\n| Charge | 充能术 |\n",
            encoding="utf-8")
        # 上下文源文包含 Charge（普通词）与 Throwdoll
        ctx2 = tmp / "ctx2.json"
        ctx2.write_text(json.dumps({
            "batches": [{"batch_index": 0, "entries": [
                {"xml": {"index": 1, "rec": "BOOK:DESC", "source": "Spell Tome: Throwdoll"},
                 "terminology": {"mod_terms_hits": [], "official_dictionary_hits": []}},
                {"xml": {"index": 2, "rec": "BOOK:DESC", "source": "Spell Tome: Charge"},
                 "terminology": {"mod_terms_hits": [], "official_dictionary_hits": []}},
            ]}],
        }, ensure_ascii=False), encoding="utf-8")
        res = run(TERM_DIGEST, "--context", str(ctx2), "--spell-registry", str(reg_md))
        out2 = res.stdout
        if res.returncode != 0 or "法术名统一表" not in out2:
            failures.append(f"term_digest registry section missing: {out2[:200]!r}")
        if "击倒术" not in out2 or "祭品" not in out2:
            failures.append("term_digest registry entries missing")
        # 命中项应标 *
        hit_lines = [ln for ln in out2.splitlines() if ln.startswith("  * ")]
        if not any("Throwdoll" in ln for ln in hit_lines):
            failures.append(f"term_digest registry hit-mark missing: {hit_lines!r}")
        # 未命中项不应标 *
        non_hit = [ln for ln in out2.splitlines() if ln.strip().startswith("Sacrifice")]
        if any(ln.startswith("  *") for ln in non_hit):
            failures.append("term_digest registry over-marked non-hit entry")
        # 缺文件时静默不报错
        res = run(TERM_DIGEST, "--context", str(ctx2),
                  "--spell-registry", str(tmp / "nope.md"))
        if res.returncode != 0:
            failures.append("term_digest missing registry should be non-fatal")

        # ---------- apply_fixes --find/--replace ----------
        # 注意：apply_fixes 的 CAS 同时校验 map.json 与 translation.json，
        # 测试构造必须保持两者同步（真实流程由 fill 保证同步）。
        m5 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        m5["0"]["translation"] = "赛伊克品脱酒馆"
        m5["1"]["translation"] = "斯罗尔森林"
        (batch_dir / "map.json").write_text(json.dumps(m5, ensure_ascii=False, indent=2), encoding="utf-8")
        t5 = json.loads((batch_dir / "translation.json").read_text(encoding="utf-8"))
        for u in t5["translations"]:
            if u["xml_index"] == 0:
                u["translation"] = "赛伊克品脱酒馆"
            elif u["xml_index"] == 1:
                u["translation"] = "斯罗尔森林"
        (batch_dir / "translation.json").write_text(json.dumps(t5, ensure_ascii=False, indent=2), encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--find", "品脱", "--replace", "烈酒")
        m6 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        if res.returncode != 0 or m6["0"]["translation"] != "赛伊克烈酒酒馆":
            failures.append(f"apply_fixes --find/--replace failed: {m6['0']} / {res.stdout[:120]}")
        # map 与 translation 两侧都应用
        t6 = json.loads((batch_dir / "translation.json").read_text(encoding="utf-8"))
        t0 = next(u["translation"] for u in t6["translations"] if u["xml_index"] == 0)
        if t0 != "赛伊克烈酒酒馆":
            failures.append(f"apply_fixes --find 未同步 translation.json: {t0}")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--find", "不存在子串", "--replace", "X")
        if res.returncode != 0 or "no-op" not in (res.stdout + res.stderr):
            failures.append(f"apply_fixes --find no-op 应 exit 0: rc={res.returncode}")
        # CAS 保护：map 与 translation 不同步时应拒绝写盘（本次诊断出的真实行为）
        m7 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        m7["0"]["translation"] = "人为漂移值"
        (batch_dir / "map.json").write_text(json.dumps(m7, ensure_ascii=False, indent=2), encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work),
                  "--find", "人为漂移", "--replace", "X")
        if "不符" not in (res.stdout + res.stderr):
            failures.append(f"apply_fixes 应因 map/translation 不同步而拒绝: {res.stdout[:160]}")
        m7["0"]["translation"] = "赛伊克烈酒酒馆"
        (batch_dir / "map.json").write_text(json.dumps(m7, ensure_ascii=False, indent=2), encoding="utf-8")
        # CAS 保护：map 与 translation 不同步时拒绝写盘

        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            return 1
        print("review tools smoke tests: all passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
