#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semgate_baseline.py — TypeSafe 语义门基线：固化判定轨迹 + 漂移比对。

与 semantic_gate.py 的分工：
  semantic_gate.py  判单批译文的语义质量（生产门，判「对不对」）
  本脚本            把一次判定的完整轨迹固化成基线，之后重跑比对位移（哨兵，判「变没变」）

为什么需要它：语义门的判定来自概率模型，判定行为可能随上游变化。机械 gate 有
corpus 回归兜底（gate/selftest 不可漂移），语义层用本脚本做定期哨兵：不判断译文
对错，只判断「同一个输入，判定轨迹是否和上次一致」。

配套关系：`MODEL = 'jev-latest'` 是指针，上游改进自动生效；哨兵负责让这种变化
可见（含来源归因），两者配对使用。

比对只报三类事件：
  FLIP   verdict 级别跨级（none / WARN / FAIL 之间变化）——直接改变流水线行为
  DRIFT  概率位移超容差但未跨级——先行指标，可能无害但值得记录
  OK     位移在容差内

用法：
  # 建库：从一次已跑的 semgate report + 对应 translation.json 固化基线
  py semgate_baseline.py --build \\
      --report <batch-semgate-report.json> \\
      --translations <batch/translation.json> \\
      --out <baseline.json> \\
      [--note "说明"]

  # 比对：重跑每条并比对（需 TYPESAFE_API_KEY 环境变量）
  py semgate_baseline.py --check \\
      --baseline <baseline.json> \\
      --contract <compiled.json> \\
      [--tolerance 0.15] [--repeat 1] [--out <diff-report.json>]

  # 稳定性探测：每条重复跑 N 次，记录 spread（判断模型对该判定是否确定）
  py semgate_baseline.py --check --baseline <b.json> --contract <c.json> --repeat 5

