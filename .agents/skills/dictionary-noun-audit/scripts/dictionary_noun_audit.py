# -*- coding: utf-8 -*-
"""Dictionary noun audit for translated xTranslator XML / translation-result JSON.

Scans translated rows and reports CHECK candidates: rows whose Source contains an
official dictionary noun while the Dest does not contain any official Chinese form
of that noun.

This tool is a *candidate finder*, not a judge:
  - It reports string facts only. Whether a candidate is a real error, a legitimate
    anaphora elision, or a context-specific official form is the Agent's decision.
  - It never edits, never auto-fixes, never upgrades a candidate to REVIEW.

Exit code: 0 on success (flags found or not). Non-zero only on real failures
(missing dictionary, unparsable XML). Use --fail-on-flags for CI-style gating.

Usage:
  py -3 scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml
  py -3 scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml --entity "Skyrim|Delphine"
  py -3 scripts/dictionary_noun_audit.py mods/<plugin>/<file>_translated.xml --include-single
  py -3 scripts/dictionary_noun_audit.py <translation-result.json> --json out.json
"""
import json
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]

# ---------------------------------------------------------------------------
# evidence loading
# ---------------------------------------------------------------------------

def norm(s):
    if s is None:
        return ''
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.replace('&apos;', chr(39)).replace('&quot;', chr(34)).replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return re.sub(r'\s+', ' ', s).strip()


# Record families whose standalone single-word source is almost certainly a
# unique world-entity name (people, places, races, factions). A single word
# found in these is enabled for default audit.
STRONG_RECS = {'NPC_', 'WRLD', 'LCTN', 'CELL', 'REFR', 'RACE', 'FACT', 'REGN', 'HDPT'}

# Semantic families whose members must keep an official Chinese form in the
# Dest even though their dictionary rows live in weak prose records (GMST and
# the like). Record structure is a proxy for "entity name"; these families are
# that same guarantee made explicit at the semantic level, so a model that
# transliterates a weekday ('Morndas' -> 莫恩达斯) or month instead of
# recognizing it still gets caught.
#
# Each family maps member -> official dest(s). Evidence is the dictionary tree
# (families are asserted at import time by find_dictionary_family_terms); a
# member the dictionary does not prove is a code bug, not a silent skip.
#
# Weekday names come straight from Skyrim's Tamrielic calendar (GMST rows in
# Skyrim_english_chinese.xml -> 周一..周日). Months are the same weak-record
# family and are folded in so both halves of a date share one guarantee.
SEMANTIC_FAMILIES = {
    'weekday': {
        'Morndas': '周一', 'Tirdas': '周二', 'Middas': '周三',
        'Turdas': '周四', 'Fredas': '周五', 'Loredas': '周六',
        'Sundas': '周日',
    },
    'month': {
        'Morning Star': '晨星月', "Sun's Dawn": '日晓月',
        'First Seed': '初种月', "Rain's Hand": '雨手月',
        'Second Seed': '次种月', 'Mid Year': '年中月',
        "Sun's Height": '日高月', 'Last Seed': '末种月',
        'Heartfire': '炉火月', 'Frostfall': '霜落月',
        "Sun's Dusk": '日暮月', 'Evening Star': '夜星月',
    },
}

# Members that have no official Chinese dest anywhere in the dictionary tree.
# Filled at import time (after evidence loads) and reused by the index builder.
FAMILY_TERMS_MISSING_EVIDENCE = set()
FAMILY_TERMS_EVIDENCE = {}


def find_dictionary_family_terms(evidence):
    """Assert the semantic families against loaded dictionary evidence.

    Returns (missing, by_member) where by_member is member -> official dests
    actually proven by the dictionary. Members the dictionary does not prove
    are collected in missing (a code/evidence bug) and the audit later reports
    them on stderr instead of silently skipping.
    """
    missing = set()
    by_member = {}
    for fam, members in SEMANTIC_FAMILIES.items():
        for member in members:
            dests = evidence.get(member, {}).get('zh', [])
            by_member[member] = dests
            if not dests:
                missing.add(f'{fam}:{member}')
    return missing, by_member


