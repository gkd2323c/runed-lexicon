# -*- coding: utf-8 -*-
"""Self-test for the gate scripts against the Phase 0 corpus.

Asserts per gate case:
  - contract term checks via term_match (bindings + terms)
  - keep_list / protected_tokens if present in contract
  - machine_assert (must_contain / must_not_contain) if present (interjection etc.)
  - golden must invert where the bad translation FAILs
  - semantic-only cases (expect PASS, no machine_assert) validate as PASS
"""
import json, io, sys, glob, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from term_match import ResolvedTerm, check_unit
from quality_gate import (keep_issue, placeholder_issue, charset_issue, truncation_issue,
                          standalone_forbidden_issues)

# project root: <skill>/scripts/../.. = .agents/skills/<skill>/scripts -> up 4 to repo root
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_SCRIPT_DIR)
_AGENTS_DIR = os.path.dirname(_SKILL_DIR)
_DOT_DIR = os.path.dirname(_AGENTS_DIR)   # .agents
_PROJECT_ROOT = os.path.dirname(_DOT_DIR)  # repo root
ROOT = os.path.join(_PROJECT_ROOT, 'corpus', 'translation-regression', 'cases', 'gate')

# sanity: if repo moved, fail loudly instead of silently scanning nothing
if not os.path.isdir(ROOT):
    print(f'[fatal] corpus gate cases dir not found: {ROOT}')
    sys.exit(2)


def gate_issues(source, translation, contract, case=None):
    """Replicate gate per-unit logic on a gate-case contract (bindings+terms inline).
    Also enforces contract.global_bans (project-wide TERM004) when present."""
    issues = []
    bindings = contract.get('bindings') or []
    terms = contract.get('terms') or {}
    applicable = []
    for b in bindings:
        t = terms.get(b['term_id'])
        if t is None:
            continue
        span = b.get('source_span', '')
        if span and span not in source:
            continue
        applicable.append(b)
    resolved = [ResolvedTerm(b['term_id'], terms[b['term_id']],
                             bool(b.get('required', True)),
                             b.get('required_target'), b.get('source_span', ''))
                for b in applicable]
    issues += check_unit(source, translation, resolved, terms)

    # R19: forbidden variants of unbound terms are still enforced when the
    # term's English anchor appears in source (reuses the real gate function).
    if translation != source:
        issues += standalone_forbidden_issues(
            source, translation, terms, {r.term_id for r in resolved})

    # project-wide ban list (TERM004): enforced on every translated line
    if translation != source:
        from term_match import resolve_global_bans, find_global_ban_hits, cross_target_covered
        gbans = resolve_global_bans(contract)
        for ban in gbans:
            for f in find_global_ban_hits(source, translation, ban):
                # 跨条 target/forbidden 交叉豁免（与 quality_gate.global_ban_issue 一致）
                if cross_target_covered(source, translation, f, gbans, ban.get('english') or ''):
                    continue
                reason = ban.get('reason') or '项目级禁用词'
                issues.append({'code': 'TERM004', 'severity': 'FAIL',
                               'detail': f'全局禁用词 {f!r}（{ban["english"]}）出现: {reason}'})

    # keep / placeholder / charset (contract-level overrides)
    keep_list = contract.get('keep_list') or []
    if keep_list:
        issues += keep_issue({'source': source, 'translation': translation}, keep_list)
    prot = contract.get('protected_tokens')
    if prot:
        issues += placeholder_issue({'source': source, 'translation': translation}, prot)
    issues += charset_issue(translation)
    issues += truncation_issue({'source': source, 'translation': translation})

    ma = (case or {}).get('machine_assert') or {}
    macode = ma.get('code') or 'ASSERT'
    if ma.get('must_contain'):
        for m in ma['must_contain']:
            if m not in translation:
                issues.append({'code': macode, 'severity': 'FAIL', 'detail': f'缺少 {m!r}'})
    if ma.get('must_not_contain'):
        for m in ma['must_not_contain']:
            if m in translation:
                issues.append({'code': macode, 'severity': 'FAIL', 'detail': f'含禁止内容 {m!r}'})
    return issues


def verdict_of(issues):
    if any(i['severity'] == 'FAIL' for i in issues):
        return 'FAIL'
    if any(i['severity'] == 'WARNING' for i in issues):
        return 'WARNING'
    return 'PASS'


def main():
    files = sorted(glob.glob(os.path.join(ROOT, '*.json')))
    total = 0
    failed = 0
    for fp in files:
        d = json.load(open(fp, encoding='utf-8-sig'))
        for c in d.get('cases', []):
            if c.get('test_layer') != 'gate':
                continue
            total += 1
            src = c['input']['source']
            contract = c['input']['contract']
            exp = c['expect']['verdict']
            exp_codes = c['expect'].get('codes', [])
            issues = gate_issues(src, c['input']['translation'], contract, c)
            got = verdict_of(issues)
            codes = sorted(set(i['code'] for i in issues if i['severity'] == 'FAIL'))
            ok = (got == exp) and (not exp_codes or set(exp_codes).issubset(set(codes)))

            # golden must be clean (no FAIL) whenever provided
            if c.get('golden') and c['golden'] != c['input']['translation']:
                gissues = gate_issues(src, c['golden'], contract)
                ggot = verdict_of(gissues)
                if ggot == 'FAIL':
                    ok = False
                    print(f"      golden FAIL codes={sorted(set(i['code'] for i in gissues))}")

            if not ok:
                failed += 1
                print(f"[FAIL] {c['case_id']}: expect {exp} {exp_codes}, got {got} {codes}")
                for i in issues[:6]:
                    print(f'        {i["code"]}: {i["detail"]}')
            else:
                print(f"[ok] {c['case_id']}: {got} {codes}")
    print(f'\nTOTAL {total}  FAILED {failed}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
