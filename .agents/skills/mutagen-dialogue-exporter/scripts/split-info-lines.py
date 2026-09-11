# -*- coding: utf-8 -*-
"""Mutagen DIAL→INFO 结构 → 按任务线拆 INFO 批次（v2 简洁版）。

输入: .work/Druadach/context/Druadach-mutagen-dialogue.json + 源 XML
产出: .work/Druadach/context/Druadach-info-split.json
      {lines: {questEdid: {topics: [{dial, edid, topic, subtype, infos: [{idx, prompt_idx, speaker, responses}]}], rows, untranslated}},
       unlinked: [未链接且未译的 INFO NAM1 xml_index], batch_plan: [...] }
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

plugin = sys.argv[1] if len(sys.argv) > 1 else 'Druadach'
moddir = sys.argv[2] if len(sys.argv) > 2 else 'mods/%s.esm' % plugin
doc = json.load(open('.work/%s/context/%s-mutagen-dialogue.json' % (plugin, plugin), encoding='utf-8'))
t = ET.parse('%s/%s_english_chinese.xml' % (moddir, plugin))
strs = t.getroot().findall('.//String')

# local FormKey(hex6) -> {subrecord -> [xml_index]}
fk_pat = re.compile(r'\[(\d\d)([0-9a-fA-F]{6})\]')
by_fk = defaultdict(lambda: defaultdict(list))
for i, s in enumerate(strs):
    rec = s.findtext('REC') or ''
    if not rec.startswith('INFO:'):
        continue
    m = fk_pat.fullmatch(s.findtext('EDID') or '')
    if not m:
        continue
    by_fk[m.group(2).lower()][rec[5:]].append(i)


def untranslated(i):
    return (strs[i].findtext('Source') or '') == (strs[i].findtext('Dest') or '')


linked = set()
lines = defaultdict(list)
for tl in doc['topics']:
    line = tl['questEdid'] or 'NOQUEST'
    tinfos = []
    for info in tl['infos']:
        fk = info['formKey']
        if not fk:
            continue
        local = fk.split(':')[0][-6:].lower()
        rows = by_fk.get(local)
        if not rows:
            continue
        nam1s = sorted(rows.get('NAM1', []))
        if not nam1s:
            continue
        rnam = rows.get('RNAM', [None])[0]
        tinfos.append({
            'info_edid': info['edid'],
            'speaker': info['speakerName'] or info.get('speakerFromCondition') or info['speaker'],
            'prompt': info['prompt'],
            'prompt_idx': rnam,
            'responses': info['responses'],
            'idx': nam1s,
        })
        linked.update(nam1s)
        if rnam is not None:
            linked.add(rnam)
    if tinfos:
        lines[line].append({'dial': tl['edid'], 'topic': tl['topic'],
                            'subtype': tl['subtype'], 'infos': tinfos})

unlinked = sorted(
    i for rows in by_fk.values() for i in rows.get('NAM1', [])
    if i not in linked and untranslated(i))

stats = {}
out_lines = {}
for line, topics in lines.items():
    rows = sum(len(ti['idx']) for tp in topics for ti in tp['infos'])
    ut = sum(1 for tp in topics for ti in tp['infos'] for i in ti['idx'] if untranslated(i))
    stats[line] = (len(topics), rows, ut)
    out_lines[line] = topics

json.dump(out_lines, open('.work/%s/context/%s-info-split.json' % (plugin, plugin), 'w', encoding='utf-8'),
          ensure_ascii=False)
json.dump(unlinked, open('.work/%s/context/%s-info-unlinked.json' % (plugin, plugin), 'w', encoding='utf-8'),
          ensure_ascii=False)

print('任务线 %d 条' % len(stats))
for line, (ntp, rows, ut) in sorted(stats.items(), key=lambda x: -x[1][2]):
    print('%-28s topics %4d  rows %5d  未译 %5d' % (line, ntp, rows, ut))
print('unlinked 未译:', len(unlinked))
tot = sum(1 for rows in by_fk.values() for i in rows.get('NAM1', []) if untranslated(i))
print('全 XML 未译 INFO NAM1 行数:', tot)
