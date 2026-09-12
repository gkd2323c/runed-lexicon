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
PROBE = HERE / "hallucination_probe.py"
READOUT = HERE / "longtext_readout.py"


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

        # ---------- apply_fixes waived_tokens（R16 占位符豁免）----------
        fixes_wt = tmp / "fix-waive.json"
        fixes_wt.write_text(json.dumps({
            "0": {"waived_tokens": ["[Show Ring]"]},
        }, ensure_ascii=False), encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--batch", "B1", "--work-root", str(work), "--fixes", str(fixes_wt))
        m8 = json.loads((batch_dir / "map.json").read_text(encoding="utf-8"))
        if res.returncode != 0 or m8["0"].get("waived_tokens") != ["[Show Ring]"]:
            failures.append(f"apply_fixes waived_tokens failed: {m8['0']}")
        t8 = json.loads((batch_dir / "translation.json").read_text(encoding="utf-8"))
        u0 = next(u for u in t8["translations"] if u["xml_index"] == 0)
        if u0.get("waived_tokens") != ["[Show Ring]"]:
            failures.append(f"apply_fixes waived_tokens 未同步 translation: {u0.get('waived_tokens')}")
        if u0["translation"] != m8["0"]["translation"]:
            failures.append("apply_fixes waived_tokens 不应改译文")

        # --- 抗幻觉探针 ---
        probe_xml = tmp / "probe_english_chinese_translated.xml"
        probe_xml.write_text(build_xml([
            # P3：2 meters / 80% 中文习惯表达，不应报数字缺失
            {"edid": "[A]", "rec": "INFO:NAM1", "source": "The walls are 2 meters of solid stone.",
             "dest": "墙是两米厚的整石。"},
            {"edid": "[B]", "rec": "INFO:NAM1", "source": "I will give you 80% for it.",
             "dest": "我出八成价。"},
            # P3 真缺失：源文有 3 而译文两个数都没有
            {"edid": "[C]", "rec": "BOOK:DESC", "source": "He waited 3 days for the answer to come.",
             "dest": "他等了些日子，答案始终没来。"},
            # P3 边界：100% -> 「十成」不得越界报错
            {"edid": "[G]", "rec": "INFO:NAM1", "source": "I am 100% certain of it.",
             "dest": "我十成十肯定。"},
            # P4：源男译女（强冲突）
            {"edid": "[D]", "rec": "INFO:NAM1",
             "source": "Talk to the blacksmith. He has been here since forever.",
             "dest": "去找铁匠聊聊。她在这儿待了很久了。"},
            # P1：源文多段、译文合段
            {"edid": "[E]", "rec": "BOOK:DESC",
             "source": "First paragraph here.\n\nSecond paragraph here.\n\nThird one.",
             "dest": "第一段。第二段。第三段。"},
        ]), encoding="utf-8")
        probe_json = tmp / "probe.json"
        res = run(PROBE, "--xml", str(probe_xml), "--min-len", "10", "--json", str(probe_json))
        if res.returncode != 0:
            failures.append(f"hallucination_probe 退出码 {res.returncode}: {res.stderr[:200]}")
        else:
            got = json.loads(probe_json.read_text(encoding="utf-8"))
            kinds = {(g["xml_index"], g["kind"]) for g in got}
            # 中文数词（两米 / 八成）不得报 P3
            if (0, "P3-数字语义缺失") in kinds or (1, "P3-数字语义缺失") in kinds:
                failures.append(f"探针 P3 未识别中文数词: {sorted(kinds)}")
            if (2, "P3-数字语义缺失") not in kinds:
                failures.append(f"探针 P3 漏报真缺失: {sorted(kinds)}")
            # 100% -> 「十成」属中文习惯写法，且不得越界
            if (3, "P3-数字语义缺失") in kinds:
                failures.append(f"探针 P3 未识别「X成」/100% 边界: {sorted(kinds)}")
            if (4, "P4-源男译女") not in kinds:
                failures.append(f"探针 P4 漏报性别强冲突: {sorted(kinds)}")
            if (5, "P1-段落少于源文") not in kinds:
                failures.append(f"探针 P1 漏报合段: {sorted(kinds)}")

        # --- 长文本分片（按 idx 头分块，不按空行）---
        shard_xml = tmp / "shard_english_chinese_translated.xml"
        shard_xml.write_text(build_xml([
            {"edid": "[F]", "rec": "BOOK:DESC",
             "source": "Alpha paragraph.\n\nBeta paragraph, long enough to be picked.",
             "dest": "甲段。\n\n乙段，写得够长以便入选。"},
        ]), encoding="utf-8")
        out_dir = tmp / "shards"
        res = run(READOUT, "--xml", str(shard_xml), "--out-dir", str(out_dir),
                  "--min-src", "10", "--cap", "100000")
        if res.returncode != 0:
            failures.append(f"longtext_readout 退出码 {res.returncode}: {res.stderr[:200]}")
        else:
            files = sorted(p.name for p in out_dir.glob("longtext-*.txt"))
            if files != ["longtext-1.txt"]:
                failures.append(f"longtext_readout 分片文件异常: {files}")
            else:
                text = (out_dir / "longtext-1.txt").read_text(encoding="utf-8")
                # 多段 Dest 必须完整保留（不得被空行截断）
                if "乙段，写得够长以便入选。" not in text:
                    failures.append("longtext_readout 丢失多段 Dest 尾段（空行分块回归）")
                if "【源文】" not in text or "【译文】" not in text:
                    failures.append("longtext_readout 缺少对照标题")
            if not (out_dir / "manifest.json").is_file():
                failures.append("longtext_readout 未写 manifest.json")

        # --- 跨批 canonical patch 模式（无 --batch）---
        # 场景：抗幻觉审查的修正常跨多个批次，不便逐个绑定 batch 目录。
        # 机制上 patch 只读 canonical + 对 idx 做 CAS，与 batch 无关。
        xp = tmp / "xp_english_chinese_translated.xml"
        xp.write_text(build_xml([
            {"edid": "[K]", "rec": "QUST:CNAM",
             "source": "We won. [END OF SEASON 1]",
             "dest": "我们赢了。[END OF SEASON 1]"},
            {"edid": "[L]", "rec": "BOOK:DESC",
             "source": "[pagebreak]\nSome text here.",
             "dest": "[pagebreak]\n一些文字。"},
        ]), encoding="utf-8")
        xfix = tmp / "xp_fixes.json"
        xfix.write_text(json.dumps({
            "0": {"new": "我们赢了。[第一季终]"},
            "1": {"new": "[pagebreak]\n一些文字，改过了。"},
        }, ensure_ascii=False), encoding="utf-8")
        xpatch = tmp / "xp_patch.json"
        res = run(APPLY, "--stem", "T", "--fixes", str(xfix),
                  "--translated-xml", str(xp), "--patch-out", str(xpatch),
                  "--work-root", str(work))
        if res.returncode != 0:
            failures.append(f"跨批 patch 模式退出码 {res.returncode}: {res.stderr[:200]}")
        else:
            xd = json.loads(xpatch.read_text(encoding="utf-8"))
            if sorted(xd) != ["0", "1"]:
                failures.append(f"跨批 patch idx 集异常: {sorted(xd)}")
            # 只有真正丢失的方括号标记入 waived_tokens；[pagebreak] 两侧都在，不得入
            if xd.get("0", {}).get("waived_tokens") != ["[END OF SEASON 1]"]:
                failures.append(f"自动豁免方括号标记错误: {xd.get('0', {}).get('waived_tokens')}")
            if "waived_tokens" in xd.get("1", {}):
                failures.append(f"两侧都保留的标记不该入豁免: {xd['1'].get('waived_tokens')}")
            if xd.get("0", {}).get("expected_dest") != "我们赢了。[END OF SEASON 1]":
                failures.append("跨批 patch 未记录 expected_dest")
        # 无 --batch 却缺 --patch-out 必须报错，不得静默成功
        res = run(APPLY, "--stem", "T", "--fixes", str(xfix),
                  "--translated-xml", str(xp), "--work-root", str(work))
        if res.returncode == 0:
            failures.append("--batch 与 --patch-out 均缺时未报错")

        # --- 跨批 --find/--replace（从 canonical 读现值）+ 多行输入 ---
        # 场景：诗节/段落的整块重写是多行的，命令行传换行不可靠，故支持从文件读。
        mfx = tmp / "multi_english_chinese_translated.xml"
        mfx.write_text(build_xml([
            {"edid": "[M]", "rec": "BOOK:DESC",
             "source": "line A\nline B\nline C",
             "dest": "甲行\n乙行\n丙行"},
        ]), encoding="utf-8")
        mfind = tmp / "mfind.txt"
        mfind.write_text("甲行\n乙行", encoding="utf-8")
        mrepl = tmp / "mrepl.txt"
        mrepl.write_text("改甲\n改乙", encoding="utf-8")
        mpatch2 = tmp / "mpatch.json"
        res = run(APPLY, "--stem", "T", "--find-file", str(mfind),
                  "--replace-file", str(mrepl), "--idx-list", "0",
                  "--translated-xml", str(mfx), "--patch-out", str(mpatch2),
                  "--work-root", str(work))
        if res.returncode != 0:
            failures.append(f"跨批多行替换退出码 {res.returncode}: {res.stderr[:200]}")
        else:
            md = json.loads(mpatch2.read_text(encoding="utf-8"))
            if md.get("0", {}).get("translation") != "改甲\n改乙\n丙行":
                failures.append(f"多行替换结果错误: {md.get('0', {}).get('translation')!r}")
            if md.get("0", {}).get("expected_dest") != "甲行\n乙行\n丙行":
                failures.append("多行替换未记录原值")
        # 文件里的串不在 canonical 出现时须 no-op 且 exit 0（不得伪造 patch）
        miss = tmp / "miss.txt"
        miss.write_text("不存在的文本", encoding="utf-8")
        res = run(APPLY, "--stem", "T", "--find-file", str(miss),
                  "--replace-file", str(mrepl), "--idx-list", "0",
                  "--translated-xml", str(mfx), "--patch-out", str(tmp / "mpatch3.json"),
                  "--work-root", str(work))
        if res.returncode != 0:
            failures.append(f"no-op 未返回 0: rc={res.returncode}")
        elif (tmp / "mpatch3.json").exists():
            failures.append("no-op 不应产出 patch 文件")

        # --- 同一 idx 的多次替换必须串联（不得互相覆盖）---
        # 真实场景：一个条目需改两处不同文字，分两次 --find/--replace 执行。
        # 若第二次从 canonical 旧值重新出发，就会把第一次的修改冲掉。
        chained = tmp / "chain_english_chinese_translated.xml"
        chained.write_text(build_xml([
            {"edid": "[N]", "rec": "BOOK:DESC",
             "source": "Alpha beta gamma.",
             "dest": "甲项乙项丙项。"},
        ]), encoding="utf-8")
        f1 = tmp / "c1f.txt"
        r1 = tmp / "c1r.txt"
        f1.write_text("甲项", encoding="utf-8")
        r1.write_text("改甲", encoding="utf-8")
        f2 = tmp / "c2f.txt"
        r2 = tmp / "c2r.txt"
        f2.write_text("丙项", encoding="utf-8")
        r2.write_text("改丙", encoding="utf-8")
        cpatch = tmp / "chain_patch.json"
        for ff, rf in ((f1, r1), (f2, r2)):
            res = run(APPLY, "--stem", "T", "--find-file", str(ff),
                      "--replace-file", str(rf), "--idx-list", "0",
                      "--translated-xml", str(chained), "--patch-out", str(cpatch),
                      "--work-root", str(work))
            if res.returncode != 0:
                failures.append(f"串联替换退出码 {res.returncode}: {res.stderr[:150]}")
                break
        else:
            cd = json.loads(cpatch.read_text(encoding="utf-8"))
            got = cd.get("0", {}).get("translation")
            if got != "改甲乙项改丙。":
                failures.append(f"多次替换未串联（后次冲掉前次）: {got!r}")
            if cd.get("0", {}).get("expected_dest") != "甲项乙项丙项。":
                failures.append(f"串联后 expected_dest 应仍取 canonical 现值: {cd.get('0', {}).get('expected_dest')!r}")

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
