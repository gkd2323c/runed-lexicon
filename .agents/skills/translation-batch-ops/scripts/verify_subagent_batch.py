# -*- coding: utf-8 -*-
"""子代理翻译产出验收：完整性 + 内容初筛 + gate，三段只读；--repair 才写盘。

对应 translation-batch-ops SKILL 批次验收节中可机械化的部分（格式查全部 +
术语查的机械前置）。语义查仍须 Agent/人做，本脚本不管。

用法：
  py verify_subagent_batch.py --plan <batches.json> --batch <BID> --map <map.json>
      [--xml <source-xml>] [--result <translation.json>] [--context <context.json>]
      [--contract <compiled.json>] [--keep-list <keep.json>]
      [--report <report.json>] [--repair]

- --plan/--batch/--map 必需：plan 的 batches[].id==BID，提供 idx 权威集合；
  map 键为 str(xml_index)，值为 {translation, status, ...}。
- --xml 给出时才做 KEEP 语义与英文残留检查（需按 idx 取 Source）。
- --result 给出时跑 translation_result.py validate（需 --context）与 quality_gate；
  --contract 缺省则只做 validate 不做 gate。
- --report 缺省为 .work/<plugin>/reports/<BID>-verify-report.json（新输出角色，见 SKILL 批次验收节）。
- 默认只读。--repair 时用 fill_translation_set 把 map 填入 result（只填 PENDING，
  已译条目跳过，符合幂等保护），再重跑 validate + gate。

退出码：0 PASS（仅 WARNING），1 FAIL，2 用法错误。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
FILL_SET = os.path.normpath(os.path.join(
    HERE, '..', '..', 'translation-executor', 'scripts', 'fill_translation_set.py'))
VALIDATE = os.path.normpath(os.path.join(
    HERE, '..', '..', 'translation-executor', 'scripts', 'translation_result.py'))
GATE = os.path.normpath(os.path.join(
    HERE, '..', '..', 'translation-quality-gate', 'scripts', 'quality_gate.py'))
SEMGATE = os.path.normpath(os.path.join(
    HERE, '..', '..', 'translation-quality-gate', 'scripts', 'semantic_gate.py'))

STATUSES = {'TRANSLATED', 'KEEP', 'REVIEW'}
CONFIDENCES = {'HIGH', 'MEDIUM', 'LOW'}
EN_RESIDUE = re.compile(r'[A-Za-z]{4,}')


def fail(bucket, where, code, detail):
    bucket.append({'where': where, 'code': code, 'detail': detail})


def default_report_path(a):
    """默认验收报告落点：.work/<plugin>/reports/<BID>-verify-report.json。
    插件名从 --xml / --map / --plan 路径推导；推导失败时回退 .work/<BID>-verify-report.json。"""
    for s in (a.xml, a.map, a.plan):
        if not s:
            continue
        t = str(s).replace('\\', '/')
        m = re.search(r'(?:^|/)(?:mods|\.work)/([^/]+)/', t)
        if m:
            p = m.group(1)
            if p.lower().endswith(('.esp', '.esm', '.esl')):
                p = p.rsplit('.', 1)[0]
            return os.path.join('.work', p, 'reports', '%s-verify-report.json' % a.batch)
    return os.path.join('.work', '%s-verify-report.json' % a.batch)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', required=True)
    ap.add_argument('--batch', required=True)
    ap.add_argument('--map', required=True)
    ap.add_argument('--xml', default=None)
    ap.add_argument('--result', default=None)
    ap.add_argument('--context', default=None)
    ap.add_argument('--contract', default=None)
    ap.add_argument('--keep-list', default=None)
    ap.add_argument('--report', default=None)
    ap.add_argument('--repair', action='store_true')
    ap.add_argument('--no-semantic', dest='semantic', action='store_false',
                    help='关闭 TypeSafe 语义门（默认开；需 --xml/--contract/--result 齐备，'
                         '无 TYPESAFE_API_KEY 时报 UNCHECKED 不阻塞主线）')
    a = ap.parse_args()

    fails, warnings = [], []
    plan = json.load(open(a.plan, encoding='utf-8'))
    batch = next((b for b in plan.get('batches', []) if b.get('id') == a.batch), None)
    if batch is None:
        print('unknown batch %s' % a.batch, file=sys.stderr)
        return 2
    expect = {str(i) for i in batch.get('idx', [])}
    m = json.load(open(a.map, encoding='utf-8'))
    if not isinstance(m, dict):
        print('map is not a JSON object', file=sys.stderr)
        return 2
    got = set(m.keys())

    # A. 完整性：键集合严格相等
    missing = sorted(expect - got, key=int)
    extra = sorted(got - expect, key=int)
    overlap = len(expect & got)
    if missing:
        fail(fails, a.batch, 'KEYS_MISSING', '%d missing e.g. %s' % (len(missing), missing[:8]))
    if extra:
        fail(fails, a.batch, 'KEYS_EXTRA', '%d extra e.g. %s' % (len(extra), extra[:8]))
    cover = overlap / len(expect) if expect else 0
    if expect and cover < 0.5:
        fail(fails, a.batch, 'KEYS_MISALIGNED',
             'overlap %d/%d=%.2f 疑似键错位，整批不可用' % (overlap, len(expect), cover))

    # 源文载入（KEEP/残留检查用）
    srcs = {}
    if a.xml:
        strs = ET.parse(a.xml).getroot().findall('.//String')
        for i in (int(k) for k in got | expect if str(k).isdigit()):
            if 0 <= i < len(strs):
                srcs[str(i)] = strs[i].findtext('Source') or ''

    # B. 内容初筛
    waiting, keep_bad, empty, bad_status, residue = [], [], [], [], []
    for k in sorted(got, key=int):
        v = m[k] if isinstance(m[k], dict) else {}
        tr = v.get('translation') or ''
        st = v.get('status')
        cf = v.get('confidence', 'HIGH')
        src = srcs.get(k, '')
        if st == 'WAITING' or not tr:
            waiting.append(k)
            continue
        if st not in STATUSES:
            bad_status.append(k)
        if cf not in CONFIDENCES:
            warnings.append({'where': k, 'code': 'CONFIDENCE',
                             'detail': 'confidence=%r 非法' % cf})
        if st == 'KEEP' and srcs and tr != src:
            keep_bad.append(k)
        if srcs and tr and tr != src:
            stripped = re.sub(r'<[^>]+>|\[[^\]]*\]|%[sd]\b', ' ', tr)
            if EN_RESIDUE.search(stripped) and not re.fullmatch(r'[^A-Za-z]*[A-Za-z .!…]+', src or ' X '):
                residue.append(k)
    if waiting:
        fail(fails, a.batch, 'INCOMPLETE', '%d 未完成(含WAITING/空译) e.g. %s' % (len(waiting), waiting[:8]))
    if bad_status:
        fail(fails, a.batch, 'STATUS', '%d 非法状态 e.g. %s' % (len(bad_status), bad_status[:8]))
    if keep_bad:
        fail(fails, a.batch, 'KEEP_MODIFIED', '%d KEEP≠原文 e.g. %s' % (len(keep_bad), keep_bad[:8]))
    if residue:
        warnings.append({'where': a.batch, 'code': 'EN_RESIDUE',
                         'detail': '%d 疑似英文残留 e.g. %s' % (len(residue), residue[:8])})

    def run_validate_gate(result):
        out = {'validate': None, 'gate': None}
        if a.context:
            r = subprocess.run([sys.executable, VALIDATE, 'validate', result,
                                '--context', a.context],
                               capture_output=True, text=True)
            out['validate'] = {'rc': r.returncode, 'tail': (r.stdout + r.stderr)[-500:]}
            if r.returncode:
                fail(fails, a.batch, 'VALIDATE', out['validate']['tail'][-200:])
        if a.contract:
            # gate 的 --report 受命名契约限制（只认 *-gate-report.json），验收不借用：
            # gate 段只取 stdout verdict 计数；FAIL 时 Agent 用合规报告名重跑取明细。
            r = subprocess.run([sys.executable, GATE, '--result', result,
                                '--contract', a.contract]
                               + (['--keep-list', a.keep_list] if a.keep_list else [])
                               + ['--auto-bind'],
                               capture_output=True, text=True)
            tail = (r.stdout + r.stderr)[-500:]
            try:
                gv = json.loads((r.stdout or '').strip().splitlines()[0])
                out['gate'] = {'rc': r.returncode, 'verdict': gv.get('verdict'),
                               'units': gv.get('units_checked'),
                               'fails': gv.get('fail_count'),
                               'warnings': gv.get('warning_count')}
                # gate 的 review candidate（auto-bind 术语缺目标等）必须对 Agent 可见：
                # 抓取 WARN 明细行并入顶层 warnings，顶层计数与 WARN 行同步显示
                gw_lines = [l.strip() for l in (r.stdout or '').splitlines()
                            if l.strip().startswith('WARN')]
                for wl in gw_lines:
                    warnings.append({'where': a.batch, 'code': 'GATE_WARN', 'detail': wl[:220]})
                out['gate']['warn_lines'] = gw_lines[:30]
                if gv.get('verdict') != 'PASS':
                    fail(fails, a.batch, 'GATE',
                         'verdict=%s fails=%s warnings=%s（用 *-gate-report.json 重跑取明细）'
                         % (gv.get('verdict'), gv.get('fail_count'), gv.get('warning_count')))
            except Exception:
                fail(fails, a.batch, 'GATE', tail[-200:])
        # D. TypeSafe 语义门（机械 gate 之后的语义层；无 key 时显式记 UNCHECKED）
        if a.semantic and a.contract and a.xml:
            r = subprocess.run([sys.executable, SEMGATE, '--result', result,
                                '--xml', a.xml, '--contract', a.contract,
                                '--batch', a.batch],
                               capture_output=True, text=True)
            try:
                sv = json.loads((r.stdout or '').strip().splitlines()[0])
                out['semantic_gate'] = {'rc': r.returncode, 'verdict': sv.get('verdict'),
                                        'units': sv.get('units_checked'),
                                        'unjudged': sv.get('unjudged_count'),
                                        'fails': sv.get('fail_count'),
                                        'warnings': sv.get('warning_count')}
                sw_lines = [l.strip() for l in (r.stdout or '').splitlines()
                            if l.strip().startswith(('WARN', 'FAIL', 'UNJUDGED'))]
                for sl in sw_lines:
                    warnings.append({'where': a.batch, 'code': 'SEMGATE', 'detail': sl[:220]})
                out['semantic_gate']['warn_lines'] = sw_lines[:30]
                sv_verdict = sv.get('verdict')
                if sv_verdict == 'FAIL':
                    fail(fails, a.batch, 'SEMGATE',
                         'verdict=FAIL fails=%s（见 %s-semgate-report.json）'
                         % (sv.get('fail_count'), a.batch))
                elif sv_verdict == 'PARTIAL':
                    # 有条目未判定：本批语义层未完成，不得当作通过。
                    fail(fails, a.batch, 'SEMGATE_PARTIAL',
                         '%s 条未判定（调用失败），本批语义层未完成（见 %s-semgate-report.json）'
                         % (sv.get('unjudged_count'), a.batch))
                elif sv_verdict == 'UNCHECKED':
                    # 无 key：本批语义层未检查。不阻断主线，但状态显式入报告，
                    # 不与「检查过且通过」混淆。
                    out['semantic_gate']['checked'] = False
                    warnings.append({'where': a.batch, 'code': 'SEMGATE_UNCHECKED',
                                     'detail': 'TYPESAFE_API_KEY 未设置，本批语义层未检查'})
                else:
                    out['semantic_gate']['checked'] = True
            except Exception as e:  # noqa: BLE001
                # 解析失败 = 语义门结果未知，与「未检查」不同，不得静默放过。
                fail(fails, a.batch, 'SEMGATE_UNKNOWN',
                     '语义门输出无法解析（%s），本批语义层结果未知：%s'
                     % (type(e).__name__, (r.stdout + r.stderr)[-200:]))
        return out

    gate_out = {}
    target = a.result
    if a.repair:
        if not a.result:
            print('--repair 需要 --result', file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory() as td:
            mf = os.path.join(td, 'manifest.json')
            json.dump({'schema_version': 1, 'files': [
                {'result': os.path.abspath(a.result), 'map': os.path.abspath(a.map),
                 'output_name': os.path.basename(a.result), 'overwrite': False,
                 'by_source': False}]}, open(mf, 'w', encoding='utf-8'))
            od = os.path.join(td, 'out')
            r = subprocess.run([sys.executable, FILL_SET, '--manifest', mf,
                                '--output-dir', od], capture_output=True, text=True)
            if r.returncode:
                fail(fails, a.batch, 'REPAIR_FILL', (r.stdout + r.stderr)[-500:])
                target = None
            else:
                import shutil
                shutil.copy(os.path.join(od, os.path.basename(a.result)), a.result)
                print('%s repair applied(仅PENDING)' % a.batch)
    if target:
        gate_out = run_validate_gate(target)

    verdict = 'FAIL' if fails else 'PASS'
    print('%s %s missing=%d extra=%d waiting=%d keep_bad=%d fails=%d warnings=%d'
          % (a.batch, verdict, len(missing), len(extra), len(waiting),
             len(keep_bad), len(fails), len(warnings)))
    for f in fails[:20]:
        print('  FAIL', f['where'], f['code'], f['detail'][:100])
    for w in warnings[:10]:
        print('  WARN', w['where'], w['code'], w.get('detail', '')[:100])
    if a.report or a.contract:
        rep = a.report or default_report_path(a)
        json.dump({'batch': a.batch, 'verdict': verdict, 'fails': fails,
                   'warnings': warnings, 'gate': gate_out},
                  open(rep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('report -> %s' % rep)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
