# -*- coding: utf-8 -*-
r"""枚举写回前的**全部**同源多形组（round_pipeline 的预检只打前 10 组）。

口径严格复刻 round_pipeline.same_source_precheck：
  forms = canonical 既有已译形 + 本批 map.json 译文
  bad   = 同源有 >1 形，且源句不在 same-source-exemptions 登记里

本脚本不依赖任何具体 MOD：--stem 决定全部路径。

用法：
  py -3 same_source_groups.py --stem <STEM> --batch <BATCH-ID> [<BATCH-ID> ...]
                             [--out <txt>] [--show-all]
"""
import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

BATCHOPS = Path('.agents/skills/translation-batch-ops/scripts')


def load_exemptions(work, stem):
    """优先复用 close_round.load_exemptions；不可用时退回本地实现。"""
    p = work / 'contracts' / (stem + '-same-source-exemptions.json')
    if not p.is_file():
        return set()
    if str(BATCHOPS.resolve()) not in sys.path:
        sys.path.insert(0, str(BATCHOPS.resolve()))
    try:
        from close_round import load_exemptions as _le
        return set(_le(p))
    except Exception:
        data = json.loads(p.read_text(encoding='utf-8'))
        ex = data.get('exemptions', data)
        return set(ex.keys()) if isinstance(ex, dict) else set(ex)


def build_index(work):
    """idx -> 所属批次（扫所有已备料批次的 index.txt）。"""
    owner = {}
    for p in (work / 'batches').glob('*/index.txt'):
        for x in p.read_text(encoding='utf-8').split():
            if x.strip():
                owner.setdefault(int(x), p.parent.name)
    return owner


def collect(stem, batches, srcxml, canon):
    srcs = [s.findtext('Source') or '' for s in ET.parse(srcxml).getroot().iter('String')]
    work = Path('.work') / stem
    forms = {}
    owner = {}

    def add(src, dst, who, key):
        if not src or not dst:
            return
        forms.setdefault(src, {}).setdefault(dst, []).append(key)
        owner.setdefault(src, {}).setdefault(dst, who)

    for i, s in enumerate(ET.parse(canon).getroot().iter('String')):
        src, dst = s.findtext('Source') or '', s.findtext('Dest') or ''
        if src and dst and dst != src:
            add(src, dst, 'canonical', str(i))
    for b in batches:
        mf = work / 'batches' / b / 'map.json'
        if not mf.is_file():
            print('!! %s 无 map.json（整批会在写回前 STOP）' % mf, file=sys.stderr)
            continue
        for k, v in json.loads(mf.read_text(encoding='utf-8')).items():
            i = int(k)
            src = srcs[i] if i < len(srcs) else ''
            add(src, str((v or {}).get('translation') or '').strip(), b, k)

    exempt = load_exemptions(work, stem)
    bad = [(s, f) for s, f in forms.items() if len(f) > 1 and s not in exempt]
    return bad, owner, exempt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--batch', nargs='+', required=True)
    ap.add_argument('--out', default=None, help='报告落盘路径（默认只打印）')
    ap.add_argument('--show-all', action='store_true',
                    help='打印每一组的全部形态明细（默认每组只显示各形的行号列表）')
    a = ap.parse_args()

    base = Path('mods') / (a.stem + '.esp')
    srcxml = base / (a.stem + '_english_chinese.xml')
    canon = base / (a.stem + '_english_chinese_translated.xml')
    for p in (srcxml, canon):
        if not p.is_file():
            print('!! 缺文件：%s' % p, file=sys.stderr)
            return 1

    bad, owner, exempt = collect(a.stem, a.batch, srcxml, canon)
    out = ['多形组 %d（豁免源句 %d 个已按 round_pipeline 口径跳过）' % (len(bad), len(exempt))]
    for n, (src, f) in enumerate(bad, 1):
        out.append('')
        out.append('[%02d] SRC: %s' % (n, src))
        for t, ks in sorted(f.items(), key=lambda kv: -len(kv[1])):
            out.append('     %2dx [%s] %s' % (len(ks), owner.get(src, {}).get(t, '?'), t))
            if a.show_all:
                out.append('        idx: %s' % ' '.join(ks))
    out.append('')
    out.append('batches-involved: %s' % sorted({o for s, f in bad
                                               for o in owner.get(s, {}).values()}))
    text = '\n'.join(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with io.open(a.out, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text + '\n')
        print('报告 -> %s' % a.out)
    print(text)
    return 0 if not bad else 0


if __name__ == '__main__':
    sys.exit(main())
