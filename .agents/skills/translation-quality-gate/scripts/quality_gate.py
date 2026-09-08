# -*- coding: utf-8 -*-
"""quality_gate.py — deterministic pre-writeback translation quality gate.

Inputs:
  --result   translation-result JSON (executor schema; translations[] with
             translation_unit_id / source / translation / status / ...)
  --contract compiled contract JSON (schema v1: terms + unit_bindings) OR
             a lightweight gate-case-style contract (bindings + terms)
  --xml      optional source xTranslator XML (for XML001 structural identity check)
Outputs:
  Quality Report on stdout; exit code 0 = PASS (no FAIL), 1 = FAIL, 2 = usage error.
  Optionally --report <path> writes the report JSON.

Principles:
  - Read-only. Never modifies translations or XML.
  - Only checks bindings; never re-derives entity identity from source text.
  - Detection != conversion: CHAR001 reports diffs, never rewrites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import io
from pathlib import Path


try:
    from term_match import (resolve_bindings, check_unit, match_required_present,
                            find_forbidden_hits, resolve_global_bans, find_global_ban_hits,
                            find_global_keep_hits, CONTRACT_GLOBAL_BANS_KEY,
                            CONTRACT_GLOBAL_KEEP_KEY)
except Exception:  # allow running from another cwd
    from .term_match import (resolve_bindings, check_unit, match_required_present,
                             find_forbidden_hits, resolve_global_bans, find_global_ban_hits,
                             find_global_keep_hits, CONTRACT_GLOBAL_BANS_KEY,
                             CONTRACT_GLOBAL_KEEP_KEY)

# CHAR001 uses the vendored zh-cn conversion table (scripts/zh_cn_conv.json);
# no third-party dependency.

# ---------------------------------------------------------------- CHAR001

# CHAR001 uses a vendored zh-cn conversion table (scripts/zh_cn_conv.json,
# merged zh2Hans + zh2CN from zhconv 1.4.3 zhcdict.json, GPLv2+) and a replica
# of zhconv's greedy longest-match conversion. Deterministic: no runtime
# dependency on zhconv or any third-party package, no environment-conditional
# behavior (the "zhconv unavailable" degradation branch is removed, t_2f368a4c).
_CONV_DATA = json.loads(
    Path(__file__).with_name('zh_cn_conv.json').read_text(encoding='utf-8'))
_CONV_MAX_LEN = max(len(k) for k in _CONV_DATA)


def _zh_convert_cn(s: str) -> str:
    """Replica of zhconv.convert(s, 'zh-cn') via the vendored table.

    Greedy longest-prefix matching: at each position take the longest dict
    entry matching the text from that position; unmatched chars pass through.
    Verified byte-identical to zhconv.convert(s, 'zh-cn') over the full CJK
    single-char range and realistic phrase probes (including identity phrase
    entries like 瞭望 that block shorter char conversions, and R11 trap
    contexts like 什么正).
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        hit = None
        for frag_len in range(min(n - i, _CONV_MAX_LEN), 0, -1):
            frag = s[i:i + frag_len]
            if frag in _CONV_DATA:
                hit = _CONV_DATA[frag]
                i += frag_len
                break
        if hit is None:
            out.append(s[i])
            i += 1
        else:
            out.append(hit)
    return ''.join(out)


def charset_issue(dest: str) -> list:
    """Return list of offending chars when dest is not fully simplified Chinese.

    Uses vendored zh-cn normalization: if _zh_convert_cn(dest) != dest, report
    the concrete differing characters. Detection only — no rewriting.

    Filters out known conversion false positives (R11, v0.1.4): when an
    already-simplified character (e.g., 么 U+4E48) is incorrectly converted in
    certain contexts (么→幺/什幺 when followed by 正 or 子), the char is already
    simplified and the diff is a conversion-table quirk, not a real
    non-simplified-char issue.
    """
    conv = _zh_convert_cn(dest)
    if conv == dest:
        return []
    # collect chars that changed
    changed = []
    for a, b in zip(dest, conv):
        if a != b:
            # Skip R11 false positives: `a` is already simplified on its own
            # (vendored single-char conversion leaves it unchanged).
            if _CONV_DATA.get(a, a) == a:
                continue
            changed.append((a, b))
    # tail length difference (rare)
    if len(conv) != len(dest):
        changed.append(('…', '…'))
    if not changed:
        return []  # all diffs were false positives
    detail = '非简体字符: ' + ', '.join(f'{a!r}->{b!r}' for a, b in changed[:20])
    if len(changed) > 20:
        detail += f' … 共 {len(changed)} 处'
    return [{'code': 'CHAR001', 'severity': 'FAIL', 'detail': detail}]