退出码：0 全 OK，1 存在 FLIP/DRIFT，2 用法错误，3 无 API key。
"""
import argparse
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import semantic_gate as sg  # noqa: E402  （复用其 judge_unit / load_contract / match_terms）

LEVEL_ORDER = {'none': 0, 'WARN': 1, 'FAIL': 2}


def level_of(verdicts):
    """把 verdicts 列表压成级别：FAIL > WARN > none。"""
    lv = 0
    for v in verdicts:
        lv = max(lv, LEVEL_ORDER[v[0]])
    return {0: 'none', 1: 'WARN', 2: 'FAIL'}[lv]


def vector_of(rec):
    """判定轨迹向量（参与位移计算的全部数值）。"""
    return {
        'term': rec.get('term'),
        'semantic': rec.get('semantic'),
        'p_pass': rec.get('p_pass'),
        'p_fail': rec.get('p_fail'),
        'issue_type': rec.get('issue_type'),
        'issue_conf': rec.get('issue_conf'),
    }


def delta_of(a, b):
    """两向量间最大绝对位移；issue_type 不同按 1.0 计（离散字段整体翻转）。"""
    d = max(abs(float(a[k]) - float(b[k]))
            for k in ('term', 'semantic', 'p_pass', 'p_fail', 'issue_conf'))
    if a['issue_type'] != b['issue_type']:
        d = max(d, 1.0)
    return round(d, 4)


def sha256_file(path):
    """文件的 sha256；路径不存在时返回 None（调用方决定是否可接受）。"""
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build(args):
    if not args.contract:
        print('--build 需要 --contract：基线靠它的 sha256 做归因闸门', file=sys.stderr)
        return 2
    rep = json.load(open(args.report, encoding='utf-8'))
    src = {}
    if args.translations:
        tr = json.load(open(args.translations, encoding='utf-8'))
        for u in tr['translations']:
            src[str(u['xml_index'])] = (u['source'], u['translation'])
    cases, missing = [], []
    for r in rep['records']:
        idx = str(r['idx'])
        s, t = src.get(idx, ('', ''))
        if not s:
            missing.append(idx)
        cases.append({
            'case_id': '%s-%s' % (rep['batch'], idx),
            'idx': idx,
            'source': s,
            'translation': t,
            'contract_hits': r['contract_hits'],
            'baseline': vector_of(r),
            'baseline_verdicts': r['verdicts'],
            'baseline_level': level_of(r['verdicts']),
        })
    out = {
        'schema_version': 1,
        'purpose': 'TypeSafe 语义门判定轨迹基线（哨兵用，不判对错）',
        'source_batch': rep['batch'],
        'source_report': os.path.basename(args.report),
        'model': sg.MODEL,
        'contract_path': os.path.basename(args.contract),
        'contract_sha256': sha256_file(args.contract),
        'thresholds': {'hard': sg.HARD, 'soft': sg.SOFT, 'register_conf': sg.REGISTER_CONF},
        'note': args.note,
        'cases': cases,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('baseline -> %s' % args.out)
    print('cases=%d  level: none=%d WARN=%d FAIL=%d' % (
        len(cases),
        sum(1 for c in cases if c['baseline_level'] == 'none'),
        sum(1 for c in cases if c['baseline_level'] == 'WARN'),
        sum(1 for c in cases if c['baseline_level'] == 'FAIL')))
    if missing:
        print('无 source 的条目（不参与比对，仅存档）：%d 条 %s'
              % (len(missing), missing[:8]))
    return 0


def check(args):
    api_key = os.environ.get('TYPESAFE_API_KEY', '').strip()
    if not api_key:
        print('SEMBASE_NO_KEY: TYPESAFE_API_KEY 未设置，无法比对', file=sys.stderr)
        return 3
    bl = json.load(open(args.baseline, encoding='utf-8'))
    terms = sg.load_contract(args.contract)
    cases = [c for c in bl['cases'] if c['source'] and c['translation']]

    # 归因闸门：基线记录的契约 hash 与当前不一致时，位移可能来自契约而非模型。
    # 不拦执行（仍跑完整比对），但在输出顶部标注，避免误判为模型漂移。
    now_hash = sha256_file(args.contract)
    base_hash = bl['contract_sha256']
    contract_changed = base_hash != now_hash
    if contract_changed:
        print('CONTRACT_CHANGED: 契约与建基线时不一致（%s -> %s）'
              % (base_hash[:12], now_hash[:12]))
        print('  → 下方位移先核对契约变更，再怀疑模型漂移。')

    def run_case(c):
        hits = sg.match_terms(c['source'], terms)
        runs = [sg.judge_unit(api_key, c['idx'], c['source'], c['translation'], hits)
                for _ in range(args.repeat)]
        return c, hits, runs

    results = []
    with ThreadPoolExecutor(max_workers=sg.WORKERS) as ex:
        for c, hits, runs in ex.map(run_case, cases):
            base = c['baseline']
            vecs = [vector_of(r) for r in runs]
            max_delta = max(delta_of(base, v) for v in vecs)
            spread = max((delta_of(vecs[i], vecs[j])
                          for i in range(len(vecs)) for j in range(i + 1, len(vecs))),
                         default=0.0)
            new_level = max(level_of(r['verdicts']) for r in runs)
            base_level = c['baseline_level']
            hits_now = sorted(h['source'] for h in hits)
            hits_then = sorted(c['contract_hits'])

            if new_level != base_level:
                event = 'FLIP'
            elif max_delta > args.tolerance:
                event = 'DRIFT'
            else:
                event = 'OK'
            results.append({
                'case_id': c['case_id'],
                'idx': c['idx'],
                'event': event,
                'level': {'then': base_level, 'now': new_level},
                'max_delta': max_delta,
                'spread': round(spread, 4),
                'fragile': spread > args.tolerance,
                'vector': {'then': base, 'now': vecs[-1]},
                'contract_hits': {'then': hits_then, 'now': hits_now},
                'verdicts_now': [v for r in runs for v in r['verdicts']],
            })

    flips = [r for r in results if r['event'] == 'FLIP']
    drifts = [r for r in results if r['event'] == 'DRIFT']
    fragiles = [r for r in results if r['fragile']]
    print('checked=%d  FLIP=%d  DRIFT=%d  OK=%d  fragile=%d'
          % (len(results), len(flips), len(drifts),
             len(results) - len(flips) - len(drifts), len(fragiles)))
    for r in flips:
        print('FLIP  %s  %s->%s  delta=%.3f  %s'
              % (r['case_id'], r['level']['then'], r['level']['now'],
                 r['max_delta'], json.dumps(r['verdicts_now'], ensure_ascii=False)[:160]))
    for r in drifts:
        print('DRIFT %s  delta=%.3f  then=%s now=%s'
              % (r['case_id'], r['max_delta'],
                 json.dumps(r['vector']['then'], ensure_ascii=False),
                 json.dumps(r['vector']['now'], ensure_ascii=False)))
    if fragiles:
        print('FRAGILE（重复跑自身抖动超容差，该判定落在模型能力边界）：')
        for r in fragiles:
            print('  %s  spread=%.3f' % (r['case_id'], r['spread']))

    if args.out:
        json.dump({'baseline': args.baseline, 'tolerance': args.tolerance,
                   'repeat': args.repeat,
                   'contract': {'then': base_hash, 'now': now_hash,
                                'changed': contract_changed},
                   'summary': {
                       'checked': len(results), 'flip': len(flips),
                       'drift': len(drifts), 'fragile': len(fragiles)},
                   'results': results},
                  open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('diff report -> %s' % args.out)
    return 1 if (flips or drifts) else 0


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='TypeSafe 语义门基线：固化轨迹 + 漂移比对')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--build', action='store_true', help='从 semgate report 固化基线')
    g.add_argument('--check', action='store_true', help='重跑并比对基线')
    ap.add_argument('--report', help='--build：semgate report 路径')
    ap.add_argument('--translations', help='--build：对应 translation.json（取 source）')
    ap.add_argument('--contract', help='compiled contract（build 记 hash，check 比对）')
    ap.add_argument('--baseline', help='--check：基线文件')
    ap.add_argument('--out', help='输出路径（build=基线，check=差异报告）')
    ap.add_argument('--note', default='', help='--build：备注')
    ap.add_argument('--tolerance', type=float, default=0.15, help='位移容差（默认 0.15）')
    ap.add_argument('--repeat', type=int, default=1, help='每条重复跑次数（测稳定性）')
    a = ap.parse_args()

    if a.build:
        if not a.report or not a.out:
            print('--build 需要 --report 与 --out', file=sys.stderr)
            return 2
        return build(a)
    if not a.baseline or not a.contract:
        print('--check 需要 --baseline 与 --contract', file=sys.stderr)
        return 2
    return check(a)


if __name__ == '__main__':
    sys.exit(main())
