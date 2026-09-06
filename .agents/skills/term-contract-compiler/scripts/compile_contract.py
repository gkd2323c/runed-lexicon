# -*- coding: utf-8 -*-
"""compile_contract.py — Term-Contract Compiler (deterministic).

Compiles DICTIONARY.md (and optionally GLOSSARY.md) Markdown tables into the
`terms` section of a Translation Contract (schema v1). Deterministic only:
it faithfully converts Markdown term rows into Term Definitions and records
provenance. It never infers `enforcement`, `match`, or `risk_flags`; those come
from a per-MOD confirmation file, or from row annotations, or default
conservatively (see below).

Enforcement resolution (never guessed):
  1. explicit `decision` in the confirmation JSON (term_id -> overrides)
  2. Markdown 状态/备注 annotations: `[enforcement=...]`, `[mode=...]`, `[risk=...]`
  3. conservative defaults:
       CONFIRMED  -> enforcement REQUIRED, match contains_phrase (unless risk)  [*]
       PROVISIONAL-> enforcement REQUIRED, match contains_phrase
       REVIEW     -> enforcement FORBIDDEN_ONLY (no automatic required)
       KEEP       -> enforcement KEEP (not emitted as a term; handled by keep list)
     [*] A note: this is a *candidate* default. Compiling a term to REQUIRED +
         contains_phrase is only safe when the term has no polysemy/knowledge-
         boundary risk. Terms whose English form is ambiguous (Companions /
         Guard / Jarl / Blades / Black Book / Dragonborn / person names that are
         ordinary words) MUST be flagged risk_flags=alias / knowledge_boundary by
         the confirmation file, which forces FORBIDDEN_ONLY and forbids auto bindings.

Term rows are parsed from Markdown tables with columns containing English and 中文.

Output:
  - term index JSON (terms section)
  - compile report (stdout): counts, skipped rows, warnings
  - optional keep-list JSON of KEEP rows

This compiler does NOT emit unit_bindings (those are semantic, produced by the
analysis Agent). Hand-written bindings remain the source of truth for unit level.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys


# ---------------------------------------------------------------- md table parsing

TABLE_ROW_RE = re.compile(r'^\s*\|(.+)\|\s*$')
CELL_RE = re.compile(r'`([^`]*)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\([^)]*\)|([^|]+)')
ANNOT_RE = re.compile(r'\[(enforcement|mode|risk|decision_status)\s*=\s*([A-Za-z_]+)\]')


def split_row(line: str):
    inner = line.strip()
    if inner.startswith('|'):
        inner = inner[1:]
    if inner.endswith('|'):
        inner = inner[:-1]
    # don't split on escaped pipes inside code spans (rare); simple split ok for our tables
    cells = [c.strip() for c in inner.split('|')]
    return cells


def strip_md(s: str) -> str:
    s = re.sub(r'<br\s*/?>', ' ', s)
    s = re.sub(r'`([^`]*)`', r'\1', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
    s = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', s)
    s = s.strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def is_separator_row(s):
    stripped = s.replace('|', '').replace(' ', '').replace(':', '').replace('-', '')
    return stripped == ''


def find_table_blocks(lines):
    """Yield list of cell-lists for each markdown table (header + rows).
    Separator rows (| :--- |) are skipped but do NOT break the block, so the
    header row stays attached to its data rows.
    """
    tables = []
    cur = []
    for ln in lines:
        s = ln.strip()
        if is_separator_row(s):
            continue  # keep block open across the markdown separator
        if s.startswith('|') and s.endswith('|'):
            cur.append(s)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def parse_dictionary_table(rows):
    """Map English -> {zh, status, source, note} from a dictionary table.
    Skips the markdown separator row; identifies header by English/Chinese column
    labels; extracts first English + first Chinese cells per data row."""
    entries = []
    header = None
    for r in rows:
        cells = split_row(r)
        if not cells:
            continue
        joined = ' '.join(cells).lower()
        # separator row (colons / dashes only)
        stripped = joined.replace(':', '').replace('-', '').replace(' ', '').replace('|', '')
        if stripped == '':
            continue
        if header is None:
            if ('english' in joined or '原文' in joined
                    or any('english' in c.lower() or '原文' in c for c in cells)):
                header = cells
                continue
            else:
                # table without recognizable header: treat first row as header
                header = cells
                continue
        vals = [strip_md(c) for c in cells]
        eng = ''
        zh = ''
        # prefer header-aligned columns
        for i, v in enumerate(vals):
            if not v:
                continue
            if re.search(r'[\u4e00-\u9fff]', v):
                if not zh:
                    zh = v
            elif re.match(r'^[A-Za-z][A-Za-z0-9 \'\-\.\(\)/]*$', v):
                if not eng:
                    eng = v
        if eng and zh:
            entries.append({'english': eng, 'zh': zh,
                            'status': _status_from(vals),
                            'note': ' | '.join(vals[2:])})
    return entries


def extract_additional_accepted(note):
    """Parse additional_accepted variants from a dictionary note.

    Syntax in note: additional_accepted: X, Y, Z
    (must appear after a 严禁-family keyword or at start of a segment to avoid
    picking up descriptive text)

    These are alternative valid translations that should be accepted by the gate
    in addition to the primary target (e.g., "白望塔" for "Whitewatch Tower"
    when the target is "白望塔楼").
    """
    out = []
    if not note:
        return out
    # Only look for the explicit "additional_accepted:" annotation
    m = re.search(r'additional_accepted\s*:\s*([^；;。]+)', note)
    if not m:
        return out
    raw = m.group(1).strip()
    for part in re.split(r'[,，、/／]', raw):
        part = part.strip().strip('""\'')
        part = re.sub(r'[（(][^）)]*[）)]$', '', part)
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def extract_forbidden(note, target):
    """Parse forbidden variants from a dictionary note.
    Handles patterns like 严禁使用“X / Y / Z”、严禁“A / B”、不得保留英文 X。
    Returns a de-duplicated list; the target itself is excluded.

    Guards against false positives:
    - quoted spans are only taken AFTER the first 严禁-family keyword in the
      segment (descriptive quotes before the keyword are usage notes, not bans);
    - unquoted free text is truncated at the first comma (commas usually start
      a new descriptive clause, variant lists use / 、 inside quotes);
    - any part that overlaps the target string (in either direction) is skipped,
      because a forbidden substring of the target would make the gate's TERM002
      check fire on every correct translation.
    """
    out = []
    if not note:
        return out

    def _overlap_target(part):
        return part in target or target in part

    segs = re.split(r'[；;。]', note)
    for seg in segs:
        kw = re.search(r'(严禁|不得|禁止|不要|不许|不能|请勿)', seg)
        if not kw:
            continue
        # '错认成' introduces an entity to avoid confusing WITH, not a wrong translation
        if re.search(r'错认成|误认成|混淆', seg):
            continue
        # only text from the first ban keyword onward counts as a ban
        ban_text = seg[kw.end():]
        quoted = re.findall(r'[“"「『]([^”"」』]+)[”"」』]', ban_text)
        for q in quoted:
            for part in re.split(r'[/／、,，]', q):
                part = part.strip()
                part = re.sub(r'[（(][^）)]*[）)]$', '', part)
                part = part.strip()
                if part and part != target and not _overlap_target(part) and part not in out:
                    out.append(part)
        m = re.search(r'(?:使用|翻译为|翻为|保留|泛化为|提前翻成|写成|译成|翻译成)?\s*[:：]?\s*([^。；]+)', ban_text)
        if m:
            # unquoted free text: stop at the first comma — a comma here starts a
            # new descriptive clause, not another variant
            free = re.split(r'[,，]', m.group(1).strip())[0]
            for part in re.split(r'[/／、或]', free):
                part = part.strip().strip('“”"\'')
                part = re.sub(r'[（(][^）)]*[）)]$', '', part)
                part = re.sub(r'["“”」』]+$', '', part)
                part = re.sub(r'(替代组织名|来替代|之类的|等词|等)$', '', part)
                part = re.sub(r'[”"「『].*$', '', part)  # cut trailing quoted leftovers
                part = part.strip()
                if part and part != target and not _overlap_target(part) and not re.search(r'[A-Za-z]{6,}', part) and part not in out:
                    out.append(part)
    return out


def _status_from(vals):
    for v in vals:
        u = v.upper()
        for s in ('CONFIRMED', 'PROVISIONAL', 'REVIEW', 'KEEP', 'OVERRIDE'):
            if s in u:
                return s
    return ''


# ---------------------------------------------------------------- structured JSON source

# A term whose English cell parsed to a status word (or that still looks like a
# column header) is a parse error, not a term. The Markdown path used to emit
# these silently as garbage terms (e.g. source="PROVISIONAL"); the JSON path
# rejects them loudly.
_STATUS_WORDS = {'CONFIRMED', 'PROVISIONAL', 'REVIEW', 'KEEP', 'OVERRIDE'}


def _looks_like_english_word(s):
    # conservative sanity check used only to detect gross parse misalignment on
    # the JSON path: a real source should not *be* a bare status word.
    return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9 \'\-\.\(\)/&!]*', s))


def parse_terms_json(path):
    """Load a structured MOD terms JSON (canonical machine source).

    Expected shape:
        {
          "id_prefix": "sb1.",            // optional; CLI --id-prefix wins
          "terms": [
            {
              "english": "Neethmarin",      // required, non-empty
              "zh": "尼思马林",              // required, non-empty
              "status": "PROVISIONAL",      // optional, default PROVISIONAL
              "note": "...",                // optional, provenance / usage note
              "forbidden": [],               // optional explicit forbidden variants
              "enforcement": "REQUIRED",    // optional; REQUIRED | FORBIDDEN_ONLY | KEEP
              "risk_flags": ["alias"]       // optional explicit risk flags
            }
          ]
        }

    The JSON path is the canonical structured form: every field is explicit, so
    the compiler performs no column guessing and no implicit status scanning.
    Terms with enforcement KEEP are moved to the keep list (not emitted as terms).
    """
    try:
        data = json.load(open(path, encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'error: {path}: invalid JSON: {exc}') from exc
    if not isinstance(data, dict) or 'terms' not in data:
        raise SystemExit(f'error: {path}: expected object with a "terms" array')
    raw_terms = data['terms']
    if not isinstance(raw_terms, list):
        raise SystemExit(f'error: {path}: "terms" must be an array')

    entries = []
    problems = []
    for i, item in enumerate(raw_terms):
        if not isinstance(item, dict):
            problems.append(f'  [{i}] term entry is not an object')
            continue
        eng = str(item.get('english') or '').strip()
        zh = str(item.get('zh') or '').strip()
        if not eng:
            problems.append(f'  [{i}] missing "english"')
        if not zh:
            problems.append(f'  [{i}] ({eng or "?"}) missing "zh"')
        if eng.upper() in _STATUS_WORDS:
            problems.append(f'  [{i}] "english" is a status word ({eng}); parse misalignment?')
        status = str(item.get('status') or 'PROVISIONAL').upper()
        if status not in _STATUS_WORDS:
            problems.append(f'  [{i}] ({eng}) unknown status: {item.get("status")!r}')
        enf = (item.get('enforcement') or '').upper()
        if enf and enf not in ('REQUIRED', 'FORBIDDEN_ONLY', 'KEEP'):
            problems.append(f'  [{i}] ({eng}) unknown enforcement: {item.get("enforcement")!r}')
    if problems:
        raise SystemExit('error: {path}: invalid term entries:\n' + '\n'.join(problems))

    return raw_terms


def build_terms_json(raw_terms, conf=None, id_prefix='', source_tag='', source_hash=''):
    """Deterministically turn validated JSON term entries into final term
    definitions. Unlike the Markdown path, nothing is guessed:
      - status / enforcement / risk_flags / forbidden come straight from JSON;
      - missing optional fields fall back to documented defaults, never to
        heuristic column scanning;
      - enforcement KEEP (or status KEEP) routes to the keep list;
      - explicit enforcement REQUIRED + risk_flags is honored verbatim (no
        ambiguous-word auto-downgrade) because the author declared it on purpose.
    """
    conf = conf or {}
    if id_prefix:
        conf['id_prefix'] = id_prefix
    elif not conf.get('id_prefix'):
        conf['id_prefix'] = ''
    prefix = conf.get('id_prefix', '')
    ovs = conf.get('overrides', {})

    terms = {}
    keep = []
    for i, item in enumerate(raw_terms):
        eng = str(item.get('english') or '').strip()
        zh = str(item.get('zh') or '').strip()
        if not eng or not zh:
            continue
        status = (str(item.get('status') or 'PROVISIONAL').upper())
        enf_in = (str(item.get('enforcement') or '').upper())
        note = str(item.get('note') or '')
        if status == 'KEEP' or enf_in == 'KEEP':
            keep.append(eng)
            continue
        ov = ovs.get(eng, {})
        # explicit JSON decisions win; conf overrides (rare, explicit) may adjust
        enforcement = enf_in or ov.get('enforcement') or (
            'FORBIDDEN_ONLY' if status == 'REVIEW' else 'REQUIRED')
        risk = item.get('risk_flags')
        if risk is None:
            risk = ov.get('risk_flags')
        if isinstance(risk, str):
            risk = [x.strip() for x in risk.split(',') if x.strip()]
        forbidden = ov.get('forbidden', item.get('forbidden') or [])
        target = ov.get('target', zh)
        # additional_accepted: explicit list of alternative valid translations
        additional = item.get('additional_accepted') or []
        if isinstance(additional, str):
            additional = [x.strip() for x in additional.split(',') if x.strip()]
        accepted = [target] + list(additional)
        definition = {
            'term_id': prefix + _slug(eng),
            'source': eng,
            'decision_status': 'CONFIRMED' if status == 'OVERRIDE' else status,
            'enforcement': enforcement,
            'target': target,
            'forbidden': list(forbidden),
            'match': {'kind': ov.get('match_kind', 'contains_phrase'),
                      'accepted': accepted},
            'origin': 'MOD_DICTIONARY',
            'evidence': source_tag + (' (hash ' + source_hash[:12] + ')' if source_hash else ''),
            'note': note,
        }
        if risk:
            definition['risk_flags'] = risk
        terms[definition['term_id']] = definition
    return terms, keep


# ---------------------------------------------------------------- term construction

# Terms whose English is ambiguous or knowledge-boundary sensitive should NEVER
# default to REQUIRED. The confirmation file must explicitly say REQUIRED for them.
# Compiler does not guess polysemy; but it refuses an unsafe default.
# A small denylist of *patterns* (English forms) that force FORBIDDEN_ONLY unless
# explicitly overridden by conf with force_required=True.
AMBIGUOUS_PATTERNS = [
    # ordinary words that can be common nouns (match anywhere in the source cell)
    r'(?i)blades?', r'(?i)companions?', r'(?i)\bguard\b', r'(?i)\bjarl\b',
    r'(?i)\bthane\b', r'(?i)\bhist\b', r'(?i)\bjel\b', r'(?i)dragonborn',
    r'(?i)black book', r'(?i)dwarven', r'(?i)dwemer',
    r'(?i)\bthe\s+blades\b',
]
# knowledge-boundary / spoiler terms: Falmer / Snow Elf etc.
SPOILER_PATTERNS = [r'(?i)falmer', r'(?i)snow elf', r'(?i)maormer', r'(?i)sea elf']


def _is_ambiguous(eng):
    for p in AMBIGUOUS_PATTERNS:
        if re.search(p, eng.strip()):
            return True
    for p in SPOILER_PATTERNS:
        if re.search(p, eng):
            return True
    return False

def build_terms(entries, conf_overrides=None, source_tag='', source_hash=''):
    """entries: list of {english, zh, status, note} -> {term_id: definition}"""
    conf = conf_overrides or {}
    terms = {}
    keep_sources = []
    for e in entries:
        eng = e['english'].strip()
        if not eng:
            continue
        status = (e.get('status') or '').upper() or 'PROVISIONAL'
        if status == 'KEEP':
            keep_sources.append(eng)
            continue  # KEEP handled by keep-list, not a term
        # id: slug from english + optional row label
        tid = conf.get('id_prefix', '') + _slug(eng)
        # gather annotations from note & status
        ann = dict(ANNOT_RE.findall(e['note']))
        # overrides from confirmation file
        ov = conf.get('overrides', {}).get(eng, {})
        enforcement = ov.get('enforcement') or ann.get('enforcement') or ann.get('mode')
        if not enforcement:
            # conservative defaults by status
            if status == 'REVIEW':
                enforcement = 'FORBIDDEN_ONLY'
            elif status in ('CONFIRMED', 'PROVISIONAL') and not _is_ambiguous(eng):
                enforcement = 'REQUIRED'
            else:
                enforcement = 'FORBIDDEN_ONLY'
        risk = ov.get('risk_flags') or ann.get('risk')
        if risk and isinstance(risk, str):
            risk = [x.strip() for x in risk.split(',') if x.strip()]
        elif _is_ambiguous(eng):
            risk = ['alias']
        # safety: ambiguous ordinary-word terms must not default to REQUIRED+contains
        # unless the confirmation file explicitly says REQUIRED
        match_kind = ov.get('match_kind') or ann.get('match') or 'contains_phrase'
        if risk and enforcement == 'REQUIRED':
            # force downgrade unless explicitly allowed
            if not ov.get('force_required'):
                enforcement = 'FORBIDDEN_ONLY'
        # Parse additional_accepted variants from note (e.g., natural shorthand)
        additional = extract_additional_accepted(e['note'])
        accepted = [e['zh']] + additional
        definition = {
            'term_id': tid,
            'source': eng,
            'decision_status': status if status != 'OVERRIDE' else 'CONFIRMED',
            'enforcement': enforcement,
            'target': e['zh'],
            'forbidden': extract_forbidden(e['note'], e['zh']),
            'match': {'kind': match_kind, 'accepted': accepted},
            'origin': 'OFFICIAL' if 'dictionary' in source_tag else ('MOD_DICTIONARY' if 'DICTIONARY' in source_tag else 'GLOSSARY'),
            'evidence': source_tag + (' (hash ' + source_hash[:12] + ')' if source_hash else ''),
            'note': e['note'],
        }
        if risk:
            definition['risk_flags'] = risk
        terms[tid] = definition
    return terms, keep_sources


def _slug(eng):
    s = re.sub(r'[^A-Za-z0-9]+', '-', eng).strip('-').lower()
    return s[:48]


# ---------------------------------------------------------------- global ban list (project-wide)

# 项目级全局禁用词库 JSON 的顶层字段：
#   bans: [ { english, forbidden[], target, reason } ... ]
#   keep: [ "English KEEP value", ... ]   （可选，项目级全局 KEEP）
# 词库本身是 canonical 源；本函数只做校验与规范化，不猜语义。

def load_global_bans(path):
    """Load & validate the project-wide global-forbidden-words JSON.

    Returns (bans, keep):
      bans — list of {english, forbidden:[...], target, reason}
      keep — list of global KEEP english values
    Raises SystemExit(1) on structural errors (never silently emit garbage).
    """
    try:
        data = json.load(open(path, encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'error: {path}: invalid JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise SystemExit(f'error: {path}: expected an object with "bans"')
    raw_bans = data.get('bans')
    if not isinstance(raw_bans, list):
        raise SystemExit(f'error: {path}: "bans" must be an array')
    problems = []
    bans = []
    for i, b in enumerate(raw_bans):
        if not isinstance(b, dict):
            problems.append(f'  [{i}] ban entry is not an object')
            continue
        eng = str(b.get('english') or '').strip()
        if not eng:
            problems.append(f'  [{i}] missing "english"')
        fb = b.get('forbidden')
        if isinstance(fb, str):
            fb = [x.strip() for x in fb.split(',') if x.strip()]
        fb = [x for x in (fb or []) if isinstance(x, str) and x.strip()]
        if not fb:
            problems.append(f'  [{i}] ({eng or "?"}) missing/empty "forbidden"')
        tgt = str(b.get('target') or '').strip()
        reason = str(b.get('reason') or '').strip()
        if not reason:
            problems.append(f'  [{i}] ({eng or "?"}) missing "reason"')
        bans.append({'english': eng, 'forbidden': fb, 'target': tgt, 'reason': reason})
    keep = data.get('keep')
    if keep is None:
        keep = []
    if isinstance(keep, str):
        keep = [x.strip() for x in keep.split(',') if x.strip()]
    if not isinstance(keep, list) or not all(isinstance(x, str) and x.strip() for x in keep):
        problems.append('  "keep" must be an array of non-empty strings')
    keep = [x.strip() for x in keep if isinstance(x, str) and x.strip()]
    if problems:
        raise SystemExit('error: {path}: invalid global ban file:\n' + '\n'.join(problems))
    return bans, keep


# ---------------------------------------------------------------- main

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Term-Contract Compiler (deterministic)')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--dictionary', default=None, help='DICTIONARY.md path (Markdown source)')
    src.add_argument('--terms', default=None, help='MOD terms JSON path (structured canonical source)')
    ap.add_argument('--glossary', default=None, help='optional GLOSSARY.md path')
    ap.add_argument('--output', required=True, help='output term-index JSON')
    ap.add_argument('--keep-output', default=None, help='optional keep-list JSON output')
    ap.add_argument('--conf', default=None, help='optional confirmation JSON (overrides)')
    ap.add_argument('--id-prefix', default='', help='term_id prefix, e.g. mvf1.')
    ap.add_argument('--global-bans', default=None,
                    help='project-wide global-forbidden-words JSON; its bans/keep are embedded\n'
                         'into the compiled contract under global_bans / global_keep for the gate')
    args = ap.parse_args()

    conf = json.load(open(args.conf, encoding='utf-8-sig')) if args.conf else {}
    if args.id_prefix:
        conf['id_prefix'] = args.id_prefix
    elif not conf.get('id_prefix'):
        conf['id_prefix'] = ''

    if args.terms:
        # Structured JSON canonical source: no column guessing, explicit fields.
        raw_terms = parse_terms_json(args.terms)
        src_hash = hashlib.sha256(open(args.terms, 'rb').read()).hexdigest()
        terms, keep = build_terms_json(raw_terms, conf, id_prefix=args.id_prefix,
                                       source_tag=args.terms, source_hash=src_hash)
        table_count = 0
        entry_count = len(raw_terms)
    else:
        lines = open(args.dictionary, encoding='utf-8-sig').read().splitlines()
        tables = find_table_blocks(lines)
        entries = []
        for tb in tables:
            entries += parse_dictionary_table(tb)
        terms, keep = build_terms(entries, conf, source_tag=args.dictionary,
                                  source_hash=hashlib.sha256(open(args.dictionary, 'rb').read()).hexdigest())
        table_count = len(tables)
        entry_count = len(entries)

    # apply explicit overrides for forbidden lists from conf (not auto-derived);
    # JSON path already applied overrides inside build_terms_json, so this only
    # affects the Markdown path.
    if not args.terms:
        for eng, ov in conf.get('overrides', {}).items():
            for t in terms.values():
                if t['source'].lower() == eng.lower():
                    if 'forbidden' in ov:
                        t['forbidden'] = ov['forbidden']
                    if 'target' in ov:
                        t['target'] = ov['target']
                        t['match']['accepted'] = [ov['target']]

    json.dump({'schema_version': '1.0', 'terms': terms,
               'compiled_from': args.terms or args.dictionary},
              open(args.output, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    if args.keep_output:
        json.dump(keep, open(args.keep_output, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    # project-wide global ban list (TERM004) + global KEEP list (KEEP002):
    # embedded into the compiled contract so the gate can enforce them on every
    # translated line independently of per-unit bindings.
    if args.global_bans:
        gbans, gkeep = load_global_bans(args.global_bans)
        # open the output freshly for the second write (avoid double-encoding drift)
        compiled = json.load(open(args.output, encoding='utf-8'))
        compiled['global_bans'] = gbans
        if gkeep:
            compiled['global_keep'] = gkeep
        json.dump(compiled, open(args.output, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'global bans: {len(gbans)} entries, global keep: {len(gkeep)} -> embedded in contract')

    print(f'tables: {table_count}  rows: {entry_count}  terms: {len(terms)}  keep: {len(keep)}')
    if args.keep_output:
        print('keep list ->', args.keep_output)
    # warnings: REVIEW / risk / empty forbidden for REQUIRED terms
    for t in terms.values():
        if t['enforcement'] == 'REQUIRED' and not t['forbidden']:
            print(f'  NOTE: REQUIRED term without forbidden list: {t["source"]} ({t["term_id"]})')
        if t.get('risk_flags'):
            print(f'  RISK: {t["source"]} flags={t["risk_flags"]} enforcement={t["enforcement"]}')


if __name__ == '__main__':
    main()