def has_semantic_zh(dst, member, by_member):
    """Does the normalized Dest contain an official Chinese form of member?
    Only matches members the dictionary proves (no empty-match traps)."""
    dests = by_member.get(member) or []
    if not dests:
        return False
    visible = re.sub(r'<[^>]*>', ' ', dst or '')
    visible = re.sub(r'&lt;[^&]*&gt;', ' ', visible)
    return any(d in visible for d in dests)

# Record families that name items/spells/mechanics/titles: multi-word names in
# them are reliable, but a single word there is often an ordinary noun
# (Time/Life/Diamond/Warrior). Single words only in these stay optional.
ITEM_RECS = {'WEAP', 'ARMO', 'SPEL', 'MGEF', 'ENCH', 'ALCH', 'INGR', 'SLGM', 'KEYM',
             'DOOR', 'FURN', 'CONT', 'ACTI', 'TACT', 'LSCR', 'PERK', 'WOOP', 'SHOU',
             'TREE', 'FLOR', 'IDLE', 'PROJ', 'HAZD', 'LIGH', 'MSTT', 'STAT', 'TXST',
             'WTHR', 'EFSH', 'EXPL', 'IMAD', 'AMMO', 'LVLI', 'LVLN', 'LVSP', 'OTFT',
             'CLAS', 'QUST', 'MESG'}


def load_dictionary_evidence(dict_dir):
    """Recursively read dictionary/**/*.xml (whole tree is official evidence).
    Parse each <String> element structurally (EDID / REC / Source / Dest),
    never relying on field adjacency or ordering.

    Returns dict: english_source -> {
        'zh': [chinese dests...],        # dedup, CJK dests only
        'recs': {record_family: [edid...]},
        'evidence': [(rec, edid, dest), ...],   # first few rows for Agent review
    }
    """
    files = sorted(Path(dict_dir).rglob('*.xml'))
    if not files:
        return {}
    evidence = {}
    failed_files = []
    for fp in files:
        try:
            root = ET.fromstring(fp.read_text(encoding='utf-8-sig'))
        except (ET.ParseError, UnicodeDecodeError, OSError) as exc:
            failed_files.append((str(fp), str(exc)))
            continue
        content = root.find('Content')
        if content is None:
            continue
        for s in content.findall('String'):
            rec_el = s.find('REC')
            edid_el = s.find('EDID')
            src_el = s.find('Source')
            dst_el = s.find('Dest')
            if rec_el is None or src_el is None or dst_el is None:
                continue
            rec = (rec_el.text or '').strip()
            edid = (edid_el.text or '').strip()
            src = norm(src_el.text)
            dst = norm(dst_el.text)
            if not src or not dst:
                continue
            if not re.search(r'[\u4e00-\u9fff]', dst):
                continue  # English-only dest is not translation evidence
            if len(src) > 120 or len(dst) > 120:
                continue
            family = rec.split(':')[0] if ':' in rec else rec
            e = evidence.setdefault(src, {'zh': [], 'recs': {}, 'evidence': []})
            if dst not in e['zh']:
                e['zh'].append(dst)
            e['recs'].setdefault(family, [])
            if edid not in e['recs'][family]:
                e['recs'][family].append(edid)
            if len(e['evidence']) < 6:
                e['evidence'].append({'rec': rec, 'edid': edid, 'file': fp.name, 'dest': dst})
    if failed_files:
        print(f'warning: {len(failed_files)} dictionary file(s) failed to parse:', file=sys.stderr)
        for fp, err in failed_files[:5]:
            print(f'  - {fp}: {err}', file=sys.stderr)
        if len(failed_files) > 5:
            print(f'  - ... and {len(failed_files) - 5} more', file=sys.stderr)
    return evidence


