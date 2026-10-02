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

    v0.3.1：maxsize 从 4096 提到 65536。**4096 装不下单元数**——全库门禁有
    39,163 条已译行，每行一个不同的 source key，在 unit × term 双层循环里
    每条 source 被连着查 1,495 次，命中率本应接近 1494/1495。缓存一旦装不下，
    命中率归零、5,854 万次全部真跑正则 sub。容量对齐单元数量级后，这层清洗
    从热点退化成常数。内存代价约 10MB（key+value 各约 100 字符 × 39,163）。
    """
    return _strip_html_tags_cached(text)


_STRIP_HTML_RE = re.compile(r'<[^>]*>')


@lru_cache(maxsize=65536)
def _strip_html_tags_cached(text: str) -> str:
    return _STRIP_HTML_RE.sub('', text)


@lru_cache(maxsize=8192)
def _compiled_literal(needle: str, case_sensitive: bool = False):
    """Cache a literal pattern (case-insensitive by default).

    原实现对每个 unit × 每个 term 都重新 re.compile（实测 500 万次以上），
    是 gate 最热的开销。

    case_sensitive=True 时精确匹配大小写（v0.4.2）：源文大小写本身承载语义
    （大写 Command＝掌权者层级 / 小写 command＝普通动词「命令」）时由词条显式
    登记，门禁不再让小写普通义误触发大写专名的 REQUIRED 约束。
    标志进缓存键，两种模式互不污染。"""
    return re.compile(re.escape(needle), 0 if case_sensitive else re.IGNORECASE)


@lru_cache(maxsize=8192)
def _compiled_word(needle: str):

    """Cache the whole-word pattern built by _iter_word_matches."""
    forms = [needle]
    if needle.endswith('f') and len(needle) > 1:
        forms.append(needle[:-1] + 'ves')
    return re.compile(
        r'(?<![A-Za-z0-9_])(?:' + '|'.join(re.escape(f) for f in forms) + r')s?(?![A-Za-z0-9_])',
        re.IGNORECASE)


_SENTENCE_TAIL = ('.', '!', '?')
_QUOTE_PAIRS = (('"', '"'), ('“', '”'), ('‘', '’'), ('「', '」'), ('『', '』'))


def is_standalone_sentence(eng: str) -> bool:
    """True when the registered English *is* a whole sentence (ends in . ! ?).

    整句词条（`He did.`、`There it is.`、`Unknown.`）按字面量匹配时会命中更长句子里的
    同形子串：`It does not rewrite what he did.` 的尾部 `he did.` 逐字等于词条
    `He did.`，而匹配默认大小写不敏感，于是这行完全正确的译文也被报
    `TERM001: required target 未出现`。实测该词表只有 4 条英文以句末标点结尾，
    且全是真整句、无 `U.S.` / `Jr.` 这类缩写，故按「英文以句末标点结尾 ⇒ 登记的是
    一整句 ⇒ 只在源文整句就是它时命中」自动判定。

    逃生口：词条写 `"substring_match": true` 可退回宽松子串匹配，供将来的缩写型
    词条使用（见 find_source_hits）。
    """
    e = (eng or '').strip()
    return len(e) > 1 and e.endswith(_SENTENCE_TAIL)


def _core_span(text: str) -> tuple:
    """(lo, hi) of `text` stripped of surrounding whitespace and quote pairs.

    Iterative with a depth cap: a pathological nest of alternating quote pairs
    must not blow the stack on what is a hot path (called per unit per term).
    """
    lo, hi = 0, len(text)
    for _ in range(4):
        while lo < hi and text[lo].isspace():
            lo += 1
        while hi > lo and text[hi - 1].isspace():
            hi -= 1
        inner = text[lo:hi]
        for op, cl in _QUOTE_PAIRS:
            if len(inner) > 1 and inner[0] == op and inner[-1] == cl:
                lo, hi = lo + 1, hi - 1
                break
        else:
            return lo, hi
    return lo, hi


def find_source_hit_spans(source: str, eng: str, case_sensitive: bool = False,
                          standalone: bool = None) -> List[tuple]:
    """Return (start, end) spans of `eng` in `source` as whole words.

    Case-insensitive by default. `case_sensitive=True` (v0.4.2, opt-in per term)
    requires the anchor to appear with exactly the registered casing — used when
    source casing is itself semantic and the contract says so via
    definition['case_sensitive'].

    Same two R6 guards as before — HTML tag stripping and word-boundary check —
    but returns spans rather than bare start offsets so callers can test whether a
    hit is *contained* in a longer anchor. Offsets are into the HTML-stripped
    source, i.e. the same coordinate space the span math runs in.
    """
    if not eng:
        return []
    # 快速预筛：子串检查不命中时直接返回，避免进正则
    if case_sensitive:
        if eng not in source:
            return []
    elif eng not in source and eng.lower() not in source.lower():
        return []
    # Strip HTML tags to avoid matching inside markup attributes
    source_clean = _strip_html_tags(source)
    pat = _compiled_literal(eng, case_sensitive)
    spans = []
    for m in pat.finditer(source_clean):
        start, end = m.start(), m.end()
        # Word boundary check: preceding/following char must not be a word char
        if start > 0 and _is_word_char(source_clean[start - 1]):
            continue
        if end < len(source_clean) and _is_word_char(source_clean[end]):
            continue
        spans.append((start, end))
    # 整句词条只认整句：命中必须覆盖源文去掉空白与成对引号后的全部内容
    if spans and (is_standalone_sentence(eng) if standalone is None else standalone):
        lo, hi = _core_span(source_clean)
        spans = [s for s in spans if s[0] <= lo and s[1] >= hi]
    return spans


def maximal_spans(spans) -> List[tuple]:
    """Keep only the spans that are not strictly contained in another span.

    Input order is irrelevant; output is sorted by (start, longest first) so the
    containment test in `is_shadowed` can look at a short, stable covering list
    instead of every hit on the line.
    """
    out: List[tuple] = []
    for s, e in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if any(bs <= s and e <= be and (be - bs) > (e - s) for bs, be in out):
            continue
        out.append((s, e))
    return out


def is_shadowed(span: tuple, covering: List[tuple]) -> bool:
    """True when `span` lies entirely inside a strictly longer `covering` span.

    This is the "short anchor swallowed by a longer registered proper name" rule
    (R21). It only ever fires on *containment*, never on mere adjacency or on a
    shared prefix, so a bare short anchor sitting next to a long one still hits.
    """
    s, e = span
    return any(bs <= s and e <= be and (be - bs) > (e - s) for bs, be in covering)


def find_source_hits(source: str, term: Dict, covering_spans=None):
    """Return the term's English source form occurrences inside `source`.
    Only direct word/phrase occurrences count; caller already narrowed to
    auto-bind-safe terms whose English form is unambiguous.

    Two guards prevent substring false positives reported by R6:
    1. Word boundary check — preceding/following char must not be a word char.
       "Rathis" won't match "Athis" (preceded by 'a'), "Imperial" won't match
       "Ria" (preceded by 'I').
    2. HTML tag stripping — <font face="Adielle"> won't match "Adielle" because
       the attribute value is inside a tag and gets removed before matching.

    `covering_spans` (optional) are the spans of *longer* registered anchors on the
    same line. A hit swallowed whole by one of them is not a valid hit of this term
    (R21): the text there is part of the longer proper name, not this term. Pass
    them only from the multi-anchor resolution path; a lone single-term call keeps
    the original whole-word semantics untouched.

    v0.4.2: honors definition['case_sensitive'] — an opt-in per term, absent means
    the historical case-insensitive behavior.
    """
    # standalone=None → 按词条形状自动判定（整句词条只认整句）；
    # substring_match=true 是逃生口，显式退回宽松子串匹配（供缩写型词条）。
    spans = find_source_hit_spans(source, term.get('source') or '',
                                  bool(term.get('case_sensitive')),
                                  standalone=False if term.get('substring_match') else None)
    if covering_spans:
        covering = maximal_spans(covering_spans)
        spans = [sp for sp in spans if not is_shadowed(sp, covering)]
    return [s for s, _ in spans]


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
    """Return list of forbidden variants present in dest (TERM002).

    R18 (v0.1.9): forbidden 是 target 子串时，落在 target 完整区间内的实例豁免
    （与 TERM004 的 R12 同源）——防止短形禁令误伤正确长形（例：forbidden「琼」
    必须不能把 target「琼恩」中的「琼」报出来；未被 target 覆盖的独立「琼」
    仍照报）。此修复弥补 TERM002 路径此前只有简单子串检查、缺少子串豁免的缺口。
    """
    out = []
    target = (term.get('target') or '').strip()
    forbidden = term.get('forbidden') or []
    # 无 target 或 forbidden 与 target 无子串关系时走快速路径（原文逻辑）
    sub_related = [f for f in forbidden if f and target and f != target and f in target]
    if not sub_related:
        for f in forbidden:
            if f and f in dest:
                out.append(f)
        return out
    # 子串关系路径：先算 target 区间
    target_spans = [(p, p + len(target)) for p in _iter_matches(dest, target)] if target else []
    for f in forbidden:
        if not f or f not in dest:
            continue
        if f not in target:
            out.append(f)
            continue
        # f 是 target 子串：仅当存在未被任 target 区间覆盖的实例时报
        uncovered = False
        for p in _iter_matches(dest, f):
            covered = any(s <= p and p + len(f) <= e for s, e in target_spans)
            if not covered:
                uncovered = True
                break
        if uncovered:
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

# MOD 级全局禁用词白名单嵌入契约后的字段名（R21 豁免：TERM004 合法形态出口）。
#
# 为什么需要它：find_global_ban_hits 的中文侧是**子串检查**，而中文没有词边界。
# 「之内」+「存在」在文本流里相邻，拼出的字面「内存」会被 anachronism 禁令硬拦——
# 撞上就只剩两条路：改写措辞绕开（译文被迫变形），或从全局词库撤除该条（对别的
# MOD 生效的真实误报也就没了）。白名单把「合法形态」显式声明出来，落在 MOD 级，
# 不污染跨 MOD 词库。
CONTRACT_GLOBAL_BAN_EXEMPTIONS_KEY = 'global_ban_exemptions'

# 豁免 scope 内允许出现的键。未知键一律报错——拼错的键如果被静默忽略，
# 豁免会退化成「无条件的白名单」，正是这个机制要防的反面。
GLOBAL_BAN_EXEMPTION_SCOPE_KEYS = ('source_contains', 'dest_left', 'dest_right')


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
    head, head_lc = _anchor_head(needle)
    if head not in text_clean and head_lc not in _lower_cached(text_clean):
        return
    pat = _compiled_word(needle)
    for m in pat.finditer(text_clean):
        yield m.start()


@lru_cache(maxsize=8192)
def _anchor_head(needle: str):
    """(首词, 首词小写) —— 原先每次调用都做两次 needle.split()。

    v0.3.1：_iter_word_matches 处在 unit × term 双层循环里（全库 5,854 万次），
    每次重新 split 锚点字符串是纯浪费——锚点种类只有契约词条数那么多。
    """
    parts = needle.split()
    head = parts[0] if parts else needle
    return head, head.lower()


@lru_cache(maxsize=65536)
def _lower_cached(text: str) -> str:
    """text.lower() 缓存。

    v0.3.1：预筛里 `head.lower() not in text_clean.lower()` 会在每次调用时对
    **整行**做一次 lower()。同一行在 unit × term 循环里被查 1,495 次，于是同一
    行被 lower 1,495 次。缓存按行内容索引，命中后每行只 lower 一次。
    容量对齐单元数量级（全库 39,163），内存约 5~10MB。
    """
    return text.lower()


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
            # R20 allow_english：双实体共存豁免（如 Falmer 行同时讨论 Snow Elves）
            'allow_english': [a for a in (b.get('allow_english') or [])
                              if isinstance(a, str) and a.strip()],
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
    canonical form. Root cause: month bans list the bare word as forbidden and
    the 带月 form as target, so target and forbidden matched the same text.

    R13: forbidden 是 target 子串且命中后紧跟 target 剩余部分（允许间隔
    "..."/"…"/空白，见 _target_tail_covered）时同样豁免——source 残缺形态
    （如 "Morning Star..." 残缺日期）的忠实译文 "晨星...月" 不是裸译错误。
    Root cause: 残缺日期行（如 "第十八..天：周一...，晨星...月..日"）的 "晨星" 被报，
    而其后 "...月" 正是 target 剩余部分。

    R20 (v0.2.3): allow_english 双实体共存豁免——ban 声明 allow_english 列表时，
    源文同时含列表中任一锚（_anchor_present 变体容忍）且 dest 已含本 ban 的
    target 时，forbidden 命中判为合法另一实体的译名而非本 ban 的误译。
    场景：Falmer 行同时讨论 Snow Elves（"The Snow Elves fell long before the
    Falmer changed physically"），「雪精灵」是 Snow Elves 的官方译名而非 Falmer
    的剧透误译。target-present 条件保证真正漏译（把 Falmer 译成雪精灵且无
    「伐莫」）的行仍被拦截；allow 锚不在源文的行豁免不生效。
    """
    if ban.get("unconditional") is not True:
        if not list(_iter_word_matches(source, ban['english'])):
            return []
    allow = ban.get('allow_english') or []
    if allow:
        tgt = (ban.get('target') or '').strip()
        if tgt and tgt in dest and any(_anchor_present(source, a) for a in allow):
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


