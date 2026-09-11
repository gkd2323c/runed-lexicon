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
from functools import lru_cache

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


# ---- 句末标点一致性（源文/译文终结标点类别对齐）----
# 不作为 FAIL：句末去标点可能是项目有意约定（游戏 UI 目标句惯用无句号），
# 也可能是漏译标点，两者只能由主会话按 REC 与已验收先例裁决。本检查只报
# “源文终结标点与译文终结标点类别不一致”，不定罪，供全库分布统计与逐批复核。
_TRAILING_WRAP = re.compile(r'[\s"\'\]\)）】」』”’]*$')


def qust_punct_fails(punct):
    """QUST:NNAM 目标句缺句末标点的行（裁决 A：统一保留句号）→ 计入机械 FAIL。
    其他 REC 的标点差异只报不定罪。"""
    return [p for p in punct
            if p.get('rec') == 'QUST:NNAM' and p['dir'] == 'lost']


def _terminal_category(text):
    """返回句末终结标点类别：period / exclam / question / ellipsis / none。
    先剥离尾随引号/括号/空白，再看前一字符；全角半角同归一类。"""
    t = _TRAILING_WRAP.sub('', (text or '').rstrip())
    if not t:
        return 'none'
    ch = t[-1]
    if ch == '。':
        return 'period'
    if ch in '！!':
        return 'exclam'
    if ch in '？?':
        return 'question'
    if ch == '…':
        return 'ellipsis'
    if ch == '.':
        return 'ellipsis' if t.endswith('...') else 'period'
    return 'none'


def check_punctuation(units):
    """units: [(uid, src, dst[, rec])] -> list of terminal-punctuation mismatches.
    只报源文有终结标点而译文无（或反之）的行；两侧都无 / 两侧类别相同不报。
    KEEP 行（dst == src）不报（保留原文天然一致）。"""
    out = []
    for unit in units:
        uid, src, dst = unit[0], unit[1], unit[2]
        rec = unit[3] if len(unit) > 3 else ''
        if not dst or dst == src:
            continue
        sc = _terminal_category(src)
        dc = _terminal_category(dst)
        if sc == dc:
            continue
        # 源文无标点、译文加了标点（或反向）都报；类别不同（句号 vs 问号）也报
        out.append({
            'id': uid,
            'rec': rec or '',
            'src_terminal': sc,
            'dst_terminal': dc,
            'dir': 'lost' if sc != 'none' and dc == 'none' else (
                   'added' if sc == 'none' and dc != 'none' else 'changed'),
            'src': (src or '')[:80],
            'dst': (dst or '')[:60],
        })
    return out


@lru_cache(maxsize=8192)
def _anchor_re(eng):
    """预编译锚点正则。此前每个 (unit × ban) 命中都现场编译，
    实测成为主热点（1500 行样本 160 万次 re.compile）。"""
    return re.compile(r'\b%s(?:s|\'s)?\b' % re.escape(eng), re.I)


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
    return bool(_anchor_re(eng).search(src or ''))


def check_units(units):
    """units: [(uid, src, dst[, rec])] -> (fails, cands)。"""
    fails, cands = [], []
    bans = BANS
    for unit in units:
        uid, src, dst = unit[0], unit[1], unit[2]
        hits = []
        for pat, eng, uncond, why, fvar in bans:
            if not pat.search(dst or ''):
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
    """从编译契约提取禁形：(编译后 forbidden 正则, english锚, unconditional, 说明, 禁形原文)。
    同时收集跨条 target 表（供行级误报豁免）。

    性能：pattern 在此预编译。旧实现存 re.escape 字符串，check_units 中每次
    re.search(pat, dst) 都走 re._compile，866 禁形 × 14968 行 ≈ 1300 万次编译，
    全库实测 293s（30s 缺陷线 10 倍）。"""
    c = json.load(open(path, encoding='utf-8'))
    out = []
    terms = c.get('terms', {})
    for tid, t in terms.items():
        target = (t.get('target') or '').strip()
        for f in t.get('forbidden') or []:
            # 禁形若是**其自身合法 target 的子串**，纯子串匹配必然误报：
            # 魂石⊂灵魂石、琼⊂琼恩、大战⊂浩大战争、雨手⊂雨手月。此类禁形在当前
            # 匹配机制下不可执行（中文无词边界），全部跳过；否则每一条合法
            # target 的译文都会被报成 FAIL（实测 126/189 条为自包含误报）。
            if target and f and f in target:
                continue
            out.append((re.compile(re.escape(f)), t.get('source'), False,
                        '%s→%s' % (tid, target), f))
    for b in c.get('global_bans') or []:
        target = (b.get('target') or '').strip()
        for f in b.get('forbidden') or []:
            if target and f and f in target:
                continue
            out.append((re.compile(re.escape(f)), b.get('english'), bool(b.get('unconditional')),
                        'GLOBAL %s→%s' % (b.get('english'), target), f))
    cross = []
    for t in terms.values():
        tg = (t.get('target') or '').strip()
        if tg:
            cross.append((tg, t.get('source') or ''))
    for b in c.get('global_bans') or []:
        tg = (b.get('target') or '').strip()
        if tg:
            cross.append((tg, b.get('english') or ''))
    del CROSS_TARGETS[:]
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
                  u.get('translation') or u.get('original_dest') or '',
                  u.get('rec') or '')
                 for u in d.get('translations', [])]
    else:
        strs = ET.parse(a.xml).getroot().findall('.//String')
        units = [('xml-index:%d' % i, s.findtext('Source') or '', s.findtext('Dest') or '',
                  s.findtext('REC') or '')
                 for i, s in enumerate(strs)]
    fails, cands = check_units(units)
    punct = check_punctuation(units)
    # QUST:NNAM 目标句：源文主体带句末标点而译文丢失 → 机械 FAIL（裁决 A：
    # 统一保留句号）。其他 REC 的标点差异仍只报不定罪（对话台词自然差异）。
    punct_fail = qust_punct_fails(punct)
    for p in punct_fail:
        fails.append({'id': p['id'], 'why': ['QUST 目标句缺句末标点'],
                      'src': p['src'], 'dst': p['dst']})
    lost = sum(1 for p in punct if p['dir'] == 'lost')
    added = sum(1 for p in punct if p['dir'] == 'added')
    changed = sum(1 for p in punct if p['dir'] == 'changed')
    print('units=%d fails=%d anachronism_candidates=%d punctuation_mismatch=%d (lost=%d added=%d changed=%d; qust_lost_fail=%d)'
          % (len(units), len(fails), len(cands), len(punct), lost, added, changed, len(punct_fail)))
    for f in fails[:20]:
        print('  FAIL', f['id'], f['why'], '|', f['src'][:56], '=>', f['dst'][:44])
    for c_ in cands[:20]:
        print('  ~', c_['id'], c_['words'], '|', c_['src'][:56], '=>', c_['dst'][:44])
    for p in punct[:20]:
        print('  PUNCT', p['id'], p['dir'], 'src=%s dst=%s' % (p['src_terminal'], p['dst_terminal']),
              '|', p['src'][:48], '=>', p['dst'][:40])
    if a.report:
        json.dump({'fails': fails, 'anachronism_candidates': cands,
                   'punctuation_mismatches': punct},
                  open(a.report, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('report -> %s' % a.report)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