def merge_trailing_punct_keys(evidence):
    """Official rows sometimes store the same name with trailing punctuation
    ('Whiterun' vs 'Whiterun.' vs 'Whiterun!'). Merge punct-suffixed keys into
    their bare counterpart so the bare name is the canonical key."""
    merged = {}
    for key, e in evidence.items():
        bare = re.sub(r'[.,:;!?，。！？、…]+$', '', key)
        if bare != key and bare in evidence:
            base = merged.setdefault(bare, {'zh': list(evidence[bare]['zh']),
                                            'recs': {k: list(v) for k, v in evidence[bare]['recs'].items()},
                                            'evidence': list(evidence[bare]['evidence'])})
            for d in e['zh']:
                if d not in base['zh']:
                    base['zh'].append(d)
            for r, eds in e['recs'].items():
                base['recs'].setdefault(r, [])
                for ed in eds:
                    if ed not in base['recs'][r]:
                        base['recs'][r].append(ed)
            for ev in e['evidence']:
                if len(base['evidence']) < 6 and ev not in base['evidence']:
                    base['evidence'].append(ev)
        else:
            merged.setdefault(key, e)
    return merged


# Sentence heads / ordinary words that must never start an entity match.
STOP_HEADS = {
    'The', 'A', 'An', 'I', 'You', 'He', 'She', 'It', 'We', 'They', 'My', 'Your',
    'His', 'Her', 'Our', 'Their', 'This', 'That', 'These', 'Those', 'There', 'Here',
    'What', 'Why', 'How', 'When', 'Where', 'Who', 'Whom', 'Whose', 'If', 'But',
    'And', 'Or', 'So', 'Well', 'Oh', 'Ok', 'Okay', 'Yes', 'No', 'Then', 'Now',
    'Because', 'After', 'Before', 'While', 'During', 'As', 'By', 'For', 'With',
    'On', 'At', 'In', 'To', 'From', 'Of', 'Not', "Let's", 'Let', 'Don', 'Do',
    'Did', 'Does', 'Can', 'Could', 'Will', 'Would', 'Should', 'May', 'Might',
    'Must', 'Look', 'Wait', 'Hey', 'Hmm', 'Please', 'Sorry', 'Thank', 'Thanks',
    'Good', 'Great', 'Come', 'Go', 'Get', 'Make', 'Say', 'Tell', 'Ask', 'See',
    'Know', 'Think', 'Feel', 'Want', 'Need', 'Have', 'Has', 'Had', 'Was', 'Were',
    'Is', 'Are', 'Be', 'Been', 'Being', 'Maybe', 'Perhaps', 'Alright', 'Sure',
    'Really', 'Even', 'Still', 'Just', 'Only', 'Also', 'Too', 'Very', 'Much',
    'Many', 'Some', 'Any', 'All', 'One', 'Two', 'First', 'Last', 'Next',
    'Another', 'Other', 'Every', 'Each', 'Both', 'Few', 'Once', 'Never',
    'Always', 'Soon', 'Later', 'Today', 'Yesterday', 'Tomorrow', 'Back', 'Away',
    'Home', 'People', 'Men', 'Women', 'Children', 'Everyone', 'Nobody',
    'Someone', 'Something', 'Anything', 'Nothing', 'Everything',
}


# Common verb heads that mark a phrase as an instruction/task prompt, not a name
# (e.g. 'Speak to Arngeir' is a quest objective, not a proper noun).
VERB_HEADS = {
    'speak', 'talk', 'go', 'come', 'find', 'search', 'kill', 'defeat', 'return',
    'take', 'bring', 'get', 'give', 'meet', 'see', 'ask', 'tell', 'follow', 'travel',
    'walk', 'run', 'leave', 'enter', 'escape', 'steal', 'read', 'use', 'open', 'close',
    'help', 'save', 'protect', 'attack', 'fight', 'slay', 'hunt', 'collect', 'gather',
    'deliver', 'escort', 'investigate', 'explore', 'learn', 'learns', 'talked', 'spoke',
    'started', 'start', 'end', 'ended', 'finish', 'complete', 'cleared', 'clean',
    'cleansed', 'cleaned', 'purify', 'defend', 'rescue', 'free', 'stop', 'destroy',
    'convince', 'persuade', 'warn', 'warns', 'confront', 'locate', 'recover', 'retrieve',
    'speaking', 'talking', 'listening', 'waiting', 'wait', 'checked', 'checking', 'check',
    'pick', 'picked', 'picking',
}