# ---------------------------------------------------------------- R21: MOD-level ban whitelist

def normalize_global_ban_exemptions(raw) -> List[Dict]:
    """Normalize raw whitelist records from the compiled contract.

    Tolerates a missing/!list payload (returns []) so an older compiled contract
    without the key keeps working. Structural validation lives in
    term-contract-compiler's load_global_ban_exemptions; this only reshapes for
    the gate's hot path.
    """
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        f = str(e.get('forbidden') or '').strip()
        if not f:
            continue
        scope = e.get('scope') if isinstance(e.get('scope'), dict) else {}
        out.append({
            'forbidden': f,
            'english': str(e.get('english') or '').strip(),
            'reason': str(e.get('reason') or '').strip(),
            'scope': {k: [x for x in (scope.get(k) or [])
                          if isinstance(x, str) and x]
                      for k in GLOBAL_BAN_EXEMPTION_SCOPE_KEYS
                      if scope.get(k)},
        })
    return out


def index_global_ban_exemptions(exemptions: List[Dict]) -> Dict[str, List[Dict]]:
    """Group exemptions by forbidden form once, outside the per-unit loop.

    The gate evaluates this only on actual TERM004 hits, but building the index
    per call would still be a linear scan of the whole whitelist per hit.
    """
    idx: Dict[str, List[Dict]] = {}
    for ex in exemptions or []:
        idx.setdefault(ex.get('forbidden') or '', []).append(ex)
    return idx


