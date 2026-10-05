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


def _case_mismatch_note(en: str, source: str) -> str:
    """词条是大写专名、但本行源文里实际是小写普通名词时给出警示。

    事故锚定（2026-10-02 INFO-370）：官方词典的 `The Companions=战友团` 与
    `Familiar=使魔` 是大写专名，但它们在源文里以小写出现
    （`the companions whose behavior…`=同行者、`familiar shapes`=熟悉的形状）。
    digest 早先只打 `Familiar=使魔[NPC_:FULL]`，译者看到「官方词条」自然
    以为必须译成召唤法术/派系名，只能靠人工识破。**大小写是专名判别的第一
    信号，必须在派单材料里显式给出**，而不是让每个译者实例各自踩一次。

    判据：词条英文首字母大写，且源文里能找到该词的**全小写**形态。
    只在两者同时成立时标注，不猜语义。
    """
    if not en or not source:
        return ""
    if not en[0].isupper():
        return ""
    # 词条本身（去掉前导冠词后）在本行是否以小写形态出现
    stem = re.sub(r"^(the|a|an|The|A|An)\s+", "", en).strip()
    if not stem:
        return ""
    if re.search(r"(?<![A-Za-z])" + re.escape(stem[0].lower()) + re.escape(stem[1:])
                 + r"(?![A-Za-z])", source):
        return " ⚠源文小写，疑普通名词"
    return ""


# ------------------------------------------------------------------ 判据型 note
# 为什么需要：2026-10-02 整轮术语漂移（machinery 实物义、virtue、cause、
# curse、vessel 各 1~2 处，以及「神龛」「教团」只能等门禁 FAIL 才发现）的
# 根因就是本函数只打 `en→zh (STATUS)`、**把词条的 note 整条丢掉**。译者
# 看到主形就无条件套用，`machinery→机械` 于是盖掉了 note 里写明的
# 「实物装置＝机械／抽象体系＝机制」。
#
# 为什么不全量带出：全库 1,471 词条都有 note，但绝大多数是取证过程记录
# （哪几行实证、什么时候回正的），与「这一行该怎么判」无关。全量带出会把
# digest 从紧凑派单材料撑成 context 的复刻，重新制造本工具要解决的读不完问题。
# 只挑**带语义判据**的那几十条 —— 判据型 = note 含下列任一标记。
_JUDGE_MARKERS = (
    "分域", "两义", "判据", "绑定", "勿强并", "不作同锚合并", "分立",
    "不是无条件", "勿混", "不是同一", "勿作",
)
_JUDGE_MAX = 180
# 点名了本行的 note 不按 180 截断。
# 事故（2026-10-02 INFO-511 idx 34842）：`communion` 的 note 写明「③**34842 未译**
# …按②取「共融」」，是对该行的**逐行预裁**，却正好落在 180 字截断线之后。digest
# 表头写着「先读它再定译文」，实际只递了残句，译者看不到预裁就自行取了「共食」并
# 标 REVIEW。判据：note 里出现本行 xml_index（独立数字）＝ 它含针对本行的裁决，
# 截掉等于把裁决藏起来；这类 note 全文带出。
_JUDGE_MAX_ROW_CITED = 4000

# main() 可用 --no-judge-notes 关掉（默认开：判据不到译者眼前就是缺陷）
_SHOW_JUDGE = True


def _judge_note(h: dict, idx=None) -> str:
    """带出判据型 note，让译者看到主形不是无条件替换指令。"""
    if not _SHOW_JUDGE:
        return ""
    note = (h.get("note") or "").strip()
    if not note:
        return ""
    if not any(m in note for m in _JUDGE_MARKERS):
        return ""
    flat = re.sub(r"\s+", " ", note)
    limit = _JUDGE_MAX
    if idx is not None and re.search(r"(?<!\d)%d(?!\d)" % int(idx), flat):
        limit = _JUDGE_MAX_ROW_CITED
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return f"〔判据:{flat}〕"


def _fmt_mod(hits, source: str = "", idx=None) -> str:
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
        item = (f"{en}→{zh}" + (f" ({st})" if st else "")
                + _case_mismatch_note(en, source)
                + _judge_note(h, idx))
        if item not in seen:
            seen.append(item)
    return "; ".join(seen) if seen else "-"


def _fmt_off(hits, source: str = "") -> str:
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
        item = (f"{en}={zh}" + (f"[{rec}]" if rec else "")
                + _case_mismatch_note(en, source))
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


def digest(context: dict, registry: dict | None = None,
           canonical_path: str | None = None,
           source_xml: str | None = None,
           registry_english: set | None = None) -> str:
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
            lines.append("    MOD: " + _fmt_mod(t.get("mod_terms_hits"), src, idx))
            lines.append("    OFF: " + _fmt_off(t.get("official_dictionary_hits"), src))
            ctx = _fmt_ctx(e)
            if ctx:
                lines.append("    CTX: " + ctx)
    sec = _registry_section(registry or {}, all_entries)
    if sec:
        lines.append("")
        lines.extend(sec)
    if canonical_path:
        extra = _noun_lookup_section(context, canonical_path,
                                     registry_english or set(), source_xml)
        if extra:
            lines.append("")
            lines.extend(extra)
    return "\n".join(_legend() + lines) + ("\n" if lines else "")


