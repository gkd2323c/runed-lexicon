# -*- coding: utf-8 -*-
r"""裁决用的两把尺：查契约词条 target/forbidden；查一个英文词在全库的译形分布。

这两件事每轮裁决都要做，而它们都是「只看一个数字就会得出错结论」的操作：
  · contract_get  —— 判组前查 target，契约 target 优先于批内多数形。
  · form_census   —— 判组前查盘面，机械多数不等于正确，但全库无主形时也不许按 2:1 换形。

用法：
  py -3 contract_get.py --stem <STEM> harvest distinction maker
  py -3 form_census.py --stem <STEM> --word harvest --word bearer --form 收成 --form 收获
"""
import argparse
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# 英文常见屈折后缀。契约写词元（harvest / maker），源句写屈折形（harvests / makers）。
# 不做还原的话词表核对会静默漏检——看起来查过了，其实没生效。
SUFFIXES = ("'s", "s", "es", "ed", "ing", "'", "en")


def stem_candidates(word):
    w = word.lower()
    out = [w]
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            out.append(w[: -len(suf)])
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)]
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                out.append(stem[:-1])
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def load_terms(stem):
    p = Path('.work') / stem / 'contracts' / (stem + '.compiled.json')
    if not p.is_file():
        return {}
    terms = (json.loads(p.read_text(encoding='utf-8')) or {}).get('terms') or {}
    if isinstance(terms, list):
        terms = {t.get('term_id') or t.get('source'): t for t in terms if isinstance(t, dict)}
    return terms


def cmd_contract(a):
    terms = load_terms(a.stem)
    if not terms:
        print('!! 契约缺失或为空：.work/%s/contracts/%s.compiled.json' % (a.stem, a.stem))
        return 1
    for w in a.words:
        cands = stem_candidates(w)
        hit = None
        for c in cands:
            for k, v in terms.items():
                if k.lower() == c:
                    hit = (k, v)
                    break
            if hit:
                break
        if not hit:
            print('%-16s -> 契约无此词条（源词形态：%s）' % (w, '/'.join(cands)))
            continue
        k, v = hit
        acc = ((v.get('match') or {}).get('accepted')) or ([v['target']] if v.get('target') else [])
        print('%-16s target=%r enforcement=%r status=%r accepted=%r forbidden=%r'
              % (k, v.get('target'), v.get('enforcement'), v.get('decision_status'),
                 acc, v.get('forbidden')))
        if v.get('note'):
            print('                 note: %s' % str(v['note'])[:220])
    return 0


def cmd_census(a):
    base = Path('mods') / (a.stem + '.esp')
    canon = base / (a.stem + '_english_chinese_translated.xml')
    if not canon.is_file():
        print('!! 缺 canonical：%s' % canon)
        return 1
    rows = []
    for i, s in enumerate(ET.parse(canon).getroot().iter('String')):
        src = (s.findtext('Source') or '').strip()
        dst = (s.findtext('Dest') or '').strip()
        if src and dst and src != dst:
            rows.append((i, src, dst))
    print('canonical 已译行 %d' % len(rows))
    for w in a.words:
        cands = set(stem_candidates(w))
        rx = re.compile(r'\b(?:%s)\b' % '|'.join(re.escape(c) for c in sorted(cands, key=len, reverse=True)),
                        re.I)
        hit = [(i, s, d) for i, s, d in rows if rx.search(s)]
        c = Counter()
        for i, s, d in hit:
            for f in (a.forms or []):
                if f in d:
                    c[f] += 1
                    break
            else:
                c['<其它>'] += 1
        print('\n%-18s 命中 %d 行  %s' % (w, len(hit), dict(c.most_common(10))))
        for i, s, d in hit[:a.show]:
            print('   [%d] %s' % (i, s[:96]))
            print('        -> %s' % d[:100])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('mode', choices=['contract', 'census'])
    ap.add_argument('words', nargs='+')
    ap.add_argument('--form', dest='forms', action='append',
                    help='census 模式：指定要统计的中文形，可重复。省略则只打命中行')
    ap.add_argument('--show', type=int, default=3, help='census 模式：每个词打几行样例')
    a = ap.parse_args()
    return cmd_contract(a) if a.mode == 'contract' else cmd_census(a)


if __name__ == '__main__':
    sys.exit(main())
