#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""term_digest.py — compile a batch context.json into a compact dispatch digest.

Why this exists
---------------
A raw context.json for one Artaeum batch is ~95-105 KB. A subagent handed that
file cannot read it in one pass; it either fails or falls back to shell grep
against the whole dictionary tree — burning its budget on mechanical lookups
that translation-context-builder already computed. This tool pre-chews the
per-entry evidence into one compact text file (a few lines per idx) so a
dispatch task card can point at the digest instead of the raw context.

Fixes the observed failure mode: subagents doing "query -> compare -> query
again" loops (real cases: a 9-minute Butter instance that never wrote output
and died with an EMPTY-RESPONSE; a Hanako instance that grepped the whole
canonical for first-use precedents of every proper noun).

What it emits, per entry:
    [idx] REC | source | dup=N
        MOD: English→中文 (STATUS); ...
        OFF: English=中文[REC]; ...
        CTX: quest=<edid>; cat=<category>; topic=<topic>   (only when matched)

MOD = project contract hits (mod_terms_hits); OFF = official dictionary hits
(official_dictionary_hits); CTX = dialogue anchor (only for matched dialogue
entries). Entries with no hits still get a line with '-' so the executor knows
the absence is real, not overlooked.

Read-only: never touches the source XML or any batch artifact. Writes only the
digest file requested via --out.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _fmt_mod(hits) -> str:
    if not hits:
        return "-"
    seen = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        en = (h.get("english") or "").strip()
        zh = (h.get("chinese") or h.get("zh") or h.get("target") or "").strip()
        st = (h.get("status") or "").strip()
        if not en:
            continue
        item = f"{en}→{zh}" + (f" ({st})" if st else "")
        if item not in seen:
            seen.append(item)
    return "; ".join(seen) if seen else "-"


def _fmt_off(hits) -> str:
    if not hits:
        return "-"
    seen = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        en = (h.get("source") or "").strip()
        zh = (h.get("dest") or "").strip()
        rec = (h.get("rec") or "").strip()
        if not en:
            continue
        item = f"{en}={zh}" + (f"[{rec}]" if rec else "")
        if item not in seen:
            seen.append(item)
    return "; ".join(seen) if seen else "-"


def _fmt_ctx(entry: dict) -> str:
    dc = entry.get("dialogue_context") or {}
    if dc.get("status") != "matched":
        return ""
    d = dc.get("dialogue") or {}
    bits = []
    if d.get("quest_edid"):
        bits.append("quest=" + str(d["quest_edid"]))
    if d.get("category"):
        bits.append("cat=" + str(d["category"]))
    if d.get("topic"):
        bits.append("topic=" + str(d["topic"]))
    return "; ".join(bits)


# ---------------------------------------------------------------- spell-name registry
# MOD 专属的法术名统一表（由早期法术书批次提取、已写回 canonical）。
# 此表不进契约（普通词作 REQUIRED 会过度绑定），因此 digest 必须单独带出：
# 否则子代理看不到已定形，只能凭音感自造（真实事故：BOOK-004 的
# Throwdoll/Sacrifice/Charge 三处自创，与已写回的击倒术/祭品/充能术偏离）。
_MD_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


def load_spell_registry(path: str) -> dict:
    """读 markdown 统一表的 Source→译名（跳过表头/分隔行）。"""
    out = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in lines:
        m = _MD_ROW.match(line)
        if not m:
            continue
        en, zh = m.group(1).strip(), m.group(2).strip()
        if en.startswith("Source") or zh.startswith("统一译名") or set(zh) <= {"-"}:
            continue
        if set(en) <= {"-"}:
            continue
        # 复合行（Pacify / Rally / Rout / Frenzy）拆分保留整行
        out[en] = zh
    return out


KNOWN_ANCHOR = ("Source", "统一译名", "---")


def _registry_section(registry: dict, entries: list) -> list:
    """渲染统一表段落；* 标出本批源文命中项。"""
    if not registry:
        return []
    srcs = [(e.get("xml") or {}).get("source") or "" for e in entries]
    lines = [f"== 法术名统一表（{len(registry)} 项；* = 本批源文命中） =="]
    for en, zh in registry.items():
        hit = any(en in s for s in srcs)
        lines.append(f"  {'* ' if hit else '  '}{en} = {zh}")
    return lines


def digest(context: dict, registry: dict | None = None) -> str:
    lines = []
    all_entries = []
    batches = context.get("batches") or []
    for b in batches:
        entries = b.get("entries") or []
        all_entries.extend(entries)
        lines.append(f"== batch {b.get('batch_index')} ({len(entries)} entries) ==")
        for e in entries:
            x = e.get("xml") or {}
            t = e.get("terminology") or {}
            idx = x.get("index")
            rec = x.get("rec") or ""
            src = x.get("source") or ""
            dup = x.get("duplicate_count")
            head = f"[{idx}] {rec} | {src}"
            if isinstance(dup, int) and dup > 1:
                head += f" | dup={dup}"
            lines.append(head)
            lines.append("    MOD: " + _fmt_mod(t.get("mod_terms_hits")))
            lines.append("    OFF: " + _fmt_off(t.get("official_dictionary_hits")))
            ctx = _fmt_ctx(e)
            if ctx:
                lines.append("    CTX: " + ctx)
    sec = _registry_section(registry or {}, all_entries)
    if sec:
        lines.append("")
        lines.extend(sec)
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--context", required=True, help="batch context.json path")
    ap.add_argument("--spell-registry", default=None,
                    help="法术名统一表 markdown（默认自动探测 .work/<plugin>/notes/*-spell-name-registry.md）")
    ap.add_argument("--out", help="write digest here instead of stdout")
    a = ap.parse_args()

    p = Path(a.context)
    if not p.exists():
        print(f"error: context not found: {p}", file=sys.stderr)
        return 2
    data = json.loads(p.read_text(encoding="utf-8"))

    # 统一表：显式路径优先；否则从 context 路径推导 .work/<plugin>/notes/
    reg_path = a.spell_registry
    if not reg_path:
        parts = p.resolve().parts
        if "notes" not in parts and ".work" in parts:
            wi = parts.index(".work")
            if wi + 1 < len(parts):
                notes_dir = Path(*parts[:wi + 2]) / "notes"
                if notes_dir.is_dir():
                    cands = sorted(notes_dir.glob("*-spell-name-registry.md"))
                    if cands:
                        reg_path = str(cands[0])
    registry = load_spell_registry(reg_path) if reg_path else {}

    text = digest(data, registry)

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        n = text.count("\n")
        extra = f"; spell-registry={len(registry)}" if registry else ""
        print(f"wrote {n} lines -> {out}{extra}")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