def classify_keys(evidence, include_single=False):
    """Split dictionary keys into (enabled_keys, optional_keys).

    enabled (default-audit) rules:
      - multi-word capitalized source (>= 2 words) -> proper noun, enabled
      - single word whose standalone rows appear in a STRONG_REC family
        (WRLD/LCTN/CELL/NPC_/RACE/FACT/...) -> proper noun, enabled
      - single word appearing ONLY in weak records (BOOK/MISC/GMST/INFO/DIAL)
        -> ordinary-word candidate; enabled only with include_single or --entity

    Data note: a word is enabled when the official record structure itself proves
    it is an entity name (WRLD:FULL Skyrim -> 天际, NPC_:FULL Delphine -> 戴尔芬,
    RACE:FULL Argonian -> 阿尔贡). BOOK/MISC single words like 'Letter' or 'Note'
    stay optional because they are ambiguous ordinary words there.
    """
    enabled = {}
    optional = {}
    for key, e in evidence.items():
        words = key.split()
        if not (1 <= len(words) <= 6):
            continue
        if not words[0][:1].isupper():
            continue
        recs = set(e['recs'].keys())
        if len(words) >= 2:
            # multi-word keys are near-unambiguous names; keep them enabled and
            # tolerate internal apostrophes (M'aiq the Liar), hyphens and
            # lowercase cultural particles (Lash gra-Shugurz). Exceptions that
            # drop to optional:
            #   - contraction-headed phrases (Let's go / I'm back / You're fine)
            #     are dialogue, not names
            #   - verb-headed task prompts (Speak to X / Search the ...)
            head0 = words[0].rstrip('.,:;!?')
            h = head0.lower()
            # contraction-headed phrases are dialogue, not names
            contraction = h in {"let's", "i'm", "you're", "you've", "you'll", "don't",
                                "can't", "won't", "isn't", "aren't", "wasn't", "weren't",
                                "that's", "it's", "what's", "there's", "he's", "she's",
                                "we're", "we've", "they're", "didn't", "doesn't", "couldn't",
                                "wouldn't", "shouldn't", "haven't", "hasn't", "i'll", "i've",
                                "i'd", "let us", "gotta", "wanna", "kinda"}
            if contraction:
                optional[key] = e
            elif h in VERB_HEADS and h not in {'the', 'a', 'an'}:
                optional[key] = e
            elif head0 in STOP_HEADS and h not in {'the', 'a', 'an'}:
                # sentence/dialogue heads (We/Not/No/Why/...) are not names,
                # except article-headed titles (The Bee and Barb / The Pale Lady)
                optional[key] = e
            else:
                enabled[key] = e
        else:
            # single word: enabled only when a STRONG (people/place/race) record
            # family proves it. Item/spell/mechanic records (ITEM_RECS) or
            # prose-only words stay optional — a single word there is often an
            # ordinary noun (Time/Life/Diamond).
            if recs & STRONG_RECS:
                enabled[key] = e
            else:
                optional[key] = e
    return enabled, optional


# ---------------------------------------------------------------------------
# entity matching with deterministic boundary handling
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# row audit
# ---------------------------------------------------------------------------

SEMANTIC_TOKENS = set()  # filled by classify_keys' caller when evidence loads


def build_lookup_maps(enabled_keys, optional_keys):
    """Pre-build normalized lookup maps once per audit run (not per row).
    norm strips trailing possessive and lowercases."""
    def norm_key(k):
        nk = k.lower()
        nk = re.sub(r"(?:'s|’s)$", '', nk)
        return nk
    return ({norm_key(k): k for k in enabled_keys},
            {norm_key(k): k for k in optional_keys})


