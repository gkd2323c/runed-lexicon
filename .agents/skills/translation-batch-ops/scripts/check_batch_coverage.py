#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次覆盖率核查：对批次计划与批次目录做全量状态扫描，输出缺口清单。

状态定义（逐批次）：
- VERIFIED  ：translation.json 已填充（≥1 条 TRANSLATED 译文）——已验收（或已写回）；
               纯 KEEP 批没有 TRANSLATED 条目，改由 consume 落盘痕迹判定（见
               consume_evidence）
- TRANSLATED：map.json 存在但 translation.json 未填充——子代理已交卷，待主会话消费
- PREPPED   ：context.json / translation.json 存在但无产出——已备料待翻译
- MISSING   ：批次目录不存在或无任何工作文件——未备料

用途：声明批次范围完成前核对（防漏批）；每轮收口时定位下一批候选。

用法：
  py -3 check_batch_coverage.py --plan .work/<plugin>/context/<plugin>-info-batches.json \
      --batches-dir .work/<plugin>/batches [--json]
"""
import argparse
import json
import os
import sys
from collections import Counter


def consume_evidence(d, entries):
    """批次是否已被 consume（消费链已落盘）的证据。返回 (consumed, resolved, map_filled)。

    事故锚定（TheKalpicAnomaly_GLENMORIL 2026-10-02，NI-TES4-001）：该批是纯 KEEP
    批（TES4:CNAM "DEFAULT"），Dest 变更恒为 0、`Dest == Source` 本来就成立，canonical
    里没有任何痕迹能证明它被消费过；而 VERIFIED 旧口径只认“≥1 条 TRANSLATED”，
    纯 KEEP 批永远拿不到，状态机于是停在 TRANSLATED，快照每轮报同一条假滞留告警
    （实测 253→270 分钟，而 round_pipeline 早已 PIPELINE PASS、0 Dest change）。纯 KEEP
    批的已消费性只能看 consume 自己的落盘痕迹。

    - resolved  ：translation.json 中「译文非空且 status ∈ {TRANSLATED, KEEP}」的条目
      数。**判据本身**——fill_translations 只由 consume / round_pipeline / close_round
      触发，能落满即证明交付确实被消费过。它不会被同源预填骗过：inherit_prefill 只写
      map.json，未派单批的 translation.json 始终是 PENDING 骨架。
    - map_filled：`map.filled.json`（consume 第 2 步写的归一化 map）是否存在且非空。
      **只作旁证，单独不成立**：它写在五步链的第 2 步、fill 之前就落盘，链在中途挂掉时
      它照样存在；空对象 `{}` 更只能证明「交付是空的」，fill 因此什么都没落盘。把
      存在性当充分条件，会把「consume 崩在步 2/3」和「子代理交了空 map」两类真洞永久
      藏成已验收——比原缺陷更危险。
    - status=REVIEW 刻意**不**计入 resolved：REVIEW 是「值已定稿、状态待转正」的中间态
      （progress_snapshot §4 的口径交叉校验会因它报不一致），转正前不算已消费。
    - entries 为空（含 translation.json 缺失/解析失败）时一律不算消费，保守侧倒。
    """
    resolved = 0
    for t in entries:
        if (t.get('translation') or '').strip() and t.get('status') in ('TRANSLATED', 'KEEP'):
            resolved += 1
    map_filled = False
    mfp = os.path.join(d, 'map.filled.json')
    if os.path.isfile(mfp):
        try:
            with open(mfp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 空对象不算：它只说明归一化后没有任何条目可消费。
            map_filled = isinstance(data, dict) and bool(data)
        except (OSError, json.JSONDecodeError):
            map_filled = False
    consumed = bool(entries) and resolved == len(entries)
    return consumed, resolved, map_filled


def classify(batch, batches_dir, canonical=None):
    bid = batch['id']
    d = os.path.join(batches_dir, bid)
    has_dir = os.path.isdir(d)
    has_tr = os.path.isfile(os.path.join(d, 'translation.json'))
    has_map = os.path.isfile(os.path.join(d, 'map.json'))
    has_ctx = os.path.isfile(os.path.join(d, 'context.json'))
    has_idx = os.path.isfile(os.path.join(d, 'index.txt'))
    has_digest = os.path.isfile(os.path.join(d, 'term-digest.md'))
    map_path = os.path.join(d, 'map.json')
    map_mtime = os.path.getmtime(map_path) if has_map else None
    # PREPPED 也要有时间戳：备好了却长期没人派单是真实事故（已备料 6 批里两批
    # 静默躺了整轮，快照只打印计数不打印成员，主会话无从发现漏派）。
    prep_mtime = None
    if has_tr:
        prep_mtime = os.path.getmtime(os.path.join(d, 'translation.json'))
    total = len(batch.get('idx') or [])
    filled = 0
    entries = []
    if has_tr:
        try:
            with open(os.path.join(d, 'translation.json'), 'r', encoding='utf-8') as f:
                res = json.load(f)
            entries = res.get('translations', [])
            filled = sum(
                1 for t in entries
                if t.get('translation') and t.get('status') == 'TRANSLATED'
            )
        except Exception:
            filled = -1
    consumed, resolved, map_filled = consume_evidence(d, entries)
    # "filled" means the batch was translated and verified; it does NOT mean the
    # rows reached the canonical XML. Cross-check when a canonical map is given,
    # otherwise a batch that passed verification but was never written back looks
    # finished forever.
    # KEEP rows legitimately stay equal to Source and are not counted.
    unwritten = None
    unwritten_idx = []
    if canonical is not None and entries:
        unwritten = 0
        for t in entries:
            if not t.get('translation') or t.get('status') != 'TRANSLATED':
                continue
            i = t.get('xml_index')
            pair = canonical.get(i)
            if pair is not None and pair[0] == pair[1]:
                unwritten += 1
                if len(unwritten_idx) < 12:
                    unwritten_idx.append(i)
    if filled > 0 or consumed:
        # filled>0：至少一条 TRANSLATED 译文已落 translation.json（旧口径，行为不变）。
        # consumed 且 filled==0：纯 KEEP 批——canonical 天然无痕，只能靠 consume 落盘
        # 痕迹判已消费（见 consume_evidence）。
        state = 'VERIFIED'
    elif has_map:
        state = 'TRANSLATED'
    elif has_idx and has_ctx and has_digest:
        state = 'PREPPED'
    elif has_tr or has_ctx or has_idx or has_digest:
        # 事故锚定：旧判定只看 context.json 就算已备料，gaps 六批 term-digest
        # 全缺却显示已备料，派单前才发现。三件套齐才可派单；部分齐单列 PARTIAL，
        # 不再冒充已备料，也不兼作 MISSING（避免把补 digest 误报成从头备料）。
        state = 'PARTIAL'
    else:
        state = 'MISSING'
    return {
        'id': bid,
        'line': batch.get('line'),
        'state': state,
        'filled': filled,
        'total': total,
        'unwritten': unwritten,
        'unwritten_idx': unwritten_idx,
        'map_mtime': map_mtime,
        'prep_mtime': prep_mtime,
        # 消费痕迹（consume 落盘证据）：纯 KEEP 批据此判 VERIFIED，见 consume_evidence
        'consumed': consumed,
        'resolved': resolved,
        'map_filled': map_filled,
    }


def load_canonical_map(path):
    """Map xml_index -> (Source, Dest) from the canonical XML."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    out = {}
    for i, node in enumerate(root.iter('String')):
        src = node.findtext('Source') or ''
        dst = node.findtext('Dest') or ''
        out[i] = (src, dst)
    return out


