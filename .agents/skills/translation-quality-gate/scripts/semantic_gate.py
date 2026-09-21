# -*- coding: utf-8 -*-
"""semantic_gate.py — TypeSafe 语义质量门（术语契约 + 语义错误 + 质量分级）。

定位：quality_gate.py（机械层：字面禁形/必备目标）之外的语义层。机械层判不了的
语境适用性（alias 条目该语境是否成立）、语义错译、语域硬伤，由本门判断。
两层互补，机械层仍全权负责字面匹配，本门不重复其检查。

判定契约（阈值在本文件常量，调整需记录理由）：
  FAIL        term_violation >= HARD 或 semantic_error >= HARD
  WARN 升级   HARD > term_violation/semantic_error >= SOFT（进人工复核队列）
  WARN 软提示 issue_type=register 且置信 >= REGISTER_CONF（仅提示，不拦）
  无 API key  verdict=SKIP，rc=0（不阻塞验收主线；stdout 打 SEMGATE_SKIPPED）

用法：
  py semantic_gate.py --result <translation.json> --xml <source.xml>
      --contract <compiled.json> [--batch <BID>] [--report <report.json>]

stdout 首行 JSON verdict（与 quality_gate.py 约定一致，供 verify 抓取）：
  {"verdict": "PASS|FAIL|SKIP", "units_checked": N, "fail_count": F, "warning_count": W}
退出码：PASS/SKIP 0，FAIL 1，用法错误 2。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

API_URL = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-latest'
HARD = 0.5          # term/semantic 硬拦截阈值（禁用词级违反实测召回 19/19）
SOFT = 0.3          # 升级队列下限（high 级漏判的实测分布 0.23~0.49 落入此带）
REGISTER_CONF = 0.5  # register 软提示的归因置信下限
WORKERS = 4
RETRIES = 3

MOD_REGISTER = (
    '《上古卷轴5：天际》全语音随从 MOD 的日常对话台词（英译中）。'
    '角色语域：克制、古旧的奇幻文体。注意：这是对话台词，自然的口语化、短句、'
    '句首语气词属于正常对话节奏，不算语域问题；只有明显现代感、网络化、'
    '现实词汇或破坏奇幻语域的表达才算语域问题。'
    '专名必须按术语契约翻译，不得自拟。'
)

WORD_RE_CACHE = {}


def _word_re(term):
    r = WORD_RE_CACHE.get(term)
    if r is None:
        r = re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)
        WORD_RE_CACHE[term] = r
    return r


def load_contract(path):
    data = json.load(open(path, encoding='utf-8'))
    terms = []
    for t in (data.get('terms') or {}).values():
        terms.append({
            'source': t.get('source', ''),
            'target': t.get('target', ''),
            'forbidden': t.get('forbidden') or [],
            'note': t.get('note', ''),
            'enforcement': t.get('enforcement', ''),
            'alias': bool(t.get('risk_flags')),
        })
    return terms


def match_terms(source_en, terms):
    hits = []
    for t in terms:
        if t['source'] and _word_re(t['source']).search(source_en or ''):
            hits.append(t)
    return hits


def make_payload(source_en, translation_zh, hits):
    contract_view = []
    for h in hits:
        entry = {'source': h['source'], 'required_zh': h['target']}
        if h['forbidden']:
            entry['forbidden_zh'] = h['forbidden']
        if h['note']:
            entry['usage_note'] = h['note']
        contract_view.append(entry)
    state = {
        'mod_register': MOD_REGISTER,
        'term_contract': contract_view if contract_view else '（本条源文不涉及术语契约条目）',
        'source_en': source_en,
        'translation_zh': translation_zh,
    }
    return {
        'state': state,
        'model': MODEL,
        'questions': {
            'term_violation': {
                'type': 'noul',
                'instructions': (
                    '译文是否违反了术语契约？违反指：适用条目要求的专名未按 required_zh 翻译，'
                    '或使用了 forbidden_zh 禁形。注意：条目的 usage_note 若注明了适用语境'
                    '（如 alias 风险、普通名词义不挂钩、依场景/族属而定），先判断该语境在源文中'
                    '是否成立；语境不成立则该条目不适用，不算违反。源文不涉及契约条目时判否。'
                ),
            },
            'semantic_error': {
                'type': 'noul',
                'instructions': '译文是否错译或漏译了源文的关键语义？包括：词义误解'
                                '（尤其动词、名词等实词被替换为含义不同的词，如「复起」误作「想起」）、'
                                '关键信息丢失、源文确定语义被改变。以下不算语义错误：'
                                '基于语境的合理显化（代词所指的具体化，如 mine 译作「我的历史」、'
                                'you 依上下文显化为对方的名字或身份）、语域风格问题。',
            },
            'quality': {
                'type': 'score',
                'instructions': '该译文作为此 MOD 发布级译文的质量等级',
                'criteria': [
                    '合格：语义准确、术语符合契约、语域无硬伤（自然口语化与短句不算问题），可直接发布',
                    '小瑕疵：语义基本正确，但存在明显现代感/网络化/现实词汇等语域硬伤，或句法拖沓，建议修订',
                    '不合格：语义错译、关键漏译、术语违反契约或严重语域越界，必须修订',
                ],
            },
            'issue_type': {
                'type': 'choice',
                'instructions': '该译文最主要的问题类型（无问题则选 none）',
                'criteria': {
                    'none': '无问题，可直接发布',
                    'semantic': '语义错译或关键漏译',
                    'term': '术语契约违反（专名未按契约或使用禁用译法）',
                    'register': '语域硬伤（明显现代感、网络化、现实词汇；自然口语化不算）',
                    'fluency': '句法拖沓或不通顺',
                },
            },
        },
    }


def ask(api_key, payload):
    body = json.dumps(payload).encode('utf-8')
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            API_URL, data=body,
            headers={'Authorization': 'Bearer ' + api_key,
                     'Content-Type': 'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def judge_unit(api_key, idx, source_en, translation_zh, hits):
    payload = make_payload(source_en, translation_zh, hits)
    resp = ask(api_key, payload)
    a = resp['answers']
    qp = a['quality'].get('probabilities', {})
    rec = {
        'idx': idx,
        'term': a['term_violation']['noul'],
        'semantic': a['semantic_error']['noul'],
        'p_pass': float(qp.get('0', 0.0)),
        'p_fail': float(qp.get('2', 0.0)),
        'issue_type': a['issue_type']['choice'],
        'issue_conf': a['issue_type'].get('confidence', 0.0),
        'contract_hits': [h['source'] for h in hits],
        'usage': resp.get('usage'),
    }
    verdicts = []
    if rec['term'] >= HARD:
        verdicts.append(('FAIL', 'TERM_VIOLATION',
                         'term=%.2f 疑似术语契约违反（命中 %s）' % (rec['term'], ','.join(rec['contract_hits']))))
    elif rec['term'] >= SOFT:
        verdicts.append(('WARN', 'TERM_ESCALATE', 'term=%.2f 边界，进人工复核' % rec['term']))
    if rec['semantic'] >= HARD:
        verdicts.append(('FAIL', 'SEMANTIC_ERROR', 'semantic=%.2f 疑似语义错译/漏译' % rec['semantic']))
    elif rec['semantic'] >= SOFT:
        verdicts.append(('WARN', 'SEM_ESCALATE', 'semantic=%.2f 边界，进人工复核' % rec['semantic']))
    if rec['issue_type'] == 'register' and rec['issue_conf'] >= REGISTER_CONF:
        verdicts.append(('WARN', 'REGISTER_HINT',
                         'register conf=%.2f 语域硬伤软提示' % rec['issue_conf']))
    if not verdicts and rec['p_fail'] >= HARD:
        verdicts.append(('WARN', 'QUALITY_FAIL_LEAN',
                         'P(不合格)=%.2f 质量分级偏不合格' % rec['p_fail']))
    rec['verdicts'] = verdicts
    return rec


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--result', required=True)
    ap.add_argument('--xml', required=True)
    ap.add_argument('--contract', required=True)
    ap.add_argument('--batch', default='BATCH')
    ap.add_argument('--report', default=None)
    a = ap.parse_args()

    api_key = os.environ.get('TYPESAFE_API_KEY', '').strip()
    if not api_key:
        print(json.dumps({'verdict': 'SKIP', 'units_checked': 0,
                          'fail_count': 0, 'warning_count': 0}, ensure_ascii=False))
        print('SEMGATE_SKIPPED: TYPESAFE_API_KEY 未设置，语义门跳过（不阻塞验收）')
        return 0

    result = json.load(open(a.result, encoding='utf-8'))
    terms = load_contract(a.contract)

    # result 两种形态：dict 平铺 {idx: {translation, status}} 或
    # 批次正式格式 {..., 'translations': [{xml_index, source, translation, status}]}
    raw_units = []
    if isinstance(result, dict) and isinstance(result.get('translations'), list):
        for v in result['translations']:
            if isinstance(v, dict):
                raw_units.append((str(v.get('xml_index')), v))
    elif isinstance(result, dict):
        raw_units = [(k, v) for k, v in result.items() if isinstance(v, dict)]

    strs = None  # 懒加载：条目缺 source 时才解析 XML
    units = []
    for k, v in raw_units:
        if not str(k).isdigit():
            continue
        if v.get('status') != 'TRANSLATED':
            continue  # KEEP/REVIEW 不由语义门判
        tr = v.get('translation') or ''
        src = v.get('source') or ''
        if not src:
            if strs is None:
                strs = ET.parse(a.xml).getroot().findall('.//String')
            i = int(k)
            if 0 <= i < len(strs):
                src = strs[i].findtext('Source') or ''
        if src and tr:
            units.append((k, src, tr))

    fails, warnings, records = [], [], []
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {}
        for k, src, tr in units:
            hits = match_terms(src, terms)
            futures[ex.submit(judge_unit, api_key, k, src, tr, hits)] = k
        for fut in futures:
            k = futures[fut]
            try:
                rec = fut.result()
                records.append(rec)
                for level, code, detail in rec['verdicts']:
                    (fails if level == 'FAIL' else warnings).append(
                        {'where': k, 'code': code, 'detail': detail})
            except Exception as e:  # noqa: BLE001
                errors.append(k)
                warnings.append({'where': k, 'code': 'SEMGATE_ERROR',
                                 'detail': '%s: %s（该条未判，进人工复核）' % (type(e).__name__, str(e)[:120])})

    records.sort(key=lambda r: int(r['idx']))
    verdict = 'FAIL' if fails else 'PASS'
    print(json.dumps({'verdict': verdict, 'units_checked': len(records),
                      'fail_count': len(fails), 'warning_count': len(warnings)},
                     ensure_ascii=False))
    for f in fails[:20]:
        print('FAIL', f['where'], f['code'], f['detail'][:160])
    for w in warnings[:30]:
        print('WARN', w['where'], w['code'], w['detail'][:160])
    if errors:
        print('WARN', a.batch, 'SEMGATE_ERRORS', '%d 条调用失败未判: %s' % (len(errors), errors[:8]))

    rep = a.report
    if not rep:
        m = re.search(r'(?:^|[/\\])(?:mods|\.work)[/\\]([^/\\]+)[/\\]',
                      a.result.replace('\\', '/'))
        plugin = m.group(1) if m else 'unknown'
        if plugin.lower().endswith(('.esp', '.esm', '.esl')):
            plugin = plugin.rsplit('.', 1)[0]
        rep = os.path.join('.work', plugin, 'reports', '%s-semgate-report.json' % a.batch)
    json.dump({'batch': a.batch, 'verdict': verdict,
               'units_checked': len(records), 'fails': fails, 'warnings': warnings,
               'records': records},
              open(rep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('semgate report -> %s' % rep)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
