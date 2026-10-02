# -*- coding: utf-8 -*-
"""fill_table_rationale.py — 给 gen_form_convergence_tables 生成的表补 rationale。

裁决表要求 rationale 必填（只写「按规则替换」等于把判断留给运气）。
**判据文本由调用方提供**（`--rationale-file`，规则名 -> 正文）：裁决是人的判断，
把它硬编码进脚本等于把脚本变成穿工具外衣的一次性脚本。

用法：
  py -3 fill_table_rationale.py --dir _tmp/data/authtabs \
      --rationale-file _tmp/data/authtabs-rationale.json

rationale-file 形如：
  {"权柄:authority,authoritative->威权": "裁断位取威权，判据是主形须只服务一个锚…",
   "权柄:power->权力": "power 全库已定为义位分立，权位义应作权力…"}

规则名需与表里目标译文能匹配上（按目标形反查），匹配不到即报错中止——
宁可停下也不写出一堆「无判据」的表。落盘后回读断言。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True, help='表目录')
    ap.add_argument('--rationale-file', required=True,
                    help='JSON：{规则名: 判据正文}')
    args = ap.parse_args()

    rat_by_rule = json.loads(Path(args.rationale_file).read_text(encoding='utf-8-sig'))
    if not isinstance(rat_by_rule, dict) or not rat_by_rule:
        raise SystemExit('error: --rationale-file 必须是非空对象 {规则名: 判据}')
    # 规则名自带逗号与冒号，用规则名末段的「目标形」反查对应关系。
    # 这里必须存成 目标形 -> [规则名] 列表：用 dict 存单值时，同名目标形会
    # **静默互相覆盖**，歧义判据形同虚设（本脚本的测试专门盯这一条）。
    targets = {}
    for rule, text in rat_by_rule.items():
        if not str(text).strip():
            raise SystemExit(f'error: 规则 {rule!r} 的判据为空——空判据等于没判据')
        m = rule.rsplit('->', 1)
        if len(m) != 2 or not m[1].strip():
            raise SystemExit(f'error: 规则名须形如 <现形>:<锚>-><目标形>，收到 {rule!r}')
        targets.setdefault(m[1].strip(), []).append(rule)

    n = 0
    for p in sorted(Path(args.dir).glob('table-*.json')):
        spec = json.loads(p.read_text(encoding='utf-8'))
        rat = {}
        for src, tgt in spec['table'].items():
            hit = [rule for tgt_form, rules in targets.items()
                   if tgt_form in tgt for rule in rules]
            if not hit:
                raise SystemExit(
                    f'error: {p.name} 的目标译文里找不到任何已知目标形，'
                    f'无法判定该用哪条判据: {tgt[:60]!r}\n'
                    f'  已知目标形: {sorted(targets)}')
            if len(hit) > 1:
                raise SystemExit(
                    f'error: {p.name} 的目标译文同时命中多条判据 {hit}，'
                    f'目标形有歧义: {tgt[:60]!r}\n'
                    f'  同一目标形被多条规则指向时无法判该用哪条判据——'
                    f'请合并成一条规则，或改用能区分开的目标形。')
            rat[src] = rat_by_rule[hit[0]]
        spec['rationale'] = rat
        p.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding='utf-8')
        back = json.loads(p.read_text(encoding='utf-8'))
        assert set(back['rationale']) == set(back['table']), f'回读不一致: {p}'
        assert all(v.strip() for v in back['rationale'].values()), f'存在空判据: {p}'
        n += len(rat)
    print(f'已补 rationale: {n} 条源句（判据来源 {args.rationale_file}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
