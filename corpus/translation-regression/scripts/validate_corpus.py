# -*- coding: utf-8 -*-
"""Corpus 结构验收：
1) 所有 cases JSON 可解析
2) 所有 case 含必需字段、test_layer 合法
3) 分类 / 层统计
4) schema 文件本身合法 JSON、$schema 用标准草案标识、$id 为本地虚拟标识（无编造 URL）
用法：py -3 corpus/translation-regression/scripts/validate_corpus.py
"""
import json, io, sys, glob, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # corpus/translation-regression
errors = []

# schema 文件合法 JSON + 本地 $id + 无编造外部 URL
for sp in sorted(glob.glob(os.path.join(ROOT, 'schema', '*.json'))):
    try:
        d = json.load(open(sp, encoding='utf-8'))
    except Exception as e:
        errors.append(f'{sp}: schema JSON 解析失败 {e}')
        continue
    sid = d.get('$id', '')
    if sid.startswith('http'):
        errors.append(f'{sp}: $id 是外部 URL（应为本地虚拟标识）')
    if 'agentskills.io' in d.get('$schema', ''):
        errors.append(f'{sp}: $schema 含编造的 agentskills.io URL')
    print(f'[schema OK] {os.path.basename(sp)}  $id={sid}')

total = 0
by_layer = {}
by_cat = {}
for cp in sorted(glob.glob(os.path.join(ROOT, 'cases', '**', '*.json'), recursive=True)):
    try:
        d = json.load(open(cp, encoding='utf-8'))
    except Exception as e:
        errors.append(f'{cp}: JSON 解析失败 {e}')
        continue
    cases = d.get('cases', [])
    if not isinstance(cases, list):
        errors.append(f'{cp}: cases 不是数组')
        continue
    print(f'[file] {os.path.relpath(cp, ROOT)}: {len(cases)} cases')
    seen = set()
    for c in cases:
        total += 1
        cid = c.get('case_id', '')
        if cid in seen:
            errors.append(f'{cp}: 重复 case_id {cid}')
        seen.add(cid)
        layer = c.get('test_layer')
        cat = c.get('category')
        by_layer[layer] = by_layer.get(layer, 0) + 1
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if layer not in ('gate', 'translator'):
            errors.append(f'{cp} {cid}: test_layer 非法 {layer}')
        for req in ('case_id', 'test_layer', 'category', 'plugin', 'origin'):
            if req not in c:
                errors.append(f'{cp} {cid}: 缺 {req}')
        if layer == 'gate':
            inp = c.get('input', {})
            if 'source' not in inp or 'translation' not in inp or 'contract' not in inp:
                errors.append(f'{cp} {cid}: gate 层 input 缺 source/translation/contract')
            if 'expect' not in c or 'verdict' not in c.get('expect', {}):
                errors.append(f'{cp} {cid}: gate 层缺 expect.verdict')
        if layer == 'translator':
            inp = c.get('input', {})
            if 'semantic_unit' not in inp:
                errors.append(f'{cp} {cid}: translator 层缺 input.semantic_unit')
            if 'assertion' not in c:
                errors.append(f'{cp} {cid}: translator 层缺 assertion')

print('\n==== 统计 ====')
print('total cases:', total)
for k, v in sorted(by_layer.items()):
    print(f'  layer {k}: {v}')
for k, v in sorted(by_cat.items()):
    print(f'  category {k}: {v}')

print('\n==== 结果 ====')
if errors:
    print('ERRORS:')
    for e in errors:
        print('  -', e)
    sys.exit(1)
print('ALL OK')
