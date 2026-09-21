#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整体进度快照：统计 MOD 翻译进度（canonical XML + 批次流水线），可记录到固定日志。

角色：进度统计（只读，--record 时追加写固定日志）。合并两个视角：
  1. XML 视角：canonical 全库翻译数与分类分布（含未开工的类别）。
  2. 流水线视角：批次计划的执行状态（已验收 / 在途 / 已备料 / 未备料；
     在途超时未消费的另计为滞留）。在途是正常中间态（子代理落盘后到交付之间），
     不是欠账信号；唯一开工信号是交付送达。

用法：
  py -3 progress_snapshot.py \
      --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
      [--source-xml mods/<plugin>/<plugin>_english_chinese.xml] \
      --plan .work/<plugin>/context/<plugin>-info-batches.json \
      --batches-dir .work/<plugin>/batches \
      [--log .work/<plugin>/reports/<plugin>-progress-log.json] [--record] [--json] [--note "<备注>"]

输出角色：日志 .work/<plugin>/reports/<plugin>-progress-log.json（{schema_version, plugin, snapshots: [...]}，
追加式；与上一条快照内容一致时跳过）。--record 才写盘，默认只读打印。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from check_batch_coverage import classify as classify_batch
except Exception:  # pragma: no cover - same-dir import; fall back to explicit path
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'check_batch_coverage', os.path.join(HERE, 'check_batch_coverage.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    classify_batch = mod.classify

FAMILIES = [
    ('INFO', 'INFO'),
    ('DIAL', 'DIAL'),
    ('QUST', 'QUST'),
    ('NPC_', 'NPC_'),
    ('BOOK', 'BOOK'),
]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def scan_xml(path):
    """Return {'total': n, 'translated': n, 'families': {name: {...}}}."""
    root = ET.parse(path).getroot()
    total = 0
    translated = 0
    fam_totals = {name: 0 for name, _ in FAMILIES}
    fam_tr = {name: 0 for name, _ in FAMILIES}
    other_total = 0
    other_tr = 0
    for node in root.findall('.//String'):
        total += 1
        src = node.findtext('Source') or ''
        dst = node.findtext('Dest') or ''
        tr = bool(dst) and dst != src
        if tr:
            translated += 1
        rec = node.findtext('REC') or ''
        matched = False
        for name, prefix in FAMILIES:
            if rec.startswith(prefix):
                fam_totals[name] += 1
                if tr:
                    fam_tr[name] += 1
                matched = True
                break
        if not matched:
            other_total += 1
            if tr:
                other_tr += 1
    families = {}
    for name, _ in FAMILIES:
        families[name] = {'total': fam_totals[name], 'translated': fam_tr[name]}
    families['OTHER'] = {'total': other_total, 'translated': other_tr}
    return {'total': total, 'translated': translated,
            'untranslated': total - translated, 'families': families}


def load_plan(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def load_canonical_map(path):
    """Map xml_index -> (Source, Dest) from the canonical XML."""
    root = ET.parse(path).getroot()
    out = {}
    for i, node in enumerate(root.iter('String')):
        out[i] = (node.findtext('Source') or '', node.findtext('Dest') or '')
    return out


def batch_summary(plan, batches_dir, canonical=None, stall_minutes=180, now=None):
    """批次状态摘要。

    state=TRANSLATED（map 已落盘、尚未 consume）在活跃流水线中是**正常在途**：
    子代理落盘后到交付/验收之间必然出现，不是欠账、不是行动信号（唯一开工信号是
    交付送达）。只有 map 长时间（stall_minutes，默认 180）未被消费的才另计为
    「滞留」——那才值得看一眼是否交付丢失。
    """
    import time as _time
    now = now if now is not None else _time.time()
    rows = [classify_batch(b, batches_dir, canonical) for b in plan.get('batches', [])]
    summary = {'total': len(rows), 'verified': 0, 'translated': 0,
               'prepped': 0, 'missing': 0}
    filled_total = 0
    unwritten_lines = 0
    unwritten_batches = []
    stalled_batches = []
    for r in rows:
        state = r['state']
        if state == 'VERIFIED':
            summary['verified'] += 1
            filled_total += max(r['filled'], 0)
        elif state == 'TRANSLATED':
            summary['translated'] += 1
            mt = r.get('map_mtime')
            if mt is not None and (now - mt) > stall_minutes * 60:
                stalled_batches.append({
                    'id': r['id'],
                    'age_minutes': round((now - mt) / 60),
                })
        elif state == 'PREPPED':
            summary['prepped'] += 1
        else:
            summary['missing'] += 1
        # VERIFIED only means the batch has filled translations; it can still be
        # absent from the canonical XML. Surface that separately instead of
        # letting "已验收" stand in for "已写回".
        if (r.get('unwritten') or 0) > 0:
            unwritten_lines += r['unwritten']
            unwritten_batches.append({'id': r['id'], 'unwritten': r['unwritten'],
                                      'sample_idx': r.get('unwritten_idx')})
    summary['filled_lines'] = filled_total
    summary['unwritten_lines'] = unwritten_lines
    summary['unwritten_batches'] = sorted(unwritten_batches, key=lambda x: -x['unwritten'])
    summary['stalled'] = len(stalled_batches)
    summary['stalled_batches'] = sorted(stalled_batches, key=lambda x: -x['age_minutes'])
    summary['stall_minutes'] = stall_minutes
    return summary


def plan_totals(plans):
    """多计划合计：主计划（info） + 补遗计划（gaps）等依次相加。"""
    target_lines = 0
    unique = 0
    for plan in plans:
        tub = plan.get('total_unique_sources')
        if tub is None:
            tub = sum((b.get('unique_src') or 0) for b in plan.get('batches', []))
        unique += tub or 0
        for b in plan.get('batches', []):
            target_lines += len(b.get('idx') or [])
    return {'target_lines': target_lines,
            'unique_sources': unique}


def build_snapshot(args):
    snap = {
        'recorded_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }
    if args.note:
        snap['note'] = args.note
    if args.xml:
        snap['canonical'] = {
            'path': args.xml,
            'sha256': sha256_file(args.xml),
        }
        snap['xml'] = scan_xml(args.xml)
    if args.source_xml and args.xml:
        src_scan = scan_xml(args.source_xml)
        can_info = snap['xml']['families']['INFO']
        src_info = src_scan['families']['INFO']
        snap['info_crosscheck'] = {
            'canonical_translated': can_info['translated'],
            'source_pre_translated': src_info['translated'],
            'campaign_from_canonical': can_info['translated'] - src_info['translated'],
        }
    if args.plan and args.batches_dir:
        plans = [load_plan(p) for p in args.plan]
        merged = {'batches': [b for p in plans for b in p.get('batches', [])]}
        canonical = load_canonical_map(args.xml) if args.xml else None
        snap['batches'] = batch_summary(merged, args.batches_dir, canonical)
        snap['plan'] = plan_totals(plans)
        if 'info_crosscheck' in snap:
            from_pipe = snap['batches']['filled_lines']
            from_canon = snap['info_crosscheck']['campaign_from_canonical']
            snap['info_crosscheck']['campaign_from_pipeline'] = from_pipe
            snap['info_crosscheck']['consistent'] = (from_pipe == from_canon)
    return snap


def load_log(path):
    if not os.path.isfile(path):
        return {'schema_version': 1, 'plugin': None, 'snapshots': []}
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def identical(a, b):
    keys = ('xml', 'batches', 'plan')
    return all(a.get(k) == b.get(k) for k in keys)


def fmt_pct(part, whole):
    return f'{part / whole * 100:.1f}%' if whole else '—'


def print_snapshot(snap, prev):
    lines = []
    lines.append(f"== 进度快照 {snap.get('recorded_at', '')} ==")
    can = snap.get('canonical') or {}
    if can:
        lines.append(f"canonical: {can.get('sha256', '')[:8]}…")
    xml = snap.get('xml')
    if xml:
        lines.append(f"全库: {xml['translated']}/{xml['total']} 已译 "
                     f"({fmt_pct(xml['translated'], xml['total'])})  未译 {xml['untranslated']}")
        for name in ('INFO', 'DIAL', 'QUST', 'NPC_', 'BOOK', 'OTHER'):
            f = xml['families'].get(name) or {}
            if f.get('total') is not None:
                lines.append(f"  {name:5s} {f['translated']}/{f['total']} "
                             f"({fmt_pct(f['translated'], f['total'])})")
    camp = snap.get('info_crosscheck')
    if camp:
        done = camp.get('campaign_from_pipeline', camp.get('campaign_from_canonical'))
        lines.append(f"INFO 战役: 完成 {done} 行")
        if not camp.get('consistent', True):
            lines.append(f"  !! 口径不一致: pipeline={camp.get('campaign_from_pipeline')} "
                         f"canonical={camp.get('campaign_from_canonical')}")
    batches = snap.get('batches')
    plan_t = snap.get('plan') or {}
    if batches:
        lines.append(f"批次: 已验收 {batches['verified']}/{batches['total']} "
                     f"({fmt_pct(batches['verified'], batches['total'])})"
                     f" | 在途 {batches['translated']} | 已备料 {batches['prepped']}"
                     f" | 未备料 {batches['missing']}")
        stalled = batches.get('stalled') or 0
        if stalled:
            ids = ', '.join(f"{b['id']}({b['age_minutes']}min)"
                            for b in batches.get('stalled_batches') or [])
            lines.append(f"  !! 滞留 {stalled} 批（在途超 {batches.get('stall_minutes')} 分钟未消费）: {ids}")
            lines.append("     （滞留仅提示复核可能性；子代理已交卷的仍等交付送达，勿提前处理）")
        unwritten = batches.get('unwritten_lines') or 0
        if unwritten:
            lines.append(f"  !! 已验收但未写回: "
                          f"{len(batches.get('unwritten_batches') or [])} 批 / {unwritten} 行"
                          f"（canonical 仍与 Source 相同）")
        if plan_t.get('target_lines'):
            lines.append(f"目标行: {plan_t['target_lines']}（唯一源句 {plan_t.get('unique_sources')}）")
    if prev:
        dx = (xml or {}).get('translated', 0) - (prev.get('xml') or {}).get('translated', 0)
        db = (batches or {}).get('verified', 0) - (prev.get('batches') or {}).get('verified', 0)
        lines.append(f"较上次: +{dx} 行 / +{db} 批")
    else:
        lines.append('（首次快照）')
    print('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser(description='MOD 翻译整体进度快照')
    ap.add_argument('--xml', help='canonical 译文 XML')
    ap.add_argument('--source-xml', help='原始源 XML（可选，用于战役口径交叉校验）')
    ap.add_argument('--plan', action='append',
                    help='批次计划 JSON；可重复（主计划 + 补遗计划合并统计）')
    ap.add_argument('--batches-dir', help='批次目录（.work/<plugin>/batches）')
    ap.add_argument('--log', help='进度日志路径（默认 .work/<plugin>/reports/<plugin>-progress-log.json）')
    ap.add_argument('--record', action='store_true', help='把快照追加到日志')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--note', default='', help='快照备注（可选）')
    args = ap.parse_args()

    log_path = args.log
    if not log_path and args.plan:
        base = os.path.basename(args.plan[0])
        suffix = '-info-batches.json'
        if base.endswith(suffix):
            plugin = base[:-len(suffix)]
            plan_dir = os.path.dirname(os.path.abspath(args.plan[0]))
            if os.path.basename(plan_dir) == 'context':
                log_path = os.path.join(os.path.dirname(plan_dir), 'reports', plugin + '-progress-log.json')
            else:
                log_path = os.path.join(os.path.dirname(args.plan[0]), plugin + '-progress-log.json')
    if args.record and not log_path:
        print('error: --record 需要 --log 或可推导的 plan 文件名', file=sys.stderr)
        return 2

    snap = build_snapshot(args)
    log = load_log(log_path) if log_path else {'snapshots': []}
    prev = log['snapshots'][-1] if log['snapshots'] else None

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    else:
        print_snapshot(snap, prev)

    if args.record:
        if prev and identical(snap, prev):
            print('（与上一快照一致，跳过记录）')
            return 0
        if log.get('plugin') is None and args.plan:
            base = os.path.basename(args.plan[0])
            if base.endswith('-info-batches.json'):
                log['plugin'] = base[:-len('-info-batches.json')]
        log['snapshots'].append(snap)
        with open(log_path, 'w', encoding='utf-8') as handle:
            json.dump(log, handle, ensure_ascii=False, indent=1)
        print(f'已记录 -> {log_path}（第 {len(log["snapshots"])} 条）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
