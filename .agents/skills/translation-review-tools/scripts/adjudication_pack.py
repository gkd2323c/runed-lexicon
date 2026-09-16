# -*- coding: utf-8 -*-
"""adjudication_pack.py — 裁决包生成与消费（裁决层并行化的确定性底座）。

两个子命令：

  build    把扫描产出（noun-consistency-scan pool JSON）编译成裁决证据包：
           每候选一块，附全行 REC/EDID/Source、EDID 家族分布、terms.json 登记
           状态。裁决子代理零查证——不读词典、不跑脚本，只对每候选做三选一判定。
           产出双格式：pack-NNN.txt（子代理读，多行排版）+ pack-NNN.json
           （主会话消费基准，全量行数据）。

  consume  消费子代理 verdict JSON，机械校验后生成修正计划：
           - 键集合 == pack 候选集合（缺/多即拒）
           - verdict ∈ {unify, legitimate-split, different-object}
           - unify 必须给 target 且 target ∈ 该候选已出现形态集合（禁止第三形态）
           - split 类必须给非空 reason
           通过的 unify 转 plan JSON（find/replace/idx 指令集），供主会话终审后
           调 apply_fixes.py --find/--replace/--idx-list 走既有 patch 链路。
           legitimate-split / different-object 只汇总报告，主会话全量复读。

只读输入（pool / XML / terms）；写出仅限 --out 与 --plan-out 指定路径。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VERDICTS = ('unify', 'legitimate-split', 'different-object')


# ---------------------------------------------------------------- 公共

def _norm(s):
    return re.sub(r'\s+', ' ', (s or '').strip()).lower()


def load_xml_index(xml_path):
    """idx(0-based String 序号) -> {rec, edid, source, dest}。循环外一次构建。"""
    root = ET.parse(xml_path).getroot()
    out = {}
    for i, s in enumerate(root.findall('.//String')):
        out[i] = {
            'rec': s.findtext('REC') or '',
            'edid': s.findtext('EDID') or '',
            'source': s.findtext('Source') or '',
            'dest': s.findtext('Dest') or '',
        }
    return out


def load_terms(path):
    """terms.json -> {归一 english: {zh, status}}。english 含 '/' 多形态时拆开登记。"""
    if not path:
        return {}
    data = json.load(open(path, encoding='utf-8-sig'))
    out = {}
    for t in data.get('terms') or []:
        eng = (t.get('english') or '').strip()
        if not eng:
            continue
        for form in re.split(r'\s*/\s*', eng):
            key = _norm(form)
            if key:
                out.setdefault(key, {'english': eng, 'zh': t.get('zh') or '',
                                     'status': t.get('status') or ''})
    return out


def load_dialogue_map(path):
    """dialogue-context JSON -> {INFO FormID（大写无括号）: {dial, quest, category}}。

    xTranslator 导出里 INFO 行的 EDID 常是 FormID 而非可读编辑器名；对话主题
    （DIAL editor_id + quest）是裁决对白行形态分裂时唯一可靠的「同一场景」证据。
    """
    if not path:
        return {}
    data = json.load(open(path, encoding='utf-8-sig'))
    out = {}
    for d in data.get('dialogues') or []:
        dial = d.get('editor_id') or d.get('form_id') or ''
        quest = d.get('quest_edid') or ''
        cat = d.get('category') or ''
        for info in d.get('infos') or []:
            fid = (info.get('form_id') or '').strip().upper()
            if fid:
                out[fid] = {'dial': dial, 'quest': quest, 'category': cat}
    return out


def edid_family(edid):
    """EDID 家族：去尾数字与序号段，用于判断同组多形态是否同一对象家族。"""
    return re.sub(r'[\d_]+$', '', edid or '')


def row_topic(row, dial_map):
    """INFO/DIAL 行的对话主题（经 FormID 反查 dialogue-context）；无则空。"""
    fid = (row.get('edid') or '').strip().strip('[]').upper()
    hit = dial_map.get(fid)
    if not hit:
        return ''
    q = f" ({hit['quest']})" if hit['quest'] and hit['quest'] != hit['dial'] else ''
    return f"{hit['dial']}{q}"


# ---------------------------------------------------------------- build

def build(args):
    pool = json.load(open(args.pool, encoding='utf-8-sig'))
    if not isinstance(pool, list):
        raise SystemExit('error: pool JSON 必须是数组（noun-consistency-scan pool-<letter>.json）')
    xml_idx = load_xml_index(args.xml)
    terms = load_terms(args.terms)
    dial_map = load_dialogue_map(args.dialogue_context)

    groups = []
    for n, g in enumerate(pool, 1):
        if not isinstance(g, dict) or 'source' not in g or 'rows' not in g:
            raise SystemExit(f'error: pool[{n}] 缺 source/rows 字段，不是支持的 pool 形态')
        kid = g.get('kid') or f'A{n}'
        rows = []
        for r in g['rows']:
            idx = r['idx']
            x = xml_idx.get(idx)
            if x is None:
                raise SystemExit(f'error: {kid} 引用 idx={idx} 超出 XML 行数 {len(xml_idx)}（pool 与 XML 不配套）')
            rows.append({'idx': idx, 'rec': x['rec'], 'edid': x['edid'],
                         'source': x['source'], 'dest': x['dest']})
        # 形态 → [idx]（以 pool variants 为准与 XML 实读对账）
        var_map = {}
        for r in rows:
            var_map.setdefault(r['dest'], []).append(r['idx'])
        fam = {}
        for r in rows:
            topic = row_topic(r, dial_map)
            r['topic'] = topic
            f = topic or edid_family(r['edid'])
            fam[f] = fam.get(f, 0) + 1
        term_hit = terms.get(_norm(g['source']))
        groups.append({
            'kid': kid,
            'source': g['source'],
            'variants': sorted(var_map.keys()),
            'variant_rows': {k: v for k, v in sorted(var_map.items(), key=lambda kv: -len(kv[1]))},
            'rows': rows,
            'edid_families': fam,
            'terms': term_hit,
        })

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = max(1, args.cap)
    manifest = []
    for shard_i in range(0, len(groups), cap):
        chunk = groups[shard_i:shard_i + cap]
        stem = f'pack-{shard_i // cap + 1:03d}'
        txt_path = out_dir / f'{stem}.txt'
        json_path = out_dir / f'{stem}.json'
        txt_path.write_text(render_txt(chunk), encoding='utf-8')
        json_path.write_text(json.dumps({'groups': chunk}, ensure_ascii=False, indent=1), encoding='utf-8')
        manifest.append({'txt': str(txt_path), 'json': str(json_path),
                         'kids': [g['kid'] for g in chunk]})
    (out_dir / 'manifest.json').write_text(
        json.dumps({'pool': args.pool, 'xml': args.xml, 'groups': len(groups),
                    'shards': manifest}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'groups: {len(groups)}  shards: {len(manifest)}  -> {out_dir}')
    for g in groups:
        print(f"  [{g['kid']}] {g['source'][:60]}  ({len(g['variants'])} 形态, {len(g['rows'])} 行)")


def render_txt(groups):
    """子代理阅读面：每候选一块。行数 >8 的形态压缩尾部（idx 全量在 json 侧）。"""
    blocks = []
    for g in groups:
        n = len(g['rows'])
        head = f"===== [{g['kid']}] {g['source']}  ({len(g['variants'])} 形态, {n} 行) ====="
        lines = [head]
        for dest, idxs in g['variant_rows'].items():
            lines.append(f'  形态「{dest}」 {len(idxs)} 行:')
            show = [r for r in g['rows'] if r['dest'] == dest]
            for r in show[:8]:
                src = r['source']
                src = src if len(src) <= 80 else src[:77] + '...'
                note = '' if r['source'] == g['source'] else f'（源句: {src}）'
                topic = f" 主题:{r['topic']}" if r.get('topic') else ''
                lines.append(f"    [{r['idx']}] {r['rec']} {r['edid']}{topic} {note}".rstrip())
            if len(show) > 8:
                rest = ','.join(str(r['idx']) for r in show[8:])
                lines.append(f'    ... 其余 {len(show) - 8} 行 idx: {rest}')
        named_fams = {k: v for k, v in g['edid_families'].items() if k and not re.fullmatch(r'\[?[0-9A-Fa-f]{6,8}\]?', k)}
        if named_fams:
            fam = ', '.join(f'{k}×{v}' for k, v in sorted(named_fams.items(), key=lambda kv: -kv[1]))
            lines.append(f'  家族/主题分布: {fam}')
        else:
            lines.append('  家族/主题分布: （FormID 导出且无对话上下文，无家族信息）')
        if g['terms']:
            t = g['terms']
            lines.append(f"  TERMS: {t['english']} = {t['zh']} [{t['status']}]")
        else:
            lines.append('  TERMS: 未登记')
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks) + '\n'


# ---------------------------------------------------------------- consume

def consume(args):
    pack = json.load(open(args.pack, encoding='utf-8-sig'))
    groups = pack.get('groups') or []
    by_kid = {g['kid']: g for g in groups}
    verdicts = json.load(open(args.verdicts, encoding='utf-8-sig'))
    if not isinstance(verdicts, dict):
        raise SystemExit('error: verdicts JSON 必须是 {kid: {verdict, ...}} 对象')

    problems = []
    pack_kids = set(by_kid)
    got_kids = set(verdicts)
    missing = sorted(pack_kids - got_kids)
    extra = sorted(got_kids - pack_kids)
    if missing:
        problems.append(f'缺裁决: {missing}')
    if extra:
        problems.append(f'多余键（不在 pack）: {extra}')

    plan = []
    splits = []
    for kid in sorted(pack_kids & got_kids):
        g = by_kid[kid]
        v = verdicts[kid] or {}
        kind = v.get('verdict')
        reason = (v.get('reason') or '').strip()
        if kind not in VERDICTS:
            problems.append(f'{kid}: verdict 非法（{kind!r}），枚举 {VERDICTS}')
            continue
        if kind == 'unify':
            target = (v.get('target') or '').strip()
            if not target:
                problems.append(f'{kid}: unify 缺 target')
                continue
            if target not in g['variants']:
                problems.append(f'{kid}: target {target!r} 不在已出现形态集合 {g["variants"]}（禁止第三形态）')
                continue
            for dest, idxs in g['variant_rows'].items():
                if dest == target:
                    continue
                item = {'kid': kid, 'source': g['source'], 'find': dest,
                        'replace': target, 'idx': idxs, 'reason': reason}
                # 子串重叠防御：find 是 target（或任一其他形态）的子串时，
                # apply_fixes --find/--replace 会误伤长形态内部（如「岩湖」命中
                # 「岩湖堡」）。标记出来，主会话终审改整句 new 而非子串替换。
                others = [d for d in g['variants'] if d != dest]
                if any(dest in o for o in others):
                    item['warn'] = 'substring-overlap'
                plan.append(item)
        else:
            if not reason:
                problems.append(f'{kid}: {kind} 缺 reason')
                continue
            splits.append({'kid': kid, 'source': g['source'], 'verdict': kind, 'reason': reason})

    print(f'pack kids: {len(pack_kids)}  verdicts: {len(got_kids)}')
    print(f'  unify: {len(plan)} 条替换指令  split 判定: {len(splits)} 条')
    for item in plan:
        if item.get('warn'):
            print(f"  ⚠ {item['kid']}: find {item['find']!r} 是其他形态的子串，"
                  f'禁用 --find/--replace，终审改整句 new')
    if problems:
        print('\n机械校验失败:')
        for p in problems:
            print(f'  {p}')
        sys.exit(1)

    for s in splits:
        print(f"  [{s['verdict']}] {s['kid']} {s['source'][:50]}: {s['reason'][:80]}")
    if args.plan_out:
        Path(args.plan_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.plan_out).write_text(json.dumps({'plan': plan, 'splits': splits},
                                                  ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'plan -> {args.plan_out}')
    print('机械校验通过。splits 需主会话全量复读，unify 抽审后走 apply_fixes --find/--replace。')


# ---------------------------------------------------------------- main

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='裁决包生成与消费（裁决层并行化底座）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='pool JSON + canonical XML -> 裁决证据包')
    b.add_argument('--pool', required=True, help='noun-consistency-scan pool-<letter>.json')
    b.add_argument('--xml', required=True, help='canonical translated XML（补 EDID/Source 全句）')
    b.add_argument('--terms', default=None, help='MOD terms.json（标注登记状态）')
    b.add_argument('--dialogue-context', default=None,
                   help='dialogue-context JSON（为 INFO/DIAL 行补对话主题证据）')
    b.add_argument('--out', required=True, help='输出目录（pack-NNN.txt/.json + manifest.json）')
    b.add_argument('--cap', type=int, default=25, help='每片候选数（默认 25，子代理单实例上限）')
    b.set_defaults(fn=build)

    c = sub.add_parser('consume', help='verdict JSON 机械校验 + 生成修正计划')
    c.add_argument('--pack', required=True, help='build 产出的 pack-NNN.json')
    c.add_argument('--verdicts', required=True, help='子代理 verdict JSON：{kid: {verdict, target?, reason?}}')
    c.add_argument('--plan-out', default=None, help='修正计划 JSON 输出路径（find/replace/idx 指令集）')
    c.set_defaults(fn=consume)

    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
