# -*- coding: utf-8 -*-
"""term_match.py — deterministic term presence / forbidden / variant matching.

Term Definition match semantics (schema v1):
  kind=forms:            accepted 项需"完整出现"（中文无空格，按字符串整体判定）；
                        若命中位置后紧跟 blocked_suffixes 中任一项 → 视为变体而非正确出现。
  kind=contains_phrase:  accepted 任一项作为连续子串出现即算（用于无派生歧义的固定短语）。

本模块只读，不做任何修正。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------- auto-bind helpers

def auto_bind_candidates(term: Dict) -> bool:
    """A term is safe for auto-binding when it is REQUIRED and carries no risk flags.
    The compiler already downgrades ambiguous/spoiler terms to FORBIDDEN_ONLY with
    risk_flags, so enforcement==REQUIRED + no risk is the safe auto-bind set."""
    return (term.get('enforcement') == 'REQUIRED' and not term.get('risk_flags'))


def _is_word_char(c):
    """Check if a character is a word character (alphanumeric or underscore)."""
    return c.isalnum() or c == '_'


def _strip_html_tags(text):
    """Remove HTML tags from text to prevent matching inside markup attributes.

    Auto-bind matches inside HTML tag attributes (e.g. <font face="Adielle">)
    are false positives — the term is not visible text. Stripping tags first
    ensures only visible-text occurrences trigger auto-binding.
    """
    return re.sub(r'<[^>]*>', '', text)


def find_source_hits(source: str, term: Dict):
    """Return the term's English source form occurrences inside `source`.
    Only direct word/phrase occurrences count; caller already narrowed to
    auto-bind-safe terms whose English form is unambiguous.

    Two guards prevent substring false positives reported by R6:
    1. Word boundary check — preceding/following char must not be a word char.
       "Rathis" won't match "Athis" (preceded by 'a'), "Imperial" won't match
       "Ria" (preceded by 'I').
    2. HTML tag stripping — <font face="Adielle"> won't match "Adielle" because
       the attribute value is inside a tag and gets removed before matching.
    """
    eng = term.get('source') or ''
    if not eng:
        return []
    # Strip HTML tags to avoid matching inside markup attributes
    source_clean = _strip_html_tags(source)
    pat = re.compile(re.escape(eng), re.IGNORECASE)
    hits = []
    for m in pat.finditer(source_clean):
        start, end = m.start(), m.end()
        # Word boundary check: preceding/following char must not be a word char
        if start > 0 and _is_word_char(source_clean[start - 1]):
            continue
        if end < len(source_clean) and _is_word_char(source_clean[end]):
            continue
        hits.append(start)
    return hits


# ---------------------------------------------------------------- matching

def _iter_matches(text: str, needle: str):
    """Yield all start indices of needle in text (non-overlapping)."""
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return
        yield i
        start = i + len(needle)


def match_required_present(dest: str, term: Dict) -> bool:
    """Return True if dest satisfies the term's required appearance (target found)."""
    match = term.get('match') or {}
    kind = match.get('kind', 'forms')
    accepted = match.get('accepted') or ([term['target']] if term.get('target') else [])
    blocked = match.get('blocked_suffixes') or []
    if not accepted:
        return False

    if kind == 'contains_phrase':
        # any accepted phrase present as substring counts
        for a in accepted:
            if a and a in dest:
                return True
        return False

    # kind == 'forms': accepted must appear and must NOT be immediately followed by
    # a blocked suffix (guards "阿尔贡尼亚人" containing "阿尔贡").
    for a in accepted:
        if not a:
            continue
        for pos in _iter_matches(dest, a):
            tail = dest[pos + len(a):]
            if any(tail.startswith(bs) for bs in blocked):
                continue  # blocked variant, not a clean appearance
            return True
    return False


def find_forbidden_hits(dest: str, term: Dict) -> List[str]:
    """Return list of forbidden variants present in dest (TERM002)."""
    out = []
    for f in term.get('forbidden') or []:
        if f and f in dest:
            out.append(f)
    return out


def find_blocked_suffix_hits(dest: str, term: Dict) -> List[str]:
    """Return list of accepted substrings that appear followed by a blocked suffix (TERM003)."""
    match = term.get('match') or {}
    accepted = match.get('accepted') or ([term['target']] if term.get('target') else [])
    blocked = match.get('blocked_suffixes') or []
    hits = []
    for a in accepted:
        if not a:
            continue
        for pos in _iter_matches(dest, a):
            tail = dest[pos + len(a):]
            for bs in blocked:
                if tail.startswith(bs):
                    hits.append(a + bs)
                    break
    return hits


# ---------------------------------------------------------------- project-wide ban list (TERM004)

# 项目级全局禁用词库（global-forbidden-words.json）嵌入契约后的字段名。
# 每一条：
#   english: source 侧英文锚点（忽略大小写，按整词出现触发）
#   forbidden: 中文坏形态列表（任一出现在 dest 即报；被 target 完整覆盖的
#              实例豁免——见 find_global_ban_hits 的 R12 说明）
#   target: 正确中文形态（dest 中任一出现区间完整覆盖其子串 forbidden 实例时豁免）
#   reason: 为什么禁（写进 report，供 Agent 判误报/修正）
CONTRACT_GLOBAL_BANS_KEY = 'global_bans'

# 项目级全局 KEEP 清单嵌入契约后的字段名（KEEP002：全局 KEEP 源值被翻译）。
CONTRACT_GLOBAL_KEEP_KEY = 'global_keep'


