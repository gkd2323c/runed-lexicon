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
from functools import lru_cache
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

    结果按文本内容缓存：gate 会对同一个 source 在 849 条 ban 上反复调用，
    实测这层重复清洗是热点之一。
    """
    return _strip_html_tags_cached(text)


_STRIP_HTML_RE = re.compile(r'<[^>]*>')


@lru_cache(maxsize=4096)
def _strip_html_tags_cached(text: str) -> str:
    return _STRIP_HTML_RE.sub('', text)


@lru_cache(maxsize=8192)
def _compiled_literal(needle: str):
    """Cache a case-insensitive literal pattern.

    原实现对每个 unit × 每个 term 都重新 re.compile（实测 500 万次以上），
    是 gate 最热的开销。"""
    return re.compile(re.escape(needle), re.IGNORECASE)


@lru_cache(maxsize=8192)
def _compiled_word(needle: str):
    """Cache the whole-word pattern built by _iter_word_matches."""
    forms = [needle]
    if needle.endswith('f') and len(needle) > 1:
        forms.append(needle[:-1] + 'ves')
    return re.compile(
        r'(?<![A-Za-z0-9_])(?:' + '|'.join(re.escape(f) for f in forms) + r')s?(?![A-Za-z0-9_])',
        re.IGNORECASE)


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
    # 快速预筛：大小写敏感的子串检查不命中时直接返回，避免进正则
    if eng not in source and eng.lower() not in source.lower():
        return []
    # Strip HTML tags to avoid matching inside markup attributes
    source_clean = _strip_html_tags(source)
    pat = _compiled_literal(eng)
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
    because the apostrophe is not a word char. Anchors ending in a single 'f'
    additionally match the irregular f→ves plural (Elf→Elves, so "High Elves"
    triggers the High Elf anchor)."""
    text_clean = _strip_html_tags(text)
    # 快速预筛：锚点首词不出现时直接返回（避免为每条 ban 跑完整正则）
    head = needle.split()[0] if needle.split() else needle
    if head not in text_clean and head.lower() not in text_clean.lower():
        return
    pat = _compiled_word(needle)
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
        record = {
            'english': eng,
            'forbidden': fb,
            'target': str(b.get('target') or '').strip(),
            'reason': str(b.get('reason') or '').strip(),
            # 无条件禁词（anachronism-*）：跳过英文锚检查，dest 含 forbidden 即拦
            'unconditional': b.get('unconditional') is True,
        }
        if isinstance(b.get('category'), str) and b['category'].strip():
            record['category'] = b['category'].strip()
        out.append(record)
    return out


