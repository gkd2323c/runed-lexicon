# -*- coding: utf-8 -*-
"""noun_consistency_scan.py — 名词翻译一致性扫描（只读）。

核心检测：同一英文 Source（名词型记录）被译成多个不同中文 Dest 的分裂组。
这是跨 MOD 复用的最小核心，输入只需一份 translated XML。

可选扩展池（需额外输入，非核心）：
  - B 池：英文头名词 → 中文后缀构词不一致（同 A 池输入）
  - C 池：官方专名 HIGH 候选复核（需 --audit，dictionary-noun-audit 的 --json 产物）
  - D 池：官方专名登记缺口（需 --scan，proper-noun-index scan 的 --json 产物 + --terms）
  - E 池：未译行（Dest==Source）按开发信号预分类（同 A 池输入）

安全边界：只读。不修改 canonical XML、terms.json、DICTIONARY.md、PROGRESS.md。
输出：JSON 分片 + 终端统计。性能基线见 SKILL.md。

用法示例见 SKILL.md。
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

DIALOG_REC = {'INFO:NAM1', 'DIAL:FULL'}


def parse_xml(path):
    """解析 xTranslator XML，返回行字典列表。REC/EDID 是 String 的子元素。"""
    root = ET.parse(path).getroot()
    strs = root.findall('.//String')
    rows = []
    for i, s in enumerate(strs):
        def g(tag):
            e = s.find(tag)
            return e.text if e is not None and e.text else ''
        rows.append({'idx': i, 'rec': g('REC'), 'edid': g('EDID'),
                     'src': g('Source'), 'dst': g('Dest')})
    return rows


def noun_rows(rows):
    """对白行（INFO:NAM1 / DIAL:FULL）不算名词行。"""
    return [r for r in rows if r['rec'] not in DIALOG_REC and r['src'].strip()]


def pool_a(noun, min_variants=2):
    """同 Source 多 Dest 分裂组。返回 [{kid, source, variants, rows}, ...]"""
    groups = defaultdict(list)
    for r in noun:
        if r['dst'] != r['src']:
            groups[r['src'].strip().lower()].append(r)
    out = []
    for k, g in groups.items():
        dsts = sorted({x['dst'].strip() for x in g})
        if len(dsts) >= min_variants:
            out.append({'source': g[0]['src'].strip()[:200],
                        'variant_count': len(dsts),
                        'variants': dsts,
                        'rows': [{'idx': x['idx'], 'rec': x['rec'],
                                  'dest': x['dst'].strip()[:160]} for x in g]})
    out.sort(key=lambda x: (-x['variant_count'], -len(x['rows'])))
    for n, g in enumerate(out, 1):
        g['kid'] = 'A%d' % n
    return out


def pool_b(noun):
    """英文头名词 → 中文后缀构词不一致（实验性，噪声较大）。"""
    head_tail = re.compile(r'([A-Za-z\'\-]+)\s*$')
    by_head = defaultdict(lambda: defaultdict(list))
    for r in noun:
        if r['dst'] == r['src'] or not r['dst'].strip():
            continue
        m = head_tail.search(r['src'].strip())
        if not m or len(m.group(1)) < 4:
            continue
        tail = re.sub(r'[^一-鿿]', '', r['dst'].strip())[-3:]
        if tail:
            by_head[m.group(1).lower()][tail].append(r)
    out = []
    for head, tails in by_head.items():
        if len(tails) < 2:
            continue
        n = sum(len(v) for v in tails.values())
        ranked = sorted(tails.items(), key=lambda kv: len(kv[1]))
        if ranked and n >= 8 and len(ranked[0][1]) >= 3 \
                and len(ranked[0][1]) < len(ranked[-1][1]):
            out.append({'head_en': head, 'total_rows': n,
                        'suffix_forms': [{'zh': t, 'count': len(v),
                                          'sample_idx': [x['idx'] for x in v[:4]]}
                                         for t, v in sorted(tails.items(),
                                                            key=lambda kv: -len(kv[1]))]})
    out.sort(key=lambda x: -x['total_rows'])
    return out


def pool_c(audit, by_en):
    """官方专名 HIGH 候选（已译行）。依赖 dictionary-noun-audit 的 --json 产物。"""
    out = []
    for x in audit:
        if x['confidence'] != 'HIGH' or x['source'] == x['dest']:
            continue
        out.append({'idx': x['xml_index'], 'rec': x['rec'], 'edid': x['edid'],
                    'term_en': x['matched_term'],
                    'official': x['official_translation'],
                    'source': x['source'][:200], 'dest': x['dest'][:200],
                    **({'term_zh': by_en[x['matched_term'].lower()]['zh']}
                       if x['matched_term'].lower() in by_en else {})})
    return out


def pool_d(scan_names, noun, by_en):
    """官方专名登记缺口（occ>=3 且名称级命中且未登记 terms）。"""
    out = []
    for e in scan_names:
        nm = e.get('name') or ''
        occ = e.get('occ', 0)
        if occ < 3 or nm.lower() in by_en:
            continue
        hits = [r for r in noun
                if nm.lower() in r['src'].lower() and len(r['src'].strip()) <= 60]
        variants = sorted({r['dst'].strip() for r in hits if r['dst'] != r['src']})
        if not hits or not variants:
            continue
        out.append({'term_en': nm, 'tier': e.get('tier', ''), 'occ': occ,
                    'official': [z['zh'] for z in e.get('zh', [])][:4],
                    'ambiguous_hint': e.get('ambiguous_hint', False),
                    'name_rows': len(hits),
                    'sample_idx': [r['idx'] for r in hits[:4]],
                    'sample_src': [r['src'].strip()[:50] for r in hits[:3]],
                    'dest_variants': [v[:40] for v in variants[:6]]})
    out.sort(key=lambda x: -x['name_rows'])
    for n, g in enumerate(out, 1):
        g['kid'] = 'D%d' % n
    return out


def pool_e(noun):
    """未译行（Dest==Source）开发信号预分类：无信号者需人判。"""
    dev = re.compile(
        r'(template|marker|testing|test\b|dev|debug|wip|placeholder|unused|dono|donotuse'
        r'|_effect|effects\b|^effect\b|visual|fx\b|spawn|sounds?\b|music|snapshot'
        r'|copy of|edit me|sample|scratch)', re.I)
    out = []
    for r in noun:
        if r['dst'] != r['src'] or not r['src'].strip():
            continue
        sig = dev.search(r['src']) or dev.search(r['edid'])
        echo = bool(r['edid'] and (r['src'].strip() in r['edid'] or r['edid'] in r['src']))
        if sig or echo:
            continue
        out.append({'idx': r['idx'], 'rec': r['rec'], 'edid': r['edid'],
                    'source': r['src'][:160]})
    return out


def main():
    ap = argparse.ArgumentParser(description='名词翻译一致性扫描（只读）')
    ap.add_argument('--xml', required=True, help='translated XML 路径')
    ap.add_argument('--out', default='_tmp/data/noun-scan',
                    help='输出目录（默认 _tmp/data/noun-scan）')
    ap.add_argument('--pools', default='A',
                    help='要跑的池，逗号分隔（A/B/C/D/E，默认 A；A 为核心）')
    ap.add_argument('--min-variants', type=int, default=2, help='A 池分裂阈值')
    ap.add_argument('--audit', help='dictionary-noun-audit --json 产物（C 池需要）')
    ap.add_argument('--scan', help='proper-noun-index scan --json 产物（D 池需要）')
    ap.add_argument('--terms', help='MOD terms.json（C/D 池需要）')
    ap.add_argument('--cap', type=int, default=170,
                    help='分片上限（默认 170 行/片，供子代理判读）')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = parse_xml(args.xml)
    noun = noun_rows(rows)
    by_en = {}
    if args.terms and os.path.exists(args.terms):
        t = json.load(open(args.terms, encoding='utf-8'))
        by_en = {x['english'].lower(): x for x in (t['terms'] if isinstance(t, dict) else t)}

    pools = [p.strip().upper() for p in args.pools.split(',')]
    result = {}

    if 'A' in pools:
        pa = pool_a(noun, args.min_variants)
        result['A'] = pa
        print('池 A 同 Source 多 Dest 组数:', len(pa),
              '| 涉及行:', sum(len(g['rows']) for g in pa))

    if 'B' in pools:
        pb = pool_b(noun)
        result['B'] = pb
        print('池 B 构词后缀不一致 组数:', len(pb), '（实验性，噪声较大）')

    if 'C' in pools:
        if not args.audit:
            print('C 池需要 --audit', file=sys.stderr)
        else:
            audit = json.load(open(args.audit, encoding='utf-8'))
            pc = pool_c(audit, by_en)
            result['C'] = pc
            print('池 C HIGH 官方候选（已译）:', len(pc))

    if 'D' in pools:
        if not args.scan:
            print('D 池需要 --scan', file=sys.stderr)
        else:
            scan = json.load(open(args.scan, encoding='utf-8'))
            pd = pool_d(scan['names'], noun, by_en)
            result['D'] = pd
            print('池 D 未登记官方专名:', len(pd),
                  '| 分层:', dict(Counter(x['tier'] for x in pd)))

    if 'E' in pools:
        pe = pool_e(noun)
        result['E'] = pe
        print('池 E 未译且无开发信号（需人判）:', len(pe))

    # 写盘：整池 JSON + 分片（供子代理逐条判读）
    manifest = {}
    for tag, data in result.items():
        with open(os.path.join(args.out, 'pool-%s.json' % tag.lower()),
                  'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        pieces = []
        for n in range(0, len(data), args.cap):
            part = data[n:n + args.cap]
            if not part:
                break
            p = os.path.join(args.out, 'pool-%s-%d.json' % (tag.lower(), n // args.cap + 1))
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(part, f, ensure_ascii=False, indent=1)
            pieces.append(p)
        manifest[tag] = pieces
    with open(os.path.join(args.out, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print('输出:', args.out)


if __name__ == '__main__':
    main()
