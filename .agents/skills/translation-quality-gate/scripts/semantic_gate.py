# -*- coding: utf-8 -*-
"""semantic_gate.py — TypeSafe 语义质量门（术语契约 + 语义错误 + 质量分级）。

定位：quality_gate.py（机械层：字面禁形/必备目标）之外的语义层。机械层判不了的
语境适用性（alias 条目该语境是否成立）、语义错译、语域硬伤，由本门判断。
两层互补，机械层仍全权负责字面匹配，本门不重复其检查。

判定契约（阈值在本文件常量，调整需记录理由）：
  FAIL        term_violation >= HARD 或 semantic_error >= HARD
  WARN 升级   HARD > term_violation/semantic_error >= SOFT（进人工复核队列）
  WARN 软提示 issue_type=register 且置信 >= REGISTER_CONF（仅提示，不拦）
  PARTIAL     有条目未能判定（调用失败/缺字段）——本批不得视为已过语义门
  UNCHECKED   无 API key，本批语义层未检查（rc=0 不阻塞，但状态显式记录）

豁免通道（人工复核裁决的落盘）：
  --waive "<idx>:<理由>"  登记豁免（可重复）：从 --result 取该 idx 当前译文算哈希，
                          写入豁免文件后退出（不调 API）。译文一改哈希即失配，豁免
                          自动失效（FAIL 附注 WAIVER_STALE），旧裁决不会掩盖新错误。
  --revoke "<idx>"        撤销豁免（可重复）。
  --waivers <path>        豁免文件路径；缺省自动探测 .work/<plugin>/contracts/
                          semgate-waivers.json（从 --result 路径推断，与报告同规则）。
判定：FAIL 项命中有效豁免 → 降为 WAIVED（不拦截、留痕于报告与 stdout）。

用法：
  py semantic_gate.py --result <translation.json> --xml <source.xml>
      --contract <compiled.json> [--batch <BID>] [--report <report.json>]
      [--waivers <path>] [--waive "<idx>:<理由>"] [--revoke "<idx>"]

stdout 首行 JSON verdict（与 quality_gate.py 约定一致，供 verify 抓取）：
  {"verdict": "PASS|FAIL|PARTIAL|UNCHECKED", "units_checked": N, ...}
退出码：PASS/UNCHECKED 0，FAIL/PARTIAL 1，用法错误 2。
"""
import argparse
import hashlib
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
    '角色语域以克制、古旧的格言式为主，但对话中也存在该角色的诙谐、调侃、'
    '自嘲变体，二者都在正常语域内。注意：这是对话台词，自然的口语化、短句、'
    '句首语气词属于正常对话节奏，不算语域问题；只有明显现代感、网络化、'
    '现实词汇或破坏奇幻语域的表达才算语域问题。'
    '专名必须按术语契约翻译，不得自拟。'
)

WORD_RE_CACHE = {}


def _tr_hash(translation):
    return hashlib.sha256(translation.encode('utf-8')).hexdigest()[:16]


def _plugin_from_result(result_path):
    m = re.search(r'(?:^|[/\\])(?:mods|\.work)[/\\]([^/\\]+)[/\\]',
                  result_path.replace('\\', '/'))
    plugin = m.group(1) if m else 'unknown'
    if plugin.lower().endswith(('.esp', '.esm', '.esl')):
        plugin = plugin.rsplit('.', 1)[0]
    return plugin


def default_waivers_path(result_path):
    return os.path.join('.work', _plugin_from_result(result_path),
                        'contracts', 'semgate-waivers.json')


