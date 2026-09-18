# -*- coding: utf-8 -*-
"""按任务线生成 INFO 翻译批次计划（去重口径：唯一未译源句数）。

输入: .work/<plugin>/context/<plugin>-info-split.json + 源 XML
产出: .work/<plugin>/context/<plugin>-info-batches.json
      每批 {id, line, dials, unique_src, srcs(唯一未译源句), idx(待写回 NAM1 行)}
      srcs 决定翻译工作量；idx 决定最终 patch 覆盖面（重复行全写）。
"""
import json
import re
import sys
import xml.etree.ElementTree as ET

plugin = sys.argv[1] if len(sys.argv) > 1 else 'Druadach'
moddir = sys.argv[2] if len(sys.argv) > 2 else 'mods/%s.esm' % plugin
split = json.load(open('.work/%s/context/%s-info-split.json' % (plugin, plugin), encoding='utf-8'))
t = ET.parse('%s/%s_english_chinese.xml' % (moddir, plugin))
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
    """把超容量 topic 按【唯一未译源句数】均分为子主题。

    切分单位必须是源句而不是 INFO 条数：INFO 与 NAM1 行是 1:N 关系（同一句台词被
    引擎按条件复制成多条记录，实测 mean 4.11、max 23），按 INFO 条数切分会让
    每片仍携带远超 CAP 的源句（TheKalpicAnomaly 事故：16 条 INFO 带 165 个源句）。
    子主题内 INFO 保持原有顺序，不跨 DIAL 重排。
    """
    srcs = topic_unique_untranslated(tp)
    if len(srcs) <= CAP:
        yield tp
        return
    # 按 INFO 逐条累加其新增源句数，装满 CAP 就切一刀。
    # cur_seen 只用于当前片内去重（同句在同一片只计一次），切片后重置，
    # 否则第二片的新增数恒为 0、再也不会切分。跨批全局去重由调用方的
    # seen_global 负责，不在这里处理。
    chunks, cur, cur_seen = [], [], set()
    for info in tp['infos']:
        add = {strs[i].findtext('Source') or '' for i in info['idx'] if untranslated(i)} - cur_seen
        if cur and len(cur_seen) + len(add) > CAP:
            chunks.append(cur)
            cur, cur_seen = [], set()
            add = {strs[i].findtext('Source') or '' for i in info['idx'] if untranslated(i)}
        cur.append(info)
        cur_seen |= add
    if cur:
        chunks.append(cur)
    for c in chunks:
        yield {**tp, 'infos': c}


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
json.dump(plan, open('.work/%s/context/%s-info-batches.json' % (plugin, plugin), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('batches:', plan['batch_count'], 'unique srcs:', plan['total_unique_sources'])
size = [b['unique_src'] for b in plan['batches']]
idxs = sum(len(b['idx']) for b in plan['batches'])
print('min/med/max src:', min(size), sorted(size)[len(size)//2], max(size), 'idx 总行:', idxs)
