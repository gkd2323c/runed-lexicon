# -*- coding: utf-8 -*-
"""源文忠实度扫描：否定丢失 / 空译未译 / 英文残留 / 时代错位候选。只读。

与 translation-quality-gate 的分工：gate 查契约符合度（term 绑定/全局禁形/
占位符/简繁），本脚本查源文忠实度（语义丢失类机械信号）。禁形不手抄：
--contract 的 terms.forbidden + global_bans 即禁形源（零维护）；时代错位
候选表见 anachronism_candidates.json（候选层，需人审，不定罪）。

用法：
  py fidelity_scan.py --result <translation.json> [--contract <compiled.json>]
  py fidelity_scan.py --xml <translated-xml> [--contract <compiled.json>]
  [--report <json>]

--result：executor schema（translations[]，含 source/translation）；
--xml：canonical 写回产物全扫（Dest vs Source）。
退出码 0 无 FAIL / 1 有 FAIL / 2 用法错误。
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CANDS = json.load(open(os.path.join(HERE, 'anachronism_candidates.json'),
                       encoding='utf-8'))['words']

NEG_PAT = re.compile(
    r'不|没|未|别|莫|否|毫无|无所|无|休想|甭|岂|未曾|无从|无法|未能|别想|别指望|不是|并不|'
    r'没有|毋|勿|弗|非|未始')
DOUBLE_NEG_RE = re.compile(
    r'\bnot\s+(?:un|im|in|ir|dis|non)[a-z]*\b'
    r'|\bnot\s+[a-z]*less\b', re.I)
SRC_NEG = re.compile(r'\b(?:not|no|never|without|n\'t)\b', re.I)
EN_RESIDUE = re.compile(r'[A-Za-z]{4,}')


def _negation_issue(src, dst):
    if not SRC_NEG.search(src or ''):
        return False
    if DOUBLE_NEG_RE.search(src or ''):
        return False
    return not NEG_PAT.search(dst or '')


def _residue(src, dst):
    if not dst or dst == src:
        return False
    stripped = re.sub(r'<[^>]+>|\[[^\]]*\]|%[sd]\b|[A-Za-z0-9_]*=[^\s，。！？…]+', ' ', dst)
    return bool(EN_RESIDUE.search(stripped)
                and not re.fullmatch(r'[^A-Za-z]*[A-Za-z .!…]+', src or ' X '))


def _anchor_hit(src, eng):
    """英文侧整词锚点（与 gate TERM004 同语义）：大小写不敏感，复数/'s 仍命中。
    unconditional 在此仅表示"无需 unit binding"，仍需英文锚点——否则源文 Bosmer 的
    合法"波斯莫"会被 Wood Elf 反向禁令误杀（2026-09-09 实测）。括号备注
    （如 God (real-world)）清洗后取真实锚。"""
    if not eng:
        return False
    eng = re.sub(r'\s*\(.*?\)\s*', '', eng).strip()
    if not eng:
        return False
    return bool(re.search(r'\b%s(?:s|\'s)?\b' % re.escape(eng), src or '', re.I))


def check_units(units):
    """units: [(uid, src, dst)] -> (fails, cands)。"""
    fails, cands = [], []
    bans = BANS
    for uid, src, dst in units:
        hits = []
        for pat, eng, uncond, why, fvar in bans:
            if not re.search(pat, dst or ''):
                continue
            if _anchor_hit(src, eng):
                # 跨条 target 豁免（与 quality-gate TERM002/TERM004 同语义）：
                # 该禁形同时是另一条的合法 target 且其锚点在源文出现时，
                # 命中是行级检查的误报（Dwemer/Dwarven 同行，译文的「矮人」
                # 对应 Dwarven 而非 Dwemer）。
                if _cross_target_ok(src, fvar):
                    continue
                hits.append(why)
        if _negation_issue(src, dst):
            hits.append('疑似丢否定')
        if not dst or dst == src:
            hits.append('空译/未译')
        if _residue(src, dst):
            hits.append('英文残留')
        if hits:
            fails.append({'id': uid, 'why': hits,
                          'src': (src or '')[:80], 'dst': (dst or '')[:60]})
        if dst and dst != src:
            got = [w for w in CANDS if w in dst]
            if got:
                cands.append({'id': uid, 'words': got,
                              'src': (src or '')[:80], 'dst': (dst or '')[:60]})
    return fails, cands


def load_contract(path):
    """从编译契约提取禁形：(forbidden, english锚, unconditional, 说明, 禁形原文)。
    同时收集跨条 target 表（供行级误报豁免）。"""
    c = json.load(open(path, encoding='utf-8'))
    out = []
    terms = c.get('terms', {})
    for tid, t in terms.items():
        for f in t.get('forbidden') or []:
            out.append((re.escape(f), t.get('source'), False,
                        '%s→%s' % (tid, t.get('target')), f))
    for b in c.get('global_bans') or []:
        for f in b.get('forbidden') or []:
            out.append((re.escape(f), b.get('english'), bool(b.get('unconditional')),
                        'GLOBAL %s→%s' % (b.get('english'), b.get('target')), f))
    cross = []
    for t in terms.values():
        tg = (t.get('target') or '').strip()
        if tg:
            cross.append((tg, t.get('source') or ''))
    for b in c.get('global_bans') or []:
        tg = (b.get('target') or '').strip()
        if tg:
            cross.append((tg, b.get('english') or ''))
    CROSS_TARGETS.extend(cross)
    return out


def _cross_target_ok(src, variant):
    """variant 是另一条的合法 target 且该条锚点在源文出现 -> 豁免。"""
    if not variant:
        return False
    for tg, anchor in CROSS_TARGETS:
        if tg != variant:
            continue
        if anchor and not _anchor_hit(src, anchor):
            continue
        return True
    return False


BANS = []
CROSS_TARGETS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--result', default=None)
    ap.add_argument('--xml', default=None)
    ap.add_argument('--contract', default=None)
    ap.add_argument('--report', default=None)
    a = ap.parse_args()
    if bool(a.result) == bool(a.xml):
        print('--result 与 --xml 二选一', file=sys.stderr)
        return 2
    global BANS
    if a.contract:
        BANS = load_contract(a.contract)
    if a.result:
        d = json.load(open(a.result, encoding='utf-8'))
        units = [(u.get('translation_unit_id'), u.get('source'),
                  u.get('translation') or u.get('original_dest') or '')
                 for u in d.get('translations', [])]
    else:
        strs = ET.parse(a.xml).getroot().findall('.//String')
        units = [('xml-index:%d' % i, s.findtext('Source') or '', s.findtext('Dest') or '')
                 for i, s in enumerate(strs)]
    fails, cands = check_units(units)
    print('units=%d fails=%d anachronism_candidates=%d' % (len(units), len(fails), len(cands)))
    for f in fails[:20]:
        print('  FAIL', f['id'], f['why'], '|', f['src'][:56], '=>', f['dst'][:44])
    for c_ in cands[:20]:
        print('  ~', c_['id'], c_['words'], '|', c_['src'][:56], '=>', c_['dst'][:44])
    if a.report:
        json.dump({'fails': fails, 'anachronism_candidates': cands},
                  open(a.report, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('report -> %s' % a.report)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