# ---------------------------------------------------------------- keep / placeholder / xml

# TRUNC001 截断检测参数（由 SB1 真实事故标定）
# 检出的是“source 完整长句、dest 以省略号中断且明显偏短”的候选，
# 可能是翻译时把后半句砍掉用……带过（真残缺），也可能是毒舌风格省略（合法）。
# 因此定位为 WARNING，需 Agent 对照 source 人审消解。
TRUNC_MIN_SRC_LEN = 55       # source 短于此不检（避免误伤短句感叹）
TRUNC_MAX_DST_RATIO = 0.62   # dest/source 长度比低于此视为可疑


def truncation_issue(unit: dict) -> list:
    """TRUNC001: 译文以省略号中断但 source 是完整句，疑似后半截断。

    判定条件（全部满足才报）：
    - source 长度 >= TRUNC_MIN_SRC_LEN（长句，排除 Gah.../Neeth... 类短叹）；
    - source 不以省略号/句号/感叹/问号之外的方式自带中断（原文含 ... 或 … 结尾则跳过：
      那是有意的欲言又止，不是残缺）；
    - dest 非空、dest != source（排 KEEP / 未译）；
    - dest 以 …… 或 … 结尾（译文中断信号）；
    - len(dest) < len(source) * TRUNC_MAX_DST_RATIO（明显短于原文）。
    注意中英长度差异天然存在，0.62 是宽松阈值，宁可多报 WARNING 让 Agent 人审，
    不追求低误报而漏掉真残缺。
    """
    src = (unit.get('source') or '').strip()
    dst = (unit.get('translation') or unit.get('original_dest') or '').strip()
    if not src or not dst or src == dst:
        return []
    # R15: 富文本先 strip 标签再判定。此前 `if '<' in src or '<' in dst: return []`
    # 一刀切跳过全部带标签文本，导致 BOOK（HTML 信件/书籍重灾区）的 11 条截断
    # 无一条被检出（Druadach-book 2026-09-08）。标签内的省略号 strip 后自然
    # 消失，不会误报；纯标签行 strip 后为空，由上方的 not dst 排除。
    src_text = re.sub(r'<[^>]*>', '', src)
    dst_text = re.sub(r'<[^>]*>', '', dst)
    if len(src_text) < TRUNC_MIN_SRC_LEN:
        return []
    # source 自带中断（以 ... / … 结尾或含末尾省略）说明原文就是吞话，跳过
    if re.search(r'\.\.\.|…\s*$', src_text):
        return []
    dst_tail = dst_text.rstrip()
    if not (dst_tail.endswith('……') or dst_tail.endswith('…')):
        return []
    if len(dst_text) >= len(src_text) * TRUNC_MAX_DST_RATIO:
        return []
    return [{'code': 'TRUNC001', 'severity': 'WARNING',
             'detail': (f'译文以省略号中断且明显短于完整句源文 '
                        f'(src={len(src_text)}字, dst={len(dst_text)}字，去标签后)，'
                        f'疑似后半截断；若是有意的欲言又止/毒舌省略请忽略'),
             'expected': ''}]


def global_ban_issue(source: str, dest: str, bans: list) -> list:
    """TERM004: project-wide ban list (global_forbidden_words) — any forbidden
    form present in dest while the English anchor appears in source → FAIL.

    Unlike TERM002 (bound terms only), the global ban list is an independent
    scan: it protects official-name integrity on every translated line, whether
    or not the line carries an explicit unit binding. Each hit carries the
    ban's reason so an Agent can judge false-positive vs real regression.

    dest == source (KEEP / untranslated technical lines) never triggers — a
    line left in English contains no Chinese wrong form.
    """
    out = []
    for ban in bans:
        eng = ban['english']
        for f in find_global_ban_hits(source, dest, ban):
            reason = ban.get('reason') or '项目级禁用词'
            out.append({'code': 'TERM004', 'severity': 'FAIL',
                        'term_id': 'global.' + _slug(eng),
                        'detail': f'全局禁用词 {f!r}（{eng}）出现: {reason}',
                        'expected': ban.get('target') or f'不含 {f!r}',
                        'variant': f})
    return out