def _noun_lookup_section(context, canonical_path, registry_english, source_xml):
    """「本批专名速查」：词表没收录、但库内已有译形（或确属全新）的专名。

    为什么必须有这一段：`MOD:` 行只给**词表已收录**的专名，词表没收录的专名在
    digest 里一个字都不出现，译者只能自己猜译形。实测这正是新造形的来源——
    `Shimmerene's Seaview` 被新拟成「微光海景居」（库内既成面是「海景」）、
    `Dro Darrj` 被新拟成「德罗'达吉」（既成面「德罗·达里'奇」，音与撇号位置都错）。
    **派单材料缺这一段时，子代理每批都会新拟专名。**

    判据用**结构证据**（源 XML 的名称行 = 玩家在界面里看到的名字）而不是形态
    猜测：英文的「首字母大写/驼峰」不足以判专名，实测两侧都错（漏掉句中的
    `Palamay if you were I`，又把 `I've`/`That's`/`can't` 全当成专名）。
    详见 `noun_lookup` 模块 docstring。
    """
    try:
        from noun_lookup import unregistered_nouns_section
    except ImportError:
        return []
    try:
        return unregistered_nouns_section(context, canonical_path,
                                          registry_english, source_xml)
    except Exception as exc:                      # 速查段挂了不能拖垮 digest
        return [f"== 本批专名速查 ==", f"  （生成失败，不影响上方逐行内容：{exc}）"]


def _legend() -> list:
    """派单材料自带读法说明——译者不必回查本脚本源码才知道标记含义。"""
    if not _SHOW_JUDGE:
        return []
    return [
        "== 读法 ==",
        "  MOD 的 en→zh 只是该词条的**主形记录**，不是无条件替换指令。",
        "  〔判据:…〕= 词条 note 里的语义判据（分域/两义/绑定），**先读它再定译文**。",
        "  ⚠源文小写 = 该专名在本行是普通名词，走普通义。",
        "",
    ]


def discover_spell_registry(context_path: Path, explicit: str | None = None) -> str | None:
    """定位法术名统一表 markdown；显式路径优先，否则从 context 路径推导。

    抽成独立函数是为了让 `read_batch.py` 的 digest 新鲜度检查复用同一套发现
    逻辑——重生成 digest 时必须和 `main()` 用完全一样的规则，否则「新鲜」只是
    换了个样子（历史上 digest 与 read_batch 各写一套，正是陈旧 digest 长期
    存在的间接原因）。
    """
    if explicit:
        return explicit
    parts = Path(context_path).resolve().parts
    if "notes" not in parts and ".work" in parts:
        wi = parts.index(".work")
        if wi + 1 < len(parts):
            notes_dir = Path(*parts[:wi + 2]) / "notes"
            if notes_dir.is_dir():
                cands = sorted(notes_dir.glob("*-spell-name-registry.md"))
                if cands:
                    return str(cands[0])
    return None


def build_digest(context_path: str | Path, spell_registry: str | None = None,
                 canonical_xml: str | None = None,
                 source_xml: str | None = None,
                 terms_path: str | None = None) -> str:
    """context.json 路径 → digest 文本。CLI 与新鲜度检查共用这一条路径。"""
    p = Path(context_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    reg_path = discover_spell_registry(p, spell_registry)
    registry = load_spell_registry(reg_path) if reg_path else {}
    reg_english = _registry_english(terms_path)
    return digest(data, registry, canonical_xml, source_xml, reg_english)


def _registry_english(terms_path: str | None) -> set:
    """terms.json 里已收录的英文专名（小写）。用于速查段排除「词表已有」的。"""
    if not terms_path:
        return set()
    tp = Path(terms_path)
    if not tp.is_file():
        return set()
    t = json.loads(tp.read_text(encoding="utf-8"))
    items = t.get("terms", t) if isinstance(t, dict) else t
    return {x["english"].lower() for x in items if isinstance(x, dict) and "english" in x}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--context", required=True, help="batch context.json path")
    ap.add_argument("--spell-registry", default=None,
                    help="法术名统一表 markdown（默认自动探测 .work/<plugin>/notes/*-spell-name-registry.md）")
    ap.add_argument("--out", help="write digest here instead of stdout")
    ap.add_argument("--no-judge-notes", action="store_true",
                    help="不带出判据型 note（默认带出；关掉等于把分域判据藏回词表）")
    ap.add_argument("--canonical", help="translated XML；给了才生成「本批专名速查」段")
    ap.add_argument("--source-xml",
                    help="源 XML（专名集合取它的名称行，译没译都算）；默认用 --canonical")
    ap.add_argument("--terms", help="terms.json；给了才能排除「词表已收录」的专名")
    a = ap.parse_args()

    global _SHOW_JUDGE
    _SHOW_JUDGE = not a.no_judge_notes

    p = Path(a.context)
    if not p.exists():
        print(f"error: context not found: {p}", file=sys.stderr)
        return 2

    reg_path = discover_spell_registry(p, a.spell_registry)
    registry = load_spell_registry(reg_path) if reg_path else {}
    text = digest(json.loads(p.read_text(encoding="utf-8")), registry,
                  a.canonical, a.source_xml, _registry_english(a.terms))

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
