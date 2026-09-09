# -*- coding: utf-8 -*-
"""为 executor result 建议 waived_tokens 背书：源文含动作提示括号而译文已中文化 的条目。

规则（R16 联动 auto-waivers 沉淀）：
- 方括号/圆括号整段视为可中文化的动作提示；
- 尖括号仅当内容匹配动作标签白名单才可豁免；含 = 或 % 的运行时占位符
  （如 <Alias=Player>、%s）绝不豁免，必须原样保留；
- 译文已原样保留的 token 不在建议之列（validator multiset 直接通过，无需背书）。

用法：
  py suggest_waived_tokens.py --result <translation.json> [--apply] [--report <json>]
默认仅提示不修改；--apply 把建议合并进各条目的 waived_tokens（去重，
只加真实存在于 source 的子串，validator 可验证）。退出码 0 完成 / 2 用法错误。
"""
import argparse
import json
import re
import sys

SQUARE = re.compile(r'(\[[^\]\n]{1,60}\])')
ROUND = re.compile(r'(\([^\)\n]{1,60}\))')
ANGLE = re.compile(r'(<[^>\n]{1,60}>)')
ACTION_WORDS = {'Lie', 'Persuade', 'Intimidate', 'Attack', 'Show', 'Give',
                'Release', 'Untie', 'Refuse', 'Unbind', 'Orc', 'Bribe', 'Skeever'}


def _waivable_angle(tok):
    inner = tok[1:-1].strip()
    if not inner:
        return False
    if '=' in inner or '%' in inner or '<' in inner:
        return False
    head = inner.split()[0].rstrip('s') if inner.split() else ''
    return head in ACTION_WORDS or inner in ACTION_WORDS


def suggest(src, tr, have):
    tokens = (SQUARE.findall(src or '') + ROUND.findall(src or '')
              + [t for t in ANGLE.findall(src or '') if _waivable_angle(t)])
    have = set(have or [])
    return [t for t in dict.fromkeys(tokens) if t not in tr and t not in have]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--result', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--report', default=None)
    a = ap.parse_args()

    d = json.load(open(a.result, encoding='utf-8'))
    units = d.get('translations', [])
    rows = []
    for u in units:
        uid = u.get('translation_unit_id')
        new = suggest(u.get('source'), u.get('translation') or '', u.get('waived_tokens'))
        if new:
            rows.append({'id': uid, 'suggest': new})
            if a.apply:
                u['waived_tokens'] = list((u.get('waived_tokens') or []) + new)
    print('%s units=%d need_waived=%d%s'
          % (a.result, len(units), len(rows), ' applied' if a.apply else ''))
    for r in rows[:20]:
        print('  ', r['id'], r['suggest'])
    if a.report:
        json.dump({'result': a.result, 'units': len(units), 'rows': rows},
                  open(a.report, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('report -> %s' % a.report)
    if a.apply:
        json.dump(d, open(a.result, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