def _iter_word_matches(text: str, needle: str):
    """Yield all start indices of needle in text as a whole word (case-insensitive).
    A whole-word match means the char before and after the hit is not a word char.
    HTML tags are stripped first so markup attributes never match.

    Optional trailing 's' is allowed so plural forms of an anchor (Argonians,
    Spriggans, mudcrabs) still trigger; an apostrophe-s ('s) also keeps the hit
    because the apostrophe is not a word char."""
    text_clean = _strip_html_tags(text)
    pat = re.compile(r'(?<![A-Za-z0-9_])(?:' + re.escape(needle) + r')s?(?![A-Za-z0-9_])', re.IGNORECASE)
    for m in pat.finditer(text_clean):
        yield m.start()


def resolve_global_bans(contract: Dict) -> List[Dict]:
    """Return the project-wide ban list carried in the contract under
    'global_bans' (a list of ban records), normalized to records with
    english / forbidden / target / reason."""
    bans = contract.get(CONTRACT_GLOBAL_BANS_KEY) or []
    if not isinstance(bans, list):
        return []
    out = []
    for b in bans:
        if not isinstance(b, dict):
            continue
        eng = str(b.get('english') or '').strip()
        fb = b.get('forbidden') or []
        if isinstance(fb, str):
            fb = [x.strip() for x in fb.split(',') if x.strip()]
        fb = [x.strip() for x in fb if x and isinstance(x, str)]
        if not eng or not fb:
            continue
        out.append({
            'english': eng,
            'forbidden': fb,
            'target': str(b.get('target') or '').strip(),
            'reason': str(b.get('reason') or '').strip(),
        })
    return out


def find_global_ban_hits(source: str, dest: str, ban: Dict) -> List[str]:
    """Return forbidden variants of this ban that actually appear in dest,
    but only when the ban's English anchor appears in source.

    English side is a whole-word anchor (so 'blades' poetry cannot trigger
    the 'Blades' organization ban; plural / 's forms still hit). Chinese side
    is a substring check — a curated wrong form in dest is wrong regardless of
    whether the canonical form also appears (e.g. "木灵遭到树精袭击").

    R12 (v0.1.5): a forbidden occurrence fully covered by an occurrence of the
    ban's canonical target is part of the correct form, not a bad variant, and
    is suppressed ("晨星" inside "晨星月" must not fire when the full month name
    is present). Occurrences NOT covered by the target still fire, so a bare
    missing-月 error keeps being caught even in a unit that also contains the
    canonical form. Root cause of 20 false TERM004 FAILs in Druadach-book
    (2026-09-08): month bans list the bare word as forbidden and the 带月 form
    as target, so target and forbidden matched the same text.
    """
    if not list(_iter_word_matches(source, ban['english'])):
        return []
    target = (ban.get('target') or '').strip()
    target_spans = []
    if target:
        target_spans = [(p, p + len(target)) for p in _iter_matches(dest, target)]
    hits = []
    for f in ban['forbidden']:
        if not f:
            continue
        for p in _iter_matches(dest, f):
            covered = any(s <= p and p + len(f) <= e for s, e in target_spans)
            if covered:
                continue  # part of the canonical form, not a bad variant
            hits.append(f)
            break  # one report per forbidden form (unchanged semantics)
    return hits


def find_global_keep_hits(source: str, dest: str, gkeep: str) -> bool:
    """Return True when a project-wide KEEP value (english) appears whole-word in
    source while dest differs from it — i.e. the KEEP value got translated."""
    if not list(_iter_word_matches(source, gkeep)):
        return False
    return dest.strip() != gkeep


# ---------------------------------------------------------------- contract helpers

@dataclass
class ResolvedTerm:
    term_id: str
    definition: Dict
    required: bool
    required_target: Optional[str]
    source_span: str = ''

    @property
    def target(self) -> str:
        return self.required_target or self.definition.get('target') or ''


def resolve_bindings(contract: Dict) -> List[ResolvedTerm]:
    """Resolve contract into a flat list of (term, required, target) per binding.

    Only bindings with required=True produce appearance checks.
    Term index is optional: if a binding lacks a definition, it is skipped with a warning
    (collected via return of a parallel warnings list not used here; caller validates).
    """
    terms = contract.get('terms') or {}
    out = []
    for ub in contract.get('unit_bindings') or []:
        for b in ub.get('bindings') or []:
            term = terms.get(b['term_id'])
            if term is None:
                continue
            out.append(ResolvedTerm(
                term_id=b['term_id'],
                definition=term,
                required=bool(b.get('required', False)),
                required_target=b.get('required_target'),
                source_span=b.get('source_span', ''),
            ))
    return out


def check_unit(source: str, dest: str, resolved: List[ResolvedTerm],
               extra_terms: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Run term checks for one unit against its resolved bindings.

    Returns list of issue dicts: {code, term_id, severity, detail, expected}.
    """
    issues = []
    for rt in resolved:
        term = rt.definition
        tid = rt.term_id
        if rt.required:
            if not match_required_present(dest, term):
                issues.append({
                    'code': 'TERM001', 'term_id': tid, 'severity': 'FAIL',
                    'detail': f"required target 未出现: {rt.target!r}",
                    'expected': rt.target,
                })
        # forbidden always checked for bound terms
        for f in find_forbidden_hits(dest, term):
            issues.append({
                'code': 'TERM002', 'term_id': tid, 'severity': 'FAIL',
                'detail': f"forbidden 变体出现: {f!r}",
                'expected': 'not in ' + repr(term.get('forbidden', [])),
                'variant': f,
            })
        # blocked-suffix variants (TERM003)
        for hit in find_blocked_suffix_hits(dest, term):
            issues.append({
                'code': 'TERM003', 'term_id': tid, 'severity': 'FAIL',
                'detail': f"含 target 子串但带禁后缀: {hit!r}",
                'expected': 'no blocked suffix after ' + repr(rt.target),
            })
    return issues