def extract_candidates(src, enabled_map, optional_map, include_single, only_re):
    """From a source string, find dictionary names that occur as standalone
    entities. Returns list of canonical dictionary keys.

    Name forms handled:
      - internal apostrophes / hyphens kept: M'aiq, J'zargo, Nix-Hound,
        Swims-In-Deep-Water
      - possessive stripped: Whiterun's / Whiterun's match Whiterun
      - plural tolerated: Argonians / Thalmors match Argonian / Thalmor
      - cultural particles allowed lowercase inside a name: gra-/gro-/jo-
        (Lash gra-Shugurz)
      - case-sensitive start: only capitalized tokens begin a match
    """
    # Source tokens: split on whitespace only; keep internal ' - ’ inside words.
    # We also keep punctuation attached so Whiterun's stays one token.
    tokens = re.findall(r"[A-Za-z][A-Za-z'’\-]*", src)

    def norm_token_seq(seq):
        # strip trailing possessive on the last token; lower all
        out = [t.lower() for t in seq]
        out[-1] = re.sub(r"(?:'s|’s)$", '', out[-1])
        return out

    def lookup(words):
        nseq = norm_token_seq(words)
        # try full phrase; then drop trailing plural s/es on last word
        cand = ' '.join(nseq)
        if cand in enabled_map:
            return enabled_map[cand]
        if cand in optional_map and (include_single or only_re):
            return optional_map[cand]
        last = nseq[-1]
        stem = None
        if last.endswith('es') and len(last) > 3:
            stem = last[:-2]
        elif last.endswith('s') and len(last) > 2:
            stem = last[:-1]
        if stem:
            cand2 = ' '.join(nseq[:-1] + [stem])
            if cand2 in enabled_map:
                return enabled_map[cand2]
            if cand2 in optional_map and (include_single or only_re):
                return optional_map[cand2]
        return None

    out = []
    i = 0
    n = len(tokens)
    while i < n:
        head = tokens[i]
        if not head[:1].isupper() or len(head) < 2:
            i += 1
            continue
        found = None
        for L in range(min(6, n - i), 0, -1):
            found = lookup(tokens[i:i + L])
            if found:
                break
        if found:
            out.append(found)
            # advance past the matched token count (canonical may differ in
            # hyphens, so advance by the matched phrase length)
            i += L
        else:
            i += 1
    return out


def audit_rows(rows, evidence, enabled_keys, optional_keys,
               only_entity=None, include_single=False,
               semantic_by_member=None):
    """rows: iterable of dicts with xml_index/edid/rec/source/dest.
    Returns (flags, checked). A flag is a CHECK candidate carrying full
    provenance so the Agent can decide without another dictionary search."""
    flags = []
    checked = 0
    only_re = re.compile(only_entity, re.I) if only_entity else None
    enabled_map, optional_map = build_lookup_maps(enabled_keys, optional_keys)
    # members that live outside the token map (multi-word with apostrophes are
    # already enabled; single-word weak-record members ride on the enabled map)
    extra = {}
    if semantic_by_member:
        for member in semantic_by_member:
            extra.setdefault(member.lower(), []).append(member)
    semantic_tokens = SEMANTIC_TOKENS

    def matches_semantic_member(src_lower):
        for tok in semantic_tokens:
            if tok in src_lower:
                return True
        for m in extra:
            if m in src_lower:
                return True
        return False

    for row in rows:
        # rows explicitly marked KEEP in result JSON are intentional; skip them
        if (row.get('status') or '') == 'KEEP':
            continue
        src = row.get('source') or ''
        dst = row.get('dest') or ''
        if not src:
            continue  # only empty Source is skipped; empty/untranslated Dest is a candidate
        if not norm(src):
            continue
        dn = norm(dst)
        vis = re.sub(r'<[^>]*>', '', dn)
        vis = re.sub(r'&lt;[^&]*&gt;', '', vis)

        candidates = extract_candidates(src, enabled_map, optional_map,
                                        include_single, only_re)
        # semantic-family membership is itself a candidate: a weekday/month
        # that was not recognized as a name still enters the audit (the reason
        # this layer exists is that the token matcher never sees GMST words)
        src_lower = src.lower()
        if matches_semantic_member(src_lower):
            for member in (semantic_by_member or {}):
                if member.lower() in src_lower:
                    if member not in candidates:
                        candidates.append(member)
                    break

        for key in candidates:
            if only_re and not only_re.search(key):
                continue
            e = evidence[key]
            # multi-word names are near-unambiguous -> HIGH. All single words stay
            # LOW: even proven names (Skyrim, Delphine) can be elided or extended
            # in prose (天际省 / 诺德人), so LOW tells the Agent to look, not that
            # the dest is wrong. --entity forces HIGH for the named term.
            # Semantic-family members (weekdays / months) are always HIGH: their
            # official form is a fixed system term, and the whole point is to
            # catch a model that did not recognize the term at all.
            if len(key.split()) >= 2:
                conf = 'HIGH'
            elif key in (semantic_by_member or {}):
                conf = 'HIGH'
            elif only_re and only_re.search(key):
                conf = 'HIGH'
            else:
                conf = 'LOW'
            zh = e['zh']
            present = any(t in vis for t in zh)
            checked += 1
            if not present:
                flags.append({
                    'xml_index': row.get('xml_index'),
                    'edid': row.get('edid') or '',
                    'rec': row.get('rec') or '',
                    'source': src,
                    'dest': dst,
                    'status': row.get('status') or '',
                    'matched_term': key,
                    'official_translation': zh,
                    'confidence': conf,
                    'semantic_family': next((fam for fam, mem in SEMANTIC_FAMILIES.items()
                                             if key in mem), None),
                    'official_evidence': e['evidence'][:4],
                })
    return flags, checked