def load_waivers(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    out = {}
    for idx, w in (data.get('waivers') or {}).items():
        if isinstance(w, dict) and w.get('hash'):
            out[str(idx)] = w
    return out


def save_waivers(path, waivers):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    json.dump({'schema_version': 1, 'waivers': waivers},
              open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


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
        # enforcement 必须随条目注入：缺失时 LLM 把 FORBIDDEN_ONLY（只查禁形、
        # 不自动绑定 required，词条 note 自声明「普通词/前缀风险不自动绑定」）
        # 条目当必填判，产生 TERM_VIOLATION 假阳性（实测：The Blade→「刀剑」
        # required 未现在译「利刃」的普通义用法上，term=0.63 误判 FAIL）。
        entry['enforcement'] = h.get('enforcement') or 'REQUIRED'
        # 契约的匹配语义是 contains_phrase（译文包含该形式即合规），不是逐字相等。
        # 不显式注入这条，模型会拿 required_zh 做逐字比对，把合规的全称/省称译法
        # 判成 TERM_VIOLATION（实测：Whiterun→「白漫城」term=0.51~0.61 误判 FAIL；
        # 注入本字段后同例降至 0.15~0.17）。
        entry['match_semantics'] = (
            'contains_phrase：译文只要包含 required_zh 的形式即合规，不要求逐字相等'
            '（如 required_zh=「白漫」时，「白漫」「白漫城」均合规）。')
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
                    '或使用了 forbidden_zh 禁形。注意 required_zh 是匹配形式而非逐字要求：'
                    '按条目的 match_semantics（contains_phrase）判断，译文包含该形式即合规。'
                    '条目 enforcement=FORBIDDEN_ONLY 时只检查 forbidden_zh 是否出现，'
                    '不要求 required_zh（此类条目多为普通词/前缀风险，普通义语境不适用其专名形）。'
                    '条目的 usage_note 若注明了适用语境'
                    '（如 alias 风险、普通名词义不挂钩、依场景/族属而定），先判断该语境在源文中'
                    '是否成立；语境不成立则该条目不适用，不算违反。源文不涉及契约条目时判否。'
                ),
            },
            # 2026-09-22 扩充 semantic_error 判定指令（针对豁免清单暴露的系统性误报）：
            # 字面隐喻/语类双关/弱化否定/省略回指的忠实直译不算语义错误 + 判定方法指令。
            # 回放实测：26499 三轮 0.51~0.73 硬拦→0.39 消除；真错召回无损
            #（26400 旧译 0.71 FAIL、26501 旧译 0.50 FAIL）；分数普降但显化类
            #（26486 0.85→0.60 且双跑跨线抖动、26193 0.72、26031 0.52）仍越线，
            # 继续靠豁免通道人工裁决。MOD_REGISTER 同步声明调佩变体属正常语域。
            'semantic_error': {
                'type': 'noul',
                'instructions': '译文是否错译或漏译了源文的关键语义？包括：词义误解'
                                '（尤其动词、名词等实词被替换为含义不同的词，如「复起」误作「想起」）、'
                                '关键信息丢失、源文确定语义被改变。以下不算语义错误：'
                                '基于语境的合理显化（代词所指的具体化，如 mine 译作「我的历史」、'
                                'you 依上下文显化为对方的名字或身份；被动句补出施事；'
                                '省略句补出与上文一致的回指成分；排比句复现词的统一处理）、'
                                '语域风格问题。源文使用非常规修辞（字面隐喻、语类或词义双关、'
                                '非常规动宾搭配）时，忠实保留字面结构的译文即使读来新奇也是正确译法，'
                                '不得因「该表达在中文里不常见」判为错译。'
                                '判定方法：先逐个在源文中定位关键成分的对应表达，'
                                '弱化否定与多重否定先还原源文逻辑再判；'
                                '只有确实找不到对应（增、漏、换义）才判是。',
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                ConnectionError, OSError) as e:
            # OSError 覆盖 ConnectionError/RemoteDisconnected/SSLError 等断连族
            # （RemoteDisconnected 非 URLError 包装，漏捕时零重试直接穿透）
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError('unreachable: retry loop must return or raise')