def _exemption_scope_matches(scope: Dict, source: str, dest: str, variant: str) -> bool:
    """True when every scope condition holds for this forbidden-form hit.

    source_contains — any-of, case-insensitive, matched against the tag-stripped
    source. Scopes the exemption to lines that are about the right subject.

    dest_left / dest_right — the characters immediately adjoining the hit. This
    is the clause that separates a real banned word from a cross-word-boundary
    artifact: 「…之内存在…」 matches 内存 only because 内 and 存 happen to
    abut, and requiring 之 on the left / 在 on the right says exactly that.
    EVERY occurrence of the form in dest must satisfy the constraint, so a line
    that also contains a genuine bare 内存 keeps failing.
    """
    needles = scope.get('source_contains') or []
    if needles:
        low = source.lower()
        if not any(n.lower() in low for n in needles):
            return False
    left = scope.get('dest_left') or []
    right = scope.get('dest_right') or []
    if left or right:
        n = len(variant)
        for p in _iter_matches(dest, variant):
            if left and not any(dest[max(0, p - len(x)):p] == x for x in left):
                return False
            if right and not any(dest[p + n:p + n + len(x)] == x for x in right):
                return False
    return True


def find_global_ban_exemption(source: str, dest: str, variant: str,
                              ex_index: Dict[str, List[Dict]]) -> Optional[Dict]:
    """Return the whitelist record that legalizes `variant` on this line, else None.

    ex_index is the output of index_global_ban_exemptions. Returning the record
    (rather than a bool) lets the gate echo the declared reason into the report,
    so an exemption stays auditable instead of silently swallowing the finding.
    """
    if not ex_index or not variant:
        return None
    candidates = ex_index.get(variant)
    if not candidates:
        return None
    clean = _strip_html_tags(source)
    for ex in candidates:
        if _exemption_scope_matches(ex.get('scope') or {}, clean, dest, variant):
            return ex
    return None