# ---------------------------------------------------------------------------
# row loaders
# ---------------------------------------------------------------------------

def load_xml_rows(xml_path):
    rows = []
    root = ET.parse(xml_path).getroot()
    content = root.find('Content')
    if content is None:
        return rows
    for i, s in enumerate(content.findall('String')):
        src = s.findtext('Source') or ''
        dst = s.findtext('Dest') or ''
        rec = s.findtext('REC') or ''
        edid = s.findtext('EDID') or ''
        rows.append({'xml_index': i, 'edid': edid, 'rec': rec, 'source': src, 'dest': dst, 'status': ''})
    return rows


def load_json_rows(json_path):
    d = json.load(open(json_path, encoding='utf-8-sig'))
    rows = []
    items = d.get('translations') or d.get('results') or []
    for t in items:
        # distinguish "translation absent" (fall back to original_dest) from
        # "translation present but empty" (an empty translation is a real
        # candidate — do NOT fall back)
        if 'translation' in t and t['translation'] is not None:
            dest = t['translation']
        elif 'original_dest' in t:
            dest = t.get('original_dest') or ''
        else:
            dest = t.get('dest') or ''
        rows.append({
            'xml_index': t.get('translation_unit_id') or t.get('xml_index', ''),
            'edid': t.get('edid') or '',
            'rec': t.get('rec') or '',
            'source': t.get('source') or '',
            'dest': dest,
            'status': (t.get('status') or '').upper(),
        })
    return rows


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='translated XML or translation-result JSON')
    ap.add_argument('--dict', default=None, help='dictionary dir (default: <project>/dictionary)')
    ap.add_argument('--entity', default=None,
                    help='also audit matching official names even if single-word/weak-record (regex)')
    ap.add_argument('--include-single', action='store_true',
                    help='also audit single-word dictionary nouns from weak records (noisier)')
    ap.add_argument('--semantic-family', dest='semantic', default=None,
                    help='audit only one semantic family (weekday|month|all); '
                         'default: all families, always on')
    ap.add_argument('--no-semantic-families', action='store_true',
                    help='disable the semantic-family layer (weekdays/months); '
                         'diagnostic only, never in normal gate runs')
    ap.add_argument('--json', dest='outjson', default=None, help='write flags to JSON')
    ap.add_argument('--limit', type=int, default=0, help='cap printed rows (0 = all)')
    ap.add_argument('--fail-on-flags', action='store_true',
                    help='exit 1 when flags exist (CI gating); default exits 0 on flags')
    args = ap.parse_args()

    # validate --entity regex early
    only_re = None
    if args.entity:
        try:
            only_re = re.compile(args.entity, re.I)
        except re.error as exc:
            print(f'error: invalid --entity regex {args.entity!r}: {exc}', file=sys.stderr)
            return 2
    if args.limit is not None and args.limit < 0:
        print('error: --limit must be >= 0', file=sys.stderr)
        return 2

    # resolve the semantic-family layer (always on unless explicitly disabled)
    family_scope = 'all'
    if args.semantic:
        family_scope = args.semantic.lower()
    if family_scope not in ('all', 'weekday', 'month'):
        print(f"error: --semantic-family must be one of weekday|month|all, got '{args.semantic}'",
              file=sys.stderr)
        return 2
    root = PROJECT_ROOT
    dict_dir = Path(args.dict) if args.dict else root / 'dictionary'
    if not dict_dir.is_absolute():
        dict_dir = root / dict_dir
    if not dict_dir.is_dir():
        print(f'error: dictionary dir not found: {dict_dir}', file=sys.stderr)
        return 2
    target = Path(args.target)
    if not target.is_absolute():
        target = root / target
    if not target.exists():
        print(f'error: target not found: {target}', file=sys.stderr)
        return 2

    evidence = load_dictionary_evidence(dict_dir)
    evidence = merge_trailing_punct_keys(evidence)
    if not evidence:
        print(f'error: no dictionary evidence under {dict_dir}', file=sys.stderr)
        return 2
    enabled_keys, optional_keys = classify_keys(evidence, include_single=args.include_single)
    # semantic families (weekdays/months) are audited regardless of their weak
    # GMST record structure; a missing dictionary dest is a hard failure because
    # a silently-skipped family member is exactly the bug this layer exists to
    # prevent
    missing_terms, fam_by_member = find_dictionary_family_terms(evidence)
    if missing_terms:
        print('error: semantic-family member missing official dictionary evidence: '
              + ', '.join(sorted(missing_terms)), file=sys.stderr)
        return 2
    if not args.no_semantic_families:
        for key in fam_by_member:
            enabled_keys.setdefault(key, evidence[key])
            # semantic terms are forced HIGH; they must not stay in the
            # optional pool where weak-record single words get demoted/filtered
            optional_keys.pop(key, None)
        if family_scope != 'all':
            fam_by_member = {k: v for k, v in fam_by_member.items()
                             if k in SEMANTIC_FAMILIES.get(family_scope, {})}
    else:
        fam_by_member = {}
    SEMANTIC_TOKENS.clear()
    SEMANTIC_TOKENS.update(k.lower() for k in fam_by_member)
    print(f'dictionary evidence sources: {len(evidence)}; '
          f'default-enabled keys: {len(enabled_keys)}; optional keys: {len(optional_keys)}',
          flush=True)
    if fam_by_member:
        print(f'semantic families: {family_scope} ({len(fam_by_member)} terms, '
              f'always HIGH)', flush=True)

    try:
        if target.suffix.lower() == '.xml':
            rows = load_xml_rows(target)
            label = 'xml'
        else:
            rows = load_json_rows(target)
            label = 'json'
    except (ET.ParseError, FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f'error: cannot read {target}: {exc}', file=sys.stderr)
        return 2
    print(f'{label} rows: {len(rows)}', flush=True)

    flags, checked = audit_rows(rows, evidence, enabled_keys, optional_keys,
                                only_entity=args.entity, include_single=args.include_single,
                                semantic_by_member=fam_by_member)
    print(f'entity checks: {checked}; CHECK candidates: {len(flags)}', flush=True)
    shown = flags if not args.limit else flags[:args.limit]
    for f in shown:
        print('=' * 70)
        print(f"[{f['xml_index']}][{f.get('rec') or ''}][{f.get('edid') or ''}][{f.get('confidence')}] "
              f"{f['matched_term']} -> 官方: {'/'.join(f['official_translation'])}")
        print(f"  SRC: {f['source'][:160]!r}")
        print(f"  DST: {f['dest'][:160]!r}")
        if f.get('official_evidence'):
            ev = f['official_evidence'][0]
            print(f"  ev: {ev.get('file')} [{ev.get('rec')} {ev.get('edid')}] -> {ev.get('dest')}")
    if args.outjson:
        out = Path(args.outjson)
        if not out.is_absolute():
            out = root / out
        canonical_re = re.compile(r"^[A-Za-z0-9_.\-]+-noun-audit\.json$")
        if not canonical_re.fullmatch(out.name):
            print(
                f"error: output filename must be <plugin>-noun-audit.json "
                f"(deterministic output contract, no version/label suffixes): {out.name}",
                file=sys.stderr,
            )
            return 2
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, 'w', encoding='utf-8') as fh:
                json.dump(flags, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            print(f'error: cannot write {out}: {exc}', file=sys.stderr)
            return 2
        print(f'flags written: {out}')
    if args.fail_on_flags and flags:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