def global_keep_issue(source: str, dest: str, gkeep_list: list) -> list:
    """KEEP002: a project-wide KEEP value that appears (whole-word) in source
    must stay untranslated. Any dest differing from it is a FAIL.
    """
    out = []
    for gk in gkeep_list or []:
        if gk and find_global_keep_hits(source, dest, gk):
            out.append({'code': 'KEEP002', 'severity': 'FAIL',
                        'detail': f'全局 KEEP 值被翻译: {gk!r}', 'expected': gk})
    return out


def _slug(s: str) -> str:
    import re
    return re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()[:48]


def keep_issue(unit: dict, keep_list: list) -> list:
    """KEEP001: KEEP-marked source values must remain unchanged in dest."""
    src = (unit.get('source') or '').strip()
    dst = (unit.get('translation') or unit.get('original_dest') or '').strip()
    if src in (keep_list or []) and dst != src:
        return [{'code': 'KEEP001', 'severity': 'FAIL',
                 'detail': f'KEEP 内容被修改: {dst!r}', 'expected': src}]
    return []


def placeholder_issue(unit: dict, protected: list) -> list:
    """PLACEHOLDER001: every protected token in source must appear unchanged in dest.

    R16 联动：result 条目声明 waived_tokens（Agent 背书的方括号中文化，如
    [Show Ring]→[展示戒指]）时，跳过对应 token。executor validate 已校验声明
    真实存在且记 warning 备查，gate 此处不再重复拦。"""
    src = unit.get('source') or ''
    dst = unit.get('translation') or unit.get('original_dest') or ''
    waived = unit.get('waived_tokens') or []
    issues = []
    for tok in protected or []:
        if tok in src and tok not in dst and tok not in waived:
            issues.append({'code': 'PLACEHOLDER001', 'severity': 'FAIL',
                           'detail': f'占位符缺失: {tok!r}', 'expected': tok})
    return issues


# ---------------------------------------------------------------- xml identity (light)

XML_STRING_RE = re.compile(r'<String\b[^>]*>.*?</String>', re.DOTALL)
TAG_RE = re.compile(r'<(Source|Dest|EDID|REC)\b[^>]*>(.*?)</\1>', re.DOTALL)


def _xml_unescape(s):
    """Decode the five standard XML entities that xTranslator XML uses."""
    return (s.replace('&lt;', '<').replace('&gt;', '>')
             .replace('&quot;', '"').replace('&apos;', "'")
             .replace('&amp;', '&'))


def xml_identity_issue(unit: dict, xml_text: str, block_index=None) -> list:
    """XML001 pre-check: locate the unit's xml_index block and verify identity fields.

    `block_index`: optional prebuilt list of <String> block match objects.
    When None, it is built from `xml_text` on this call (slow path, kept for
    single-unit callers); run_gate builds it once and passes it in.
    """
    idx = unit.get('xml_index')
    if idx is None or xml_text is None:
        return []
    blocks = block_index if block_index is not None else list(XML_STRING_RE.finditer(xml_text))
    if idx < 0 or idx >= len(blocks):
        return [{'code': 'XML001', 'severity': 'FAIL', 'detail': f'xml_index {idx} 越界'}]
    blk = blocks[idx].group(0)
    fields = {m.group(1): _xml_unescape(m.group(2)) for m in TAG_RE.finditer(blk)}
    src = unit.get('source')
    if src is not None:
        if fields.get('Source') != src:
            return [{'code': 'XML001', 'severity': 'FAIL',
                     'detail': f'xml_index {idx} Source 与 XML 不一致',
                     'expected': fields.get('Source')}]
    return []


# ---------------------------------------------------------------- gate driver