def find_global_keep_hits(source: str, dest: str, gkeep: str) -> bool:
    """Return True when a project-wide KEEP value (english) appears whole-word in
    source while dest differs from it — i.e. the KEEP value got translated."""
    if not list(_iter_word_matches(source, gkeep)):
        return False
    return dest.strip() != gkeep


def _anchor_present(source: str, anchor: str, case_sensitive: bool = False) -> bool:
    """跨条豁免用的锚点存在性检查：整词匹配优先，其次允许专名形容词派生
    （Altmer → Altmeri / Altmeris）。官方行会用形容词形态（"noble Altmeri
    blood"），而锚点只登记名词形；不放宽就会把合法覆盖判成未覆盖。

    v0.1.8（R17）：连接符变体识别——插件作者会把专名写成 "High-elf"、
    "alt-mer" 这类连字符拼法；此前不识别导致跨条豁免失效、把正确译文误判为
    触发（INFO-035 incident: Altmer/High Elf 两处互搏被误拦）。多词锚点允许
    词间用 [\\s-]+ 连接（High Elf → High-elf）；单词锚点枚举单点插连字符变体
    （Altmer → alt-mer）。仅豁免侧生效，不影响 TERM004 主检查。

    仅用于豁免侧判断，不影响 TERM004 主检查的严格整词语义。

    v0.4.4：`case_sensitive=True` 时走精确大小写的整词判定。这是**必需**的——
    本函数是唯一被 standalone_forbidden_issues（TERM002 独立路径）用来判断
    「这条词条适不适用于本行」的闸门，而它的匹配器 _iter_word_matches 硬编码
    re.IGNORECASE。真实事故 TheKalpicAnomaly_GLENMORIL idx 9039：词条
    `the Serpent` 已开 case_sensitive（源文含 serpent 共 14 行，大写 Serpent
    12 行 → 巨蛇，小写 serpent 仅 9038/9039 两行 → 巨蟒，一对一），但
    standalone_forbidden_issues 不传本参数，仍按大小写不敏感命中，把正确的
    「巨蟒」误报成 TERM002 forbidden 变体——**开字段不管用，全库门禁照报**。
    派生/连字符放宽分支只对大小写不敏感的主路径有意义，敏感路径直接精确判定。
    """
    if case_sensitive:
        # standalone=False：豁免闸门保持历史的整词语义，不套「整句词条只认整句」
        return bool(find_source_hit_spans(source, anchor, True, standalone=False))
    if list(_iter_word_matches(source, anchor)):
        return True
    if not anchor or not anchor[0].isupper():
        return False
    stripped = _strip_html_tags(source)
    # 形容词派生（原有分支）：Altmer → Altmeri
    if _compiled_derived(anchor).search(stripped):
        return True
    # 连字符变体（v0.1.8）：词间或词内由 '-' 连接
    if _compiled_hyphen(anchor).search(stripped):
        return True
    return False


