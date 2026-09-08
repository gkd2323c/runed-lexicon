# -*- coding: utf-8 -*-
"""按任务线生成 INFO 翻译批次计划（去重口径：唯一未译源句数）。"""
import json
import re
import xml.etree.ElementTree as ET

split = json.load(open('.work/Druadach-info-split.json', encoding='utf-8'))
t = ET.parse('mods/Druadach.esm/Druadach_english_chinese.xml')
strs = t.getroot().findall('.//String')
CAP = 45


def topic_unique_untranslated(tp):
    srcs = set()
    for i in tp['infos']:
        for idx in i['idx']:
            s = strs[idx].findtext('Source') or ''
            d = strs[idx].findtext('Dest') or ''
            if s == d and s:
                srcs.add(s)
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
    'note': 'srcs 为该批唯一未译源句（跨批已去重）；rows 见 split 文件',
    'batch_count': len(batches),
    'total_unique_sources': sum(len(b['srcs']) for b in batches),
    'batches': [{'id': 'INFO-%03d' % (n + 1), 'line': b['line'],
                 'dials': [t['dial'] for t in b['topics']],
                 'unique_src': len(b['srcs'])}
                for n, b in enumerate(batches)],
}
json.dump(plan, open('.work/Druadach-info-batches.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('batches:', plan['batch_count'], 'unique srcs:', plan['total_unique_sources'])
size = [b['unique_src'] for b in plan['batches']]
print('min/med/max:', min(size), sorted(size)[len(size)//2], max(size))