def run_gate(results: list, contract: dict, keep_list: list, xml_text=None,
             protected_override=None, auto_bind=False, auto_strict_terms=None) -> dict:
    """auto_bind: for units without explicit bindings, auto-bind no-risk REQUIRED
    terms whose English source form appears in the source text. Hits that lack the
    target become WARNING (review-queue candidates) unless the term is in
    auto_strict_terms, in which case they FAIL.
    """
    issues_total = []
    checkable = 0
    # resolve bindings once per unit_id (unit-level bindings)
    bind_by_unit = {}
    for ub in contract.get('unit_bindings') or []:
        bind_by_unit[ub['translation_unit_id']] = ub['bindings']
    # lightweight gate-case style: contract may carry top-level 'bindings' with terms inline
    legacy_bindings = contract.get('bindings') or []

    term_index = contract.get('terms') or {}
    auto_strict = set(auto_strict_terms or [])
    # project-wide ban list + KEEP list (compiled into the contract by the
    # term-contract-compiler from the root global-forbidden-words.json).
    global_bans = resolve_global_bans(contract)
    global_keep = contract.get(CONTRACT_GLOBAL_KEEP_KEY) or []

    # build the <String> block index once for XML001 identity pre-checks;
    # rebuilding it per unit made the gate O(units × blocks) on large batches
    xml_block_index = None
    if xml_text is not None:
        xml_block_index = list(XML_STRING_RE.finditer(xml_text))

    for unit in results:
        uid = unit.get('translation_unit_id') or str(unit.get('xml_index'))
        src = unit.get('source') or ''
        dst = unit.get('translation') or unit.get('original_dest') or ''
        if not dst:
            continue
        checkable += 1
        issues = []

        # --- bindings for this unit
        resolved = []
        unit_auto = False
        if uid in bind_by_unit:
            resolved = _resolve(term_index, bind_by_unit[uid])
        elif legacy_bindings:
            resolved = _resolve_legacy(term_index, legacy_bindings, src)
        elif auto_bind and dst != src:
            # no explicit bindings and dest was actually translated: auto-bind no-risk
            # REQUIRED terms found in source. Units where dest == source are KEEP/
            # untranslated technical strings and must not be auto-checked.
            resolved = _resolve_auto(term_index, src)
            unit_auto = bool(resolved)
        chk = check_unit(src, dst, resolved, term_index)
        if unit_auto:
            # in auto-bind mode, missing-target findings are review candidates (WARNING)
            # unless the term is declared strict. forbidden hits stay FAIL.
            for c in chk:
                if c['code'] == 'TERM001' and c['term_id'] not in auto_strict:
                    c['severity'] = 'WARNING'
        issues += chk

        # --- keep / placeholder / charset / xml
        issues += keep_issue(unit, keep_list)
        # project-wide bans & KEEP list are checked on every translated line,
        # independently of unit bindings.
        if dst != src:
            g_issues = global_ban_issue(src, dst, global_bans)
            g_issues += global_keep_issue(src, dst, global_keep)
            # a concrete wrong form can be both a local term's forbidden variant
            # (TERM002, bound unit) and a project-wide ban (TERM004). Keep the
            # local finding (richer term evidence), drop the duplicate global one.
            local_variants = {i.get('variant') for i in issues if i.get('code') == 'TERM002'}
            if local_variants:
                g_issues = [i for i in g_issues
                            if not (i.get('code') == 'TERM004' and i.get('variant') in local_variants)]
            issues += g_issues
        prot = protected_override if protected_override is not None else (unit.get('protected_tokens') or [])
        issues += placeholder_issue(unit, prot)
        issues += charset_issue(dst)
        issues += truncation_issue(unit)
        if xml_text is not None:
            issues += xml_identity_issue(unit, xml_text, block_index=xml_block_index)

        for iss in issues:
            iss['translation_unit_id'] = uid
            iss['rec'] = unit.get('rec')
            iss['edid'] = unit.get('edid')
            if 'expected' not in iss:
                iss['expected'] = ''
        issues_total += issues

    fails = [i for i in issues_total if i['severity'] == 'FAIL']
    warns = [i for i in issues_total if i['severity'] == 'WARNING']
    return {
        'verdict': 'PASS' if not fails else 'FAIL',
        'units_checked': checkable,
        'fail_count': len(fails),
        'warning_count': len(warns),
        'fails': fails,
        'warnings': warns,
    }