@lru_cache(maxsize=4096)
def _compiled_derived(anchor: str):
    """Compiled pattern for the adjective-derivation branch of _anchor_present.

    性能（v0.3.1）：本函数原先在函数体内直接 re.compile，而调用方
    standalone_forbidden_issues 是 **unit × term 双层循环**——1,495 词条 ×
    39,163 行 = 5,854 万次调用。`re.compile` 自带的 `_cache` 只有 512 项，
    1,495 个不同 pattern 必然击穿它，每次都真编译。实测该路径是全库门禁
    52.78s（不带 auto-bind）的主要构成。lru_cache 把编译次数降到锚点种类数。
    语义不变：同一 anchor 永远编译出同一 pattern。
    """
    return re.compile(r'(?<![A-Za-z0-9_])' + re.escape(anchor) + r'(?=[a-z])', re.IGNORECASE)


@lru_cache(maxsize=4096)
def _compiled_hyphen(anchor: str):
    """Compiled pattern for the hyphen-variant branch of _anchor_present (v0.1.8).

    缓存理由同 _compiled_derived：这是 unit × term 双层循环里的热点编译点。
    """
    words = anchor.split()
    if len(words) == 1 and '-' not in anchor:
        alts = [re.escape(anchor)]
        for i in range(1, len(anchor)):
            alts.append(re.escape(anchor[:i]) + '-' + re.escape(anchor[i:]))
        core = '(?:' + '|'.join(alts) + ')'
    else:
        core = r'[\s\-]+'.join(re.escape(w) for w in words)
    return re.compile(
        r'(?<![A-Za-z0-9_])' + core + r'(?=[a-z]|[^A-Za-z0-9_]|$)',
        re.IGNORECASE,
    )


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
        if osrc:
            # v0.4.4：覆盖方词条自己声明了 case_sensitive 就必须按精确大小写判锚点，
            # 否则会出现「不区分大小写地豁免」——只认大写的词条被小写源文豁免掉。
            if oterm.get('case_sensitive'):
                covered = bool(find_source_hit_spans(source, osrc, True, standalone=False))
            else:
                covered = bool(list(_iter_word_matches(source, osrc)))
            if not covered:
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