def _target_tail_covered(dest: str, pos: int, f: str, target: str) -> bool:
    """R13: forbidden 是 target 子串时，命中后紧跟 target 剩余部分即豁免。

    允许剩余部分前隔省略号/空白——忠实还原 source 残缺形态（如 "Morning
    Star..." → "晨星...月..日"）的译文不是裸译错误。间隔只认 "..."、"…"、
    半角/全角空格，不认其他字符，所以 "晨星的光" 这类真裸用不受影响。
    forbidden 不在 target 内时返回 False，调用方走原覆盖逻辑。
    """
    if not target or f not in target:
        return False
    for tp in _iter_matches(target, f):
        tail = target[tp + len(f):]
        if not tail:
            return True  # forbidden 占 target 尾部：本就是规范形态的一部分
        j = pos + len(f)
        while dest.startswith('...', j) or dest.startswith('…', j):
            j += 3 if dest.startswith('...', j) else 1
        while j < len(dest) and dest[j] in (' ', '　'):
            j += 1
        if dest.startswith(tail, j):
            return True
    return False


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

    R13: forbidden 是 target 子串且命中后紧跟 target 剩余部分（允许间隔
    "..."/"…"/空白，见 _target_tail_covered）时同样豁免——source 残缺形态
    （如 "Morning Star..." 残缺日期）的忠实译文 "晨星...月" 不是裸译错误。
    Root cause of 1 false TERM004 FAIL in Druadach-book xml-index:8530
    (2026-09-08): "第十八..天：周一...，晨星...月..日" 的 "晨星" 被报，
    而其后 "...月" 正是 target 剩余部分。
    """
    if ban.get("unconditional") is not True:
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
        # 快速预筛：坏形态不在 dest 里时直接跳过（省去 _iter_matches 的函数调用开销）
        if f not in dest:
            continue
        for p in _iter_matches(dest, f):
            covered = any(s <= p and p + len(f) <= e for s, e in target_spans)
            if covered:
                continue  # part of the canonical form, not a bad variant
            if target and _target_tail_covered(dest, p, f, target):
                continue  # R13: 省略号/空白隔断的 target 剩余部分同样豁免
            hits.append(f)
            break  # one report per forbidden form (unchanged semantics)
    return hits


def find_global_keep_hits(source: str, dest: str, gkeep: str) -> bool:
    """Return True when a project-wide KEEP value (english) appears whole-word in
    source while dest differs from it — i.e. the KEEP value got translated."""
    if not list(_iter_word_matches(source, gkeep)):
        return False
    return dest.strip() != gkeep


def _anchor_present(source: str, anchor: str) -> bool:
    """跨条豁免用的锚点存在性检查：整词匹配优先，其次允许专名形容词派生
    （Altmer → Altmeri / Altmeris）。官方行会用形容词形态（"noble Altmeri
    blood"），而锚点只登记名词形；不放宽就会把合法覆盖判成未覆盖。

    仅用于豁免侧判断，不影响 TERM004 主检查的严格整词语义。
    """
    if list(_iter_word_matches(source, anchor)):
        return True
    if not anchor or not anchor[0].isupper():
        return False
    pat = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(anchor) + r'(?=[a-z])', re.IGNORECASE)
    return bool(pat.search(_strip_html_tags(source)))


def cross_target_covered(source: str, dest: str, variant: str, bans: list, self_eng: str) -> bool:
    """Return True when a global-ban forbidden hit is a false positive caused by
    a cross-ban target/forbidden overlap.

    Scenario: "波斯莫" is the canonical target of the Bosmer ban and at the
    same time a forbidden variant of the Wood Elf ban. When source contains the
    Bosmer anchor, dest "波斯莫" is the legitimate Bosmer target — firing the
    Wood Elf ban would be a false positive. This helper checks whether the hit
    is fully covered by an occurrence of *another* ban's target whose English
    anchor appears in source.

    Applies to unconditional bans too: unconditional skips the source-anchor
    check for the *current* ban, but cross-ban target coverage still needs the
    covering ban's own anchor to be present in source, otherwise the coverage
    is coincidental and the hit stays.
    """
    if not variant:
        return False
    variant_spans = [(p, p + len(variant)) for p in _iter_matches(dest, variant)]
    for other in bans:
        oeng = other.get('english') or ''
        otarget = (other.get('target') or '').strip()
        if not otarget or oeng == self_eng:
            continue
        if oeng and not _anchor_present(source, oeng):
            continue  # covering ban's anchor absent from source: no legitimate basis
        target_spans = [(p, p + len(otarget)) for p in _iter_matches(dest, otarget)]
        for vs, ve in variant_spans:
            if any(ts <= vs and ve <= te for ts, te in target_spans):
                return True
    return False


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


def _cross_term_target_covered(source: str, dest: str, variant: str,
                               extra_terms: Optional[Dict[str, Dict]],
                               self_tid: str) -> bool:
    """TERM002 跨条豁免：某个 forbidden 形态同时是另一条 term 的合法 target，
    且该条的英文锚点在源文出现时，本条命中是误报。

    场景：同一行同时含 Dwemer 和 Dwarven（如《巴塔尔-泽尔之谜》同时讨论两词），
    dwemer 条的 forbidden '矮人' 同时是 dwarven 条的 target '矮人'；译文里
    '矮人' 对应的是 Dwarven，行级检查却把它算到 Dwemer 头上。与 TERM004 的
    cross_target_covered 同源，但按 term 定义而非 ban 定义工作。

    保守条件：只豁免 otarget 与 variant 完全相等的情况，且覆盖方锚点必须在
    source 里真实出现；否则命中保留。
    """
    if not variant or not extra_terms:
        return False
    variant_spans = [(p, p + len(variant)) for p in _iter_matches(dest, variant)]
    if not variant_spans:
        return False
    for otid, oterm in extra_terms.items():
        if otid == self_tid or not isinstance(oterm, dict):
            continue
        otarget = (oterm.get('target') or '').strip()
        if otarget != variant:
            continue
        osrc = (oterm.get('source') or '').strip()
        if osrc and not list(_iter_word_matches(source, osrc)):
            continue  # 覆盖方锚点不在源文：没有合法依据，命中保留
        return True
    return False


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
            # 跨条 target 豁免：形态是另一条的合法 target 且其锚点在源文出现
            if _cross_term_target_covered(source, dest, f, extra_terms, tid):
                continue
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
