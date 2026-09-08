# -*- coding: utf-8 -*-
"""按任务线生成 INFO 翻译批次计划（去重口径：唯一未译源句数）。

输入: .work/<plugin>-info-split.json + 源 XML
产出: .work/<plugin>-info-batches.json
      每批 {id, line, dials, unique_src, srcs(唯一未译源句), idx(待写回 NAM1 行)}
      srcs 决定翻译工作量；idx 决定最终 patch 覆盖面（重复行全写）。
"""
import json
import re
import sys
import xml.etree.ElementTree as ET

plugin = sys.argv[1] if len(sys.argv) > 1 else 'Druadach'
split = json.load(open('.work/%s-info-split.json' % plugin, encoding='utf-8'))
t = ET.parse('mods/%s.esm/%s_english_chinese.xml' % (plugin, plugin))
strs = t.getroot().findall('.//String')
CAP = 45


def untranslated(i):
    s = strs[i].findtext('Source') or ''
    d = strs[i].findtext('Dest') or ''
    return s == d and bool(s)


def topic_unique_untranslated(tp):
    srcs = set()
    for i in tp['infos']:
        for idx in i['idx']:
            if untranslated(idx):
                srcs.add(strs[idx].findtext('Source') or '')
    return srcs


# 全局长句去重：同一句在多个批次出现时只翻一次，后续批次从剩余容量中扣除。
# 单主题超 CAP 时，按 info 均分为子主题（同一 DIAL 内保持连续）。
batches = []
seen_global = set()


def topic_units(tp):
    srcs = topic_unique_untranslated(tp)
    if len(srcs) <= CAP:
        yield tp
        return
    k = (len(tp['infos']) + CAP - 1) // CAP
    per = (len(tp['infos']) + k - 1) // k
    for n in range(0, len(tp['infos']), per):
        yield {**tp, 'infos': tp['infos'][n:n + per]}


for line in sorted(split):
    cur, cur_srcs = [], set()
    for tp0 in split[line]:
        for tp in topic_units(tp0):
            srcs = topic_unique_untranslated(tp) - seen_global
            if cur and len(cur_srcs) + len(srcs) > CAP:
                batches.append({'line': line, 'topics': cur, 'srcs': sorted(cur_srcs)})
                seen_global |= cur_srcs
                cur, cur_srcs = [], set()
            cur.append(tp)
            cur_srcs |= srcs
    if cur:
        batches.append({'line': line, 'topics': cur, 'srcs': sorted(cur_srcs)})
        seen_global |= cur_srcs

plan = {
    'cap': CAP,
    'note': 'srcs 为该批唯一未译源句（跨批已去重）；idx 为待写回的全部 NAM1 行（含重复）',
    'batch_count': len(batches),
    'total_unique_sources': sum(len(b['srcs']) for b in batches),
    'batches': [{'id': 'INFO-%03d' % (n + 1), 'line': b['line'],
                 'dials': [t['dial'] for t in b['topics']],
                 'unique_src': len(b['srcs']),
                 'srcs': b['srcs'],
                 'idx': sorted({i for t in b['topics'] for ti in t['infos'] for i in ti['idx'] if untranslated(i)})}
                for n, b in enumerate(batches)],
}
json.dump(plan, open('.work/%s-info-batches.json' % plugin, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('batches:', plan['batch_count'], 'unique srcs:', plan['total_unique_sources'])
size = [b['unique_src'] for b in plan['batches']]
idxs = sum(len(b['idx']) for b in plan['batches'])
print('min/med/max src:', min(size), sorted(size)[len(size)//2], max(size), 'idx 总行:', idxs)
