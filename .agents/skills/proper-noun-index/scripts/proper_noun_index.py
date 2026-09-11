# -*- coding: utf-8 -*-
"""proper_noun_index.py — derived proper-noun index over the official dictionary.

Two deterministic, read-only commands:

  build  Scan the official dictionary tree once and emit a stable index JSON:
         for every English source with Chinese official evidence, record the
         official Chinese candidates with cross-file support (which files use
         that dest), the record families
         that prove the name, and word count. This makes the "people and place
         names have a stable organization" fact an explicit, machine-readable
         contract instead of a per-run re-parse.

  scan   Given a MOD xTranslator XML or translation-result JSON, list the
         official proper nouns (people / places / races / factions first, then
         multi-word record names) that actually appear in the MOD, with
         occurrence counts and official evidence. Output is a deduped inventory
         for pre-translation adjudication, NOT a per-row dest check (that is
         dictionary-noun-audit's job on translated output).

Reuse contract: extraction tokens, stop/verb heads, strong-record families and
normalization are imported from dictionary-noun-audit so this tool can never
drift from the audit's matching behavior. Ambiguity hints reuse the
term-contract-compiler denylist. No translation semantics are decided here:
every entry still needs Agent adjudication into the MOD's DICTIONARY.md /
terms.json before the compiler/gate run.

Exit codes: 0 success; 2 real failure (missing inputs, unreadable target).
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SKILLS_DIR = PROJECT_ROOT / '.agents' / 'skills'

# ---------------------------------------------------------------------------
# reuse: dictionary-noun-audit (matching behavior must never drift)
# ---------------------------------------------------------------------------

def _load_sibling(skill_name: str, script_name: str):
    path = SKILLS_DIR / skill_name / 'scripts' / script_name
    spec = importlib.util.spec_from_file_location(skill_name.replace('-', '_'), path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'error: cannot import sibling skill module: {path}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_AUDIT = None
_COMPILER = None


def _audit():
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = _load_sibling('dictionary-noun-audit', 'dictionary_noun_audit.py')
    return _AUDIT


def _compiler():
    global _COMPILER
    if _COMPILER is None:
        _COMPILER = _load_sibling('term-contract-compiler', 'compile_contract.py')
    return _COMPILER


def norm(s):
    return _audit().norm(s)


STRONG_RECS = None
STOP_HEADS = None
VERB_HEADS = None
ITEM_RECS = None


def _init_rule_sets():
    global STRONG_RECS, STOP_HEADS, VERB_HEADS, ITEM_RECS
    a = _audit()
    STRONG_RECS = a.STRONG_RECS
    STOP_HEADS = a.STOP_HEADS
    VERB_HEADS = a.VERB_HEADS
    ITEM_RECS = a.ITEM_RECS


CJK_RE = re.compile(r'[\u4e00-\u9fff]')
CONTRACTION_HEADS = {"let's", "i'm", "you're", "you've", "you'll", "don't",
                     "can't", "won't", "isn't", "aren't", "wasn't", "weren't",
                     "that's", "it's", "what's", "there's", "he's", "she's",
                     "we're", "we've", "they're", "didn't", "doesn't", "couldn't",
                     "wouldn't", "shouldn't", "haven't", "hasn't", "i'll", "i've",
                     "i'd", "let us", "gotta", "wanna", "kinda"}


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build_index(dict_dir: Path) -> dict:
    files = sorted(dict_dir.rglob('*.xml'))
    if not files:
        raise SystemExit(f'error: no dictionary XML under {dict_dir}')

    # English source -> {zh: {files: set}, recs: set, words: int}.  Every file
    # under the dictionary tree is official evidence with equal standing: no
    # filename is special-cased, so adding a dictionary XML later requires no
    # code change (same rule as skyrim-xml-tools).
    raw = {}
    failed = []
    for fp in files:
        try:
            root = ET.fromstring(fp.read_text(encoding='utf-8-sig'))
        except (ET.ParseError, UnicodeDecodeError, OSError) as exc:
            failed.append((str(fp), str(exc)))
            continue
        content = root.find('Content')
        if content is None:
            continue
        for s in content.findall('String'):
            rec_el = s.find('REC')
            src_el = s.find('Source')
            dst_el = s.find('Dest')
            if rec_el is None or src_el is None or dst_el is None:
                continue
            rec = (rec_el.text or '').strip()
            src = norm(src_el.text)
            dst = norm(dst_el.text)
            if not src or not dst:
                continue
            if not CJK_RE.search(dst):
                continue  # English-only dest is not translation evidence
            if len(src) > 120 or len(dst) > 120:
                continue
            e = raw.setdefault(src, {})
            e.setdefault('zh', {})
            z = e['zh'].setdefault(dst, {'files': set()})
            z['files'].add(fp.name)
            e.setdefault('recs', set()).add(rec.split(':')[0] if ':' in rec else rec)
            e['words'] = len(src.split())

    # merge trailing-punctuation keys into their bare counterpart (audit parity)
    merged = {}
    for key, e in raw.items():
        bare = re.sub(r'[.,:;!?，。！？、…]+$', '', key)
        if bare != key and bare in raw:
            base = merged.setdefault(bare, {'zh': {}, 'recs': set(), 'words': len(bare.split())})
            for zh, z in e['zh'].items():
                bz = base['zh'].setdefault(zh, {'files': set()})
                bz['files'] |= z['files']
            base['recs'] |= e['recs']
        else:
            merged.setdefault(key, e)

    # article case is not a semantic distinction: "the Rift" and "The Rift"
    # are the same hold/place.  Canonicalize a leading lowercase "the" to
    # "The" and fold dests/records together so each place has one index entry.
    case_folded = {}
    for key, e in merged.items():
        canon = key
        if key[:4].lower() == 'the ' and key[0].islower():
            canon = 'The ' + key[4:]
        tgt = case_folded.setdefault(canon, {'zh': {}, 'recs': set(), 'words': len(canon.split())})
        for zh, z in e['zh'].items():
            bz = tgt['zh'].setdefault(zh, {'files': set()})
            bz['files'] |= z['files']
        tgt['recs'] |= e['recs']

    names = {}
    for key, e in case_folded.items():
        zh_out = []
        for zh in sorted(e['zh']):
            z = e['zh'][zh]
            zh_out.append({'zh': zh, 'files': sorted(z['files'])})
        # stable dest ordering: number of supporting files desc, zh asc
        zh_out.sort(key=lambda x: (-len(x['files']), x['zh']))
        names[key] = {
            'zh': zh_out,
            'records': sorted(e['recs']),
            'words': e['words'],
        }

    # classify each key exactly like dictionary-noun-audit.classify_keys does.
    # Proper-noun index must not inherit INFO/GMST prose sentences as names:
    # the whole dictionary tree contains dialogue lines (INFO:NAM1 ~34k) and
    # settings text; only keys classified as names belong in the entity pool.
    audit_view = {}
    for key, e in names.items():
        audit_view[key] = {
            'zh': [z['zh'] for z in e['zh']],
            'recs': {r: [] for r in e['records']},
            'evidence': [],
        }
    enabled_keys, optional_keys = _audit().classify_keys(audit_view)

    # semantic families (weekdays/months) are system terms whose dests must be
    # kept official even though their rows are weak GMST records.  Fold them
    # into the enabled pool exactly like dictionary-noun-audit does, so the
    # index and the audit can never drift apart.
    try:
        missing, fam_by_member = _audit().find_dictionary_family_terms(audit_view)
    except AttributeError:
        missing, fam_by_member = set(), {}
    if missing:
        raise SystemExit('proper-noun-index: semantic-family member missing dictionary evidence: '
                         + ', '.join(sorted(missing)))
    for member in fam_by_member:
        enabled_keys.setdefault(member, audit_view[member])
        # semantic terms are forced HIGH in the audit; they must not stay in
        # the optional pool where weak-record single words get demoted
        optional_keys.pop(member, None)


    # also annotate keys whose *dest* is a bare sentence / ends with sentence
    # punctuation — such dests are residual sentence translations, not usable
    # accepted variants for a gate match list.  The name key itself may still be
    # a legit record name; only the dest candidates get filtered by callers
    # (see clean_zh_candidates).

    return {
        'schema_version': '1.1',
        'generator': 'proper-noun-index.build',
        'file_count': len(files),
        'failed_files': failed,
        'names': names,
        'classify': {
            'enabled': sorted(enabled_keys),
            'optional': sorted(optional_keys),
            'semantic': sorted(fam_by_member),
        },
    }


def clean_zh_candidates(zh_list):
    """Filter dest candidates to forms usable as term match targets.

    Drops dests that are clearly residual sentences or sentence fragments
    (multi-char dest ending in 。！？…、，, or English-only technical strings)
    while keeping short legit name forms (e.g. 白漫领 / 独孤城).  A trailing
    。！？ on a long dest almost always means the dictionary row was a full
    sentence; a short name rarely carries one.  Callers show cleaned candidates
    for gate-facing suggestions; the raw index still holds every dest.
    """
    out = []
    for z in zh_list:
        zh = z['zh']
        if not CJK_RE.search(zh):
            continue
        if len(zh) >= 2 and re.search(r'[。！？…]$', zh):
            # keep only if the whole dest is a very short name-like phrase
            if len(zh) <= 3 and zh.endswith(('。', '！')):
                pass
            else:
                continue
        out.append(z)
    return out


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def _entry_is_entity(entry) -> bool:
    """Record-proven entity name (people / places / races / factions / cells)."""
    return bool(set(entry['records']) & (STRONG_RECS or set()))


def _tier_for(key: str, entry, enabled_set, semantic_set=None) -> str:
    """Classify a surfaced name for the scan inventory.

    ENTITY      record-proven people/place/race/faction, multi-word (near-
                unambiguous proper noun).
    ENTITY_LOW  record-proven but single-word (Nord / Agent / Dragon): the audit
                treats these LOW because prose may elide or extend them; the
                Agent must adjudicate before it becomes a term.
    MULTIWORD   other enabled official names (item / quest / book titles etc.).
    SEMANTIC    weekday / month system terms: forced HIGH by the audit layer
                regardless of their weak GMST records.
    OPTIONAL    weak or ambiguous names the audit keeps optional; only surfaced
                with --include-optional.
    """
    semantic_set = semantic_set or set()
    if key in semantic_set:
        return 'SEMANTIC'
    if _entry_is_entity(entry):
        return 'ENTITY_LOW' if entry['words'] == 1 else 'ENTITY'
    if key in enabled_set:
        return 'MULTIWORD'
    return 'OPTIONAL'


def _entry_is_clean_multiword(key: str, entry) -> bool:
    """Multi-word official name that is not a demoted sentence/objective head.
    Entity names ('the Rift', 'the Reach') are allowed even with a lowercase
    head; ordinary capitalized multi-word names must not start with a stop word,
    verb head or contraction."""
    if entry['words'] < 2:
        return False
    head = key.split()[0].rstrip('.,:;!?')
    if _entry_is_entity(entry):
        return True
    h = head.lower()
    if not head[:1].isupper() or len(head) < 2:
        return False
    if h in CONTRACTION_HEADS:
        return False
    if h in VERB_HEADS:
        return False
    if head in STOP_HEADS:
        return False
    return True


def load_target_rows(path: Path):
    a = _audit()
    if path.suffix.lower() == '.xml':
        return a.load_xml_rows(path), 'xml'
    return a.load_json_rows(path), 'json'


def scan_target(target: Path, index: dict, include_optional: bool = False):
    a = _audit()
    cls = index.get('classify', {})
    enabled_set = set(cls.get('enabled', []))
    optional_set = set(cls.get('optional', []))
    semantic_set = set(cls.get('semantic', []) or [])
    # Only keys the audit would consider names participate in matching.  If a
    # pre-classified index is absent (hand-built), fall back to structural
    # classification over the index's own names.
    if not enabled_set and not optional_set:
        for k in index['names']:
            e = index['names'][k]
            if _entry_is_entity(e) or _entry_is_clean_multiword(k, e):
                enabled_set.add(k)
            else:
                optional_set.add(k)
    enabled_map, optional_map = a.build_lookup_maps(enabled_set, optional_set)

    rows, label = load_target_rows(target)
    occ = {}
    samples = {}
    for row in rows:
        src = row.get('source') or ''
        if not src or not norm(src):
            continue
        # Article case is not a semantic distinction.  A prose sentence writes
        # "the Rift" (lowercase the); the index canonicalizes such place names
        # under "The Rift".  Align a sentence-leading lowercase "the" to its
        # capitalized form so the match is found, without changing the source
        # string used in samples.
        match_src = re.sub(r'(?<![A-Za-z])the\s+([A-Z])', r'The \1', src)
        # include_single=True lets optional weak names surface; we classify
        # surfaced names below into entity / multiword / optional.
        candidates = a.extract_candidates(match_src, enabled_map, optional_map,
                                          include_single=True, only_re=None)
        # semantic-family members are surfaced regardless of token matching:
        # weekdays/months live in weak GMST records and the token matcher never
        # sees them without this explicit scan.
        if semantic_set:
            src_lower = match_src.lower()
            for member in semantic_set:
                if member.lower() in src_lower and member not in candidates:
                    candidates.append(member)
        for key in candidates:
            if key in optional_set and not include_optional:
                continue
            entry = index['names'][key]
            tier = _tier_for(key, entry, enabled_set, semantic_set)
            if not include_optional and tier == 'OPTIONAL':
                continue
            if key not in occ:
                occ[key] = 0
                samples[key] = []
            occ[key] += 1
            if len(samples[key]) < 3 and src not in samples[key]:
                samples[key].append(src)

    out = []
    for key, count in occ.items():
        entry = index['names'][key]
        amb = False
        try:
            amb = bool(_compiler()._is_ambiguous(key))
        except Exception:
            amb = False
        zh_raw = entry['zh']
        zh = clean_zh_candidates(zh_raw)
        if not zh:
            zh = zh_raw
        tier = _tier_for(key, entry, enabled_set, semantic_set)
        out.append({
            'name': key,
            'occ': count,
            'tier': tier,
            'zh': zh,
            'records': entry['records'],
            'ambiguous_hint': amb,
            'samples': samples[key],
        })
    # ordering: record-proven people/places first, then multiword, by occ
    def _rank(it):
        return {'ENTITY': 0, 'SEMANTIC': 0, 'MULTIWORD': 1,
                'ENTITY_LOW': 2, 'OPTIONAL': 3}[it['tier']]
    out.sort(key=lambda x: (_rank(x), -x['occ'], x['name']))
    return out, label, len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_inventory(items):
    print(f"{'NAME':<42} {'OCC':>4}  {'TIER':<11}  OFFICIAL ZH (files)")
    print('-' * 118)
    for it in items:
        zh = ' | '.join(
            f"{z['zh']}({len(z.get('files', []))})" for z in it['zh'])
        hint = '  [ambiguous/alias risk]' if it['ambiguous_hint'] else ''
        print(f"{it['name']:<42} {it['occ']:>4}  {it['tier']:<11}  {zh}{hint}")
    print('-' * 120)
    print(f"total distinct official names: {len(items)}")
    ent = sum(1 for i in items if i['tier'] == 'ENTITY')
    sem = sum(1 for i in items if i['tier'] == 'SEMANTIC')
    mw = sum(1 for i in items if i['tier'] == 'MULTIWORD')
    low = sum(1 for i in items if i['tier'] == 'ENTITY_LOW')
    opt = sum(1 for i in items if i['tier'] == 'OPTIONAL')
    print(f"  ENTITY (people/places/races/factions): {ent}")
    if sem:
        print(f"  SEMANTIC (weekdays/months, forced HIGH): {sem}")
    print(f"  MULTIWORD (other official names): {mw}")
    print(f"  ENTITY_LOW (single-word strong-rec, needs review): {low}")
    if opt:
        print(f"  OPTIONAL (weak single-word names): {opt}")
    print('NOTE: inventory is a candidate list. Adjudicate each name into the MOD',
          'DICTIONARY.md / terms.json before compiling the contract.')


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='scan dictionary tree -> index JSON')
    b.add_argument('--dict', default=None, help='dictionary dir (default <project>/dictionary)')
    b.add_argument('--output', default=None,
                   help='index JSON output (default .work/_shared/proper-noun-index/index.json)')

    s = sub.add_parser('scan', help='list official proper nouns present in a MOD file')
    s.add_argument('index', help='index JSON from build')
    s.add_argument('--target', required=True, help='MOD xTranslator XML or translation-result JSON')
    s.add_argument('--include-optional', action='store_true',
                   help='also list weak single-word official names (noisy)')
    s.add_argument('--json', dest='outjson', default=None, help='write inventory to JSON')
    args = ap.parse_args()

    root = PROJECT_ROOT
    _init_rule_sets()

    if args.cmd == 'build':
        dict_dir = Path(args.dict) if args.dict else root / 'dictionary'
        if not dict_dir.is_absolute():
            dict_dir = root / dict_dir
        if not dict_dir.is_dir():
            print(f'error: dictionary dir not found: {dict_dir}', file=sys.stderr)
            return 2
        out = Path(args.output) if args.output else root / '.work' / '_shared' / 'proper-noun-index' / 'index.json'
        if not out.is_absolute():
            out = root / out
        data = build_index(dict_dir)
        if data['failed_files']:
            print(f"warning: {len(data['failed_files'])} dictionary file(s) failed to parse:",
                  file=sys.stderr)
            for fp, err in data['failed_files'][:5]:
                print(f'  - {fp}: {err}', file=sys.stderr)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f'dictionary files: {data["file_count"]}')
        print(f'distinct English sources with official Chinese evidence: {len(data["names"])}')
        print(f'index written: {out}')
        return 0

    if args.cmd == 'scan':
        idx_path = Path(args.index)
        if not idx_path.is_absolute():
            idx_path = root / idx_path
        if not idx_path.exists():
            print(f'error: index not found: {idx_path}', file=sys.stderr)
            return 2
        try:
            index = json.load(open(idx_path, encoding='utf-8-sig'))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            print(f'error: cannot read index {idx_path}: {exc}', file=sys.stderr)
            return 2
        if 'names' not in index:
            print(f'error: {idx_path} is not a proper-noun index (no "names")', file=sys.stderr)
            return 2
        target = Path(args.target)
        if not target.is_absolute():
            target = root / target
        if not target.exists():
            print(f'error: target not found: {target}', file=sys.stderr)
            return 2
        items, label, nrows = scan_target(target, index, include_optional=args.include_optional)
        print(f'{label} rows: {nrows}', flush=True)
        _print_inventory(items)
        if args.outjson:
            out = Path(args.outjson)
            if not out.is_absolute():
                out = root / out
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, 'w', encoding='utf-8') as fh:
                json.dump({'target': str(target), 'rows': nrows, 'names': items},
                          fh, ensure_ascii=False, indent=1)
            print(f'inventory written: {out}')
        return 0

    return 2


if __name__ == '__main__':
    sys.exit(main())