def _resolve(term_index, bindings):
    out = []
    from term_match import ResolvedTerm
    for b in bindings:
        t = term_index.get(b['term_id'])
        if t is None:
            continue
        out.append(ResolvedTerm(b['term_id'], t, bool(b.get('required', False)),
                                b.get('required_target'), b.get('source_span', '')))
    return out


def _resolve_auto(term_index, src):
    """Auto-bind every no-risk REQUIRED term whose English form appears in source.
    Returns the resolved binding list. Actual dest conformance is checked by
    check_unit (TERM001), which the caller downgrades to WARNING in auto mode.
    """
    from term_match import auto_bind_candidates, find_source_hits, ResolvedTerm
    resolved = []
    for tid, term in term_index.items():
        if not auto_bind_candidates(term):
            continue
        hits = find_source_hits(src, term)
        if not hits:
            continue
        resolved.append(ResolvedTerm(tid, term, True, term.get('target'), ''))
    return resolved



def _resolve_legacy(term_index, legacy_bindings, src):
    """gate-case style: bindings apply to the unit if source_span in source."""
    out = []
    from term_match import ResolvedTerm
    for b in legacy_bindings:
        tid = b['term_id']
        t = term_index.get(tid)
        if t is None:
            continue
        span = b.get('source_span', '')
        if span and span not in src:
            continue  # binding not applicable to this unit
        out.append(ResolvedTerm(tid, t, bool(b.get('required', True)),
                                b.get('required_target'), span))
    return out


# ---------------------------------------------------------------- main

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Translation Quality Gate (read-only)')
    ap.add_argument('--result', required=True, action='append', help='translation-result JSON (repeatable, one or more files)')
    ap.add_argument('--contract', required=True, help='compiled contract JSON')
    ap.add_argument('--keep-list', default=None, help='JSON list of KEEP source values (optional)')
    ap.add_argument('--xml', default=None, help='source xTranslator XML for XML001 identity pre-check (optional)')
    ap.add_argument('--report', default=None, help='write Quality Report JSON to path')
    ap.add_argument('--auto-bind', action='store_true', help='auto-bind no-risk REQUIRED terms found in source; missing targets become WARNING review candidates')
    ap.add_argument('--auto-strict', default=None, help='comma-separated term_ids that stay FAIL on missing target in auto-bind mode')
    args = ap.parse_args()

    results = []
    for p in args.result:
        data = _load_json(p)
        if isinstance(data, dict):
            data = data.get('translations') or data.get('items') or data.get('results') or []
        results.extend(data)
    contract = _load_json(args.contract)
    keep_list = _load_json(args.keep_list) if args.keep_list else []
    xml_text = open(args.xml, encoding='utf-8-sig').read() if args.xml else None
    auto_strict = [s.strip() for s in (args.auto_strict or '').split(',') if s.strip()] or None

    report = run_gate(results, contract, keep_list, xml_text, auto_bind=args.auto_bind,
                      auto_strict_terms=auto_strict)
    # human summary
    print(json.dumps({
        'verdict': report['verdict'],
        'units_checked': report['units_checked'],
        'fail_count': report['fail_count'],
        'warning_count': report['warning_count'],
    }, ensure_ascii=False))
    for i in report['fails'][:50]:
        print(f"  FAIL [{i.get('translation_unit_id')}] {i['code']} {i.get('term_id','')}: {i['detail']}")
    for i in report['warnings'][:20]:
        print(f"  WARN [{i.get('translation_unit_id')}] {i['code']}: {i['detail']}")
    if report['fail_count'] > 50:
        print(f'  … 其余 {report["fail_count"]-50} 条 FAIL')
    if args.report:
        report_path = Path(args.report)
        canonical = re.compile(r"^[A-Za-z0-9_.\-]+-gate-report\.json$")
        if not canonical.fullmatch(report_path.name):
            raise SystemExit(
                f"error: report filename must be <plugin>-gate-report.json "
                f"(deterministic output contract, no version/label suffixes): {report_path.name}"
            )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(report, open(args.report, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('GATE VERDICT:', report['verdict'])
    sys.exit(0 if report['verdict'] == 'PASS' else 1)


def _load_json(p):
    return json.load(open(p, encoding='utf-8-sig'))


if __name__ == '__main__':
    main()