def main():
    ap = argparse.ArgumentParser(description='批次覆盖率核查')
    ap.add_argument('--plan', required=True, help='批次计划 JSON（plan-info-batches.py 产物）')
    ap.add_argument('--batches-dir', required=True, help='批次目录（.work/<plugin>/batches）')
    ap.add_argument('--xml', help='canonical 译文 XML；给出后额外核算“已验收但未写回”的行')
    ap.add_argument('--json', action='store_true', help='输出 JSON 而非文本')
    args = ap.parse_args()

    with open(args.plan, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    canonical = load_canonical_map(args.xml) if args.xml else None
    rows = [classify(b, args.batches_dir, canonical) for b in plan['batches']]
    cnt = Counter(r['state'] for r in rows)
    unwritten_rows = [r for r in rows if (r['unwritten'] or 0) > 0]
    out = {
        'total_batches': len(rows),
        'summary': {k: cnt.get(k, 0) for k in ('VERIFIED', 'TRANSLATED', 'PREPPED', 'MISSING')},
        'non_verified': [r for r in rows if r['state'] != 'VERIFIED'],
        'unwritten_batches': len(unwritten_rows),
        'unwritten_rows': sum(r['unwritten'] or 0 for r in unwritten_rows),
        'unwritten': [
            {'id': r['id'], 'unwritten': r['unwritten'], 'sample_idx': r['unwritten_idx']}
            for r in sorted(unwritten_rows, key=lambda x: -(x['unwritten'] or 0))
        ],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        s = out['summary']
        print(f"batches={out['total_batches']} VERIFIED={s['VERIFIED']} "
              f"TRANSLATED={s['TRANSLATED']} PREPPED={s['PREPPED']} MISSING={s['MISSING']}")
        if canonical is not None:
            print(f"unwritten: {out['unwritten_batches']} 批 / {out['unwritten_rows']} 行"
                  f"（已填充译文但 canonical 仍与 Source 相同）")
        print()
        for r in out['unwritten']:
            print(f"!! {r['id']} | 未写回 {r['unwritten']} 行 | 例 {r['sample_idx']}")
        for r in out['non_verified']:
            print(f"{r['id']} | {r['state']:10s} | filled={r['filled']}/{r['total']} | {r['line']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