def judge_unit(api_key, idx, source_en, translation_zh, hits):
    payload = make_payload(source_en, translation_zh, hits)
    resp = ask(api_key, payload)
    a = resp['answers']
    qp = a['quality']['probabilities']
    rec = {
        'idx': idx,
        'term': a['term_violation']['noul'],
        'semantic': a['semantic_error']['noul'],
        'p_pass': float(qp['0']),
        'p_fail': float(qp['2']),
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
    ap.add_argument('--waivers', default=None,
                    help='豁免文件路径（缺省 .work/<plugin>/contracts/semgate-waivers.json）')
    ap.add_argument('--waive', action='append', default=[],
                    metavar='IDX:理由', help='登记豁免（可重复）；从 --result 取该 idx 当前译文算哈希')
    ap.add_argument('--revoke', action='append', default=[], metavar='IDX',
                    help='撤销豁免（可重复）')
    a = ap.parse_args()

    result = json.load(open(a.result, encoding='utf-8'))
    cur = {}  # idx -> 当前译文（豁免哈希绑定用）
    if isinstance(result, dict) and isinstance(result.get('translations'), list):
        for v in result['translations']:
            if isinstance(v, dict) and str(v.get('xml_index', '')).isdigit():
                cur[str(v['xml_index'])] = v.get('translation') or ''
    elif isinstance(result, dict):
        for k, v in result.items():
            if str(k).isdigit() and isinstance(v, dict):
                cur[str(k)] = v.get('translation') or ''

    waivers_path = a.waivers or default_waivers_path(a.result)

    # 豁免管理子命令：登记/撤销后直接退出，不调 API
    if a.waive or a.revoke:
        waivers = load_waivers(waivers_path)
        for spec in a.waive:
            m = re.match(r'^(\d+)\s*[:：]\s*(.+)$', spec.strip())
            if not m:
                print('用法错误：--waive 需 "<idx>:<理由>"，got: %s' % spec, file=sys.stderr)
                return 2
            idx, reason = m.group(1), m.group(2).strip()
            tr = cur.get(idx)
            if not tr:
                print('用法错误：--result 里找不到 idx %s 的译文（先备好译文再登记豁免）'
                      % idx, file=sys.stderr)
                return 2
            waivers[idx] = {'hash': _tr_hash(tr), 'reason': reason,
                            'batch': a.batch, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
            print('WAIVED %s hash=%s 理由=%s' % (idx, waivers[idx]['hash'], reason))
        for idx in a.revoke:
            idx = idx.strip()
            if waivers.pop(idx, None) is not None:
                print('REVOKED %s' % idx)
            else:
                print('未找到豁免：%s' % idx)
        save_waivers(waivers_path, waivers)
        print('waivers -> %s (%d 条)' % (waivers_path, len(waivers)))
        return 0

    api_key = os.environ.get('TYPESAFE_API_KEY', '').strip()
    if not api_key:
        print(json.dumps({'verdict': 'UNCHECKED', 'units_checked': 0,
                          'fail_count': 0, 'warning_count': 0}, ensure_ascii=False))
        print('SEMGATE_UNCHECKED: TYPESAFE_API_KEY 未设置，本批语义层未检查'
              '（机械层结论不受影响，但不得当作已过语义门）')
        return 0

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
            units.append((k, src, tr, _tr_hash(tr)))

    fails, warnings, records = [], [], []
    waived, stale = [], []
    errors = []
    waivers = load_waivers(waivers_path)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {}
        for k, src, tr, trh in units:
            hits = match_terms(src, terms)
            futures[ex.submit(judge_unit, api_key, k, src, tr, hits)] = (k, trh)
        for fut in futures:
            k, trh = futures[fut]
            try:
                rec = fut.result()
                w = waivers.get(str(k))
                if w and w.get('hash') == trh:
                    # 有效豁免：FAIL 降级为 WAIVED，不拦截；原始判定保留在
                    # rec['verdicts']（基线哨兵比对不受影响），豁免留痕入报告。
                    rec['waiver'] = {'reason': w.get('reason', ''), 'hash': trh,
                                     'granted_at': w.get('at', ''), 'batch_granted': w.get('batch', '')}
                    for level, code, detail in rec['verdicts']:
                        if level == 'FAIL':
                            waived.append({'where': k, 'code': code, 'detail': detail,
                                           'reason': w.get('reason', '')})
                elif w:
                    # 译文已变，旧豁免失效：仍拦，附注提醒重裁
                    stale.append(k)
                    rec['waiver_stale'] = True
                records.append(rec)
                active_waiver = bool(w and w.get('hash') == trh)
                for level, code, detail in rec['verdicts']:
                    if level == 'FAIL' and active_waiver:
                        continue  # 已由 WAIVED 留痕，不进 fails
                    (fails if level == 'FAIL' else warnings).append(
                        {'where': k, 'code': code, 'detail': detail})
            except Exception as e:  # noqa: BLE001
                # 调用失败即本批未完整判定：记入 errors，最终 verdict 降为 PARTIAL。
                # 不当作普通 warning——那会让「没查到」和「查过没问题」长得一样。
                errors.append('%s(%s: %s)' % (k, type(e).__name__, str(e)[:80]))

    records.sort(key=lambda r: int(r['idx']))
    if fails:
        verdict = 'FAIL'
    elif errors:
        verdict = 'PARTIAL'
    else:
        verdict = 'PASS'
    print(json.dumps({'verdict': verdict, 'units_checked': len(records),
                      'unjudged_count': len(errors),
                      'fail_count': len(fails), 'warning_count': len(warnings),
                      'waived_count': len(waived)},
                     ensure_ascii=False))
    for f in fails[:20]:
        suffix = ' [WAIVER_STALE：译文已变，旧豁免失效，需重裁]' if f['where'] in stale else ''
        print('FAIL', f['where'], f['code'], f['detail'][:160] + suffix)
    for wv in waived[:20]:
        print('WAIVED', wv['where'], wv['code'], '理由=%s' % wv['reason'][:120])
    for w in warnings[:30]:
        print('WARN', w['where'], w['code'], w['detail'][:160])
    if errors:
        print('UNJUDGED %d 条未判定（调用失败，本批语义层未完成）: %s'
              % (len(errors), errors[:8]))

    rep = a.report
    if not rep:
        plugin = _plugin_from_result(a.result)
        rep = os.path.join('.work', plugin, 'reports', '%s-semgate-report.json' % a.batch)
    json.dump({'batch': a.batch, 'verdict': verdict,
               'units_checked': len(records), 'unjudged': errors,
               'fails': fails, 'warnings': warnings,
               'waived': waived,
               'waivers_file': waivers_path if waivers else None,
               'records': records},
              open(rep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('semgate report -> %s' % rep)
    return 1 if (fails or errors) else 0


if __name__ == '__main__':
    sys.exit(main())
