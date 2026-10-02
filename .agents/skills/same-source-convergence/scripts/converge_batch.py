# -*- coding: utf-8 -*-
r"""表驱动的同源整族收敛：裁决表放 JSON，脚本本身是通用资产。

为什么表要外置：裁决表里装的是判断（每组取哪个形、为什么），判断是一次性的；
把它写死在 .py 里就等于每个批次产出一个注定被丢掉的脚本。表外置之后，
本脚本可以被每一轮、每一个 MOD 复用。

裁决表 JSON 结构：
{
  "batch": "INFO-595",
  "table": { "<英文源句>": "<裁决形>", ... },
  "rationale": { "<英文源句>": "<为什么这么裁，可选>" },
  "exempt_prompt_fix": { "<已登记豁免的源句>": "<目标形>" }   // 可选
}

用法：
  py -3 converge_batch.py --stem <STEM> --table <table.json>            # 预检：列多形组、缺表即中止、核契约
  py -3 converge_batch.py --stem <STEM> --table <table.json> --apply    # 改写 map.json
  py -3 converge_batch.py --stem <STEM> --table <table.json> --verify   # 复验剩余组

关键护栏（每一条都对应过一次真实事故）：
  1. 未在表里裁决的多形组 -> 预检直接中止，防止漏判。
  2. 契约 enforcement=REQUIRED 的词条若命中源句、而裁决形里没有 match.accepted 任一形 ->
     报 CONTRACT_MISSING。默认只告警，--strict 升级为中止。
     （义位分立会造成误报，所以不给它默认的否决权；但它必须响，
       因为「按批内多数收敛」压过「契约 target」这类事故已经发生过多次。）
  3. 裁决形命中契约 forbidden 形 -> 报 CONTRACT_FORBIDDEN，一律中止。
  4. 待改的 idx 若不属于本批 -> 中止，提示先走 close_round（批已写回的情形）。
  5. 已登记的同源豁免源句按 round_pipeline 口径跳过；但豁免的前提是 prompt 不同，
     同一源句内「前一行源文相同却译得不同」属同 prompt 漂移，用 exempt_prompt_fix 收。
"""
import argparse
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

BATCHOPS = Path('.agents/skills/translation-batch-ops/scripts')
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")

# 英文常见屈折后缀。契约词条写的是词元（harvest / maker / curse），
# 源句里出现的是屈折形（harvests / makers / curses）。不做还原的话，
# 契约核对会**静默漏检**——那正是最危险的一种漏：看起来检查过了，其实没生效。
# 这里只剥离常见后缀，不引入词干分析器：可预测、够用、不会把无关词误并进来。
SUFFIXES = ("'s", "s", "es", "ed", "ing", "'", "en")


def stem_candidates(word):
    """一个源词的全部可匹配候选（本身 + 逐个剥常见后缀）。"""
    w = word.lower()
    out = [w]
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            out.append(w[: -len(suf)])
    # doubled consonant + ing/ed：running -> run、stopped -> stop
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)]
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                out.append(stem[:-1])
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def warn(msg):
    print('!! ' + msg, file=sys.stderr)


def load_exemptions(work, stem):
    p = work / 'contracts' / (stem + '-same-source-exemptions.json')
    if not p.is_file():
        return set()
    if str(BATCHOPS.resolve()) not in sys.path:
        sys.path.insert(0, str(BATCHOPS.resolve()))
    try:
        from close_round import load_exemptions as _le
        return set(_le(p))
    except Exception:
        data = json.loads(p.read_text(encoding='utf-8'))
        ex = data.get('exemptions', data)
        return set(ex.keys()) if isinstance(ex, dict) else set(ex)


def load_contract(work, stem):
    p = work / 'contracts' / (stem + '.compiled.json')
    if not p.is_file():
        return None
    return (json.loads(p.read_text(encoding='utf-8')) or {}).get('terms') or {}


def contract_index(terms):
    """英文源词（小写）-> 词条列表。terms 可以是 dict（按 term_id 键）也可以是 list。

    编译契约的 terms 字段在不同版本里两种形态都出现过（dict 与 list），
    所以这里两种都收，不要假设只有一种。
    """
    if isinstance(terms, dict):
        terms = list(terms.values())
    idx = defaultdict(list)
    for t in (terms or []):
        if not isinstance(t, dict):
            continue
        src = (t.get('source') or '').strip().lower()
        if src:
            idx[src].append(t)
    return idx


def check_contract(src_en, target, cidx, out, strict):
    """核对裁决形与契约。返回 True 表示有硬失败。

    源词匹配走屈折还原（见 stem_candidates）：契约写 harvest，源句写 harvests，
    两边要能对上，否则检查形同虚设。
    """
    hard = False
    matched = {}
    for w in WORD.findall(src_en):
        for cand in stem_candidates(w):
            for t in cidx.get(cand, []):
                matched[t.get('term_id')] = (w, t)
    for tid, (w, t) in sorted(matched.items()):
        forb = [f for f in (t.get('forbidden') or []) if f and f in target]
        if forb:
            out.append('  CONTRACT_FORBIDDEN [%s] 源句含 %r，裁决形 %r 命中禁形 %s'
                       % (tid, w, target, forb))
            hard = True
            continue
        if (t.get('enforcement') or '').upper() != 'REQUIRED':
            continue
        acc = ((t.get('match') or {}).get('accepted')) or ([t['target']] if t.get('target') else [])
        acc = [x for x in acc if x]
        if acc and not any(x in target for x in acc):
            out.append('  CONTRACT_MISSING [%s] 源句含 %r（enforcement=REQUIRED，target=%r），'
                       '但裁决形 %r 里没有 %s'
                       % (tid, w, t.get('target'), target, '/'.join(acc)))
            if strict:
                hard = True
    return hard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--table', required=True, help='裁决表 JSON')
    ap.add_argument('--apply', action='store_true', help='改写 map.json')
    ap.add_argument('--verify', action='store_true', help='只复验剩余多形组')
    ap.add_argument('--strict', action='store_true', help='把 CONTRACT_MISSING 升级为中止')
    ap.add_argument('--allow-cross-batch', action='store_true',
                    help='允许改到本批以外的 idx（需要先走 close_round，一般不要开）')
    a = ap.parse_args()

    spec = json.loads(Path(a.table).read_text(encoding='utf-8'))
    batch = spec['batch']
    T = spec.get('table') or {}
    PF = spec.get('exempt_prompt_fix') or {}

    work = Path('.work') / a.stem
    bdir = work / 'batches' / batch
    mf = bdir / 'map.json'
    if not mf.is_file():
        warn('%s 无 map.json——整批会在写回前 STOP，先让子代理交卷' % mf)
        return 1
    base = Path('mods') / (a.stem + '.esp')
    srcxml = base / (a.stem + '_english_chinese.xml')
    canon = base / (a.stem + '_english_chinese_translated.xml')
    srcs = [s.findtext('Source') or '' for s in ET.parse(srcxml).getroot().iter('String')]

    exempt = load_exemptions(work, a.stem)
    cidx = contract_index(load_contract(work, a.stem))

    canon_forms = defaultdict(lambda: defaultdict(list))
    for i, s in enumerate(ET.parse(canon).getroot().iter('String')):
        s_, d_ = s.findtext('Source') or '', s.findtext('Dest') or ''
        if s_ and d_ and d_ != s_:
            canon_forms[s_][d_].append(i)

    owner = {}
    for p in (work / 'batches').glob('*/index.txt'):
        for x in p.read_text(encoding='utf-8').split():
            if x.strip():
                owner.setdefault(int(x), p.parent.name)

    mp = json.loads(mf.read_text(encoding='utf-8'))
    rows = defaultdict(list)
    for k, v in mp.items():
        rows[srcs[int(k)] if int(k) < len(srcs) else ''].append(
            (k, str((v or {}).get('translation') or '').strip()))

    def multiform():
        out = []
        for s, rs in rows.items():
            if s in exempt:
                continue
            if len(set(canon_forms.get(s, {})) | {t for _, t in rs}) > 1:
                out.append((s, rs))
        return out

    groups = multiform()

    if a.verify:
        print('剩余同源异形组 %d（豁免源句 %d 个已跳过）' % (len(groups), len(exempt)))
        for s, rs in groups:
            print('  !! %s\n     canonical: %s\n     %s: %s'
                  % (s[:70], sorted(canon_forms.get(s, {})), batch, sorted({t for _, t in rs})))
        return 0 if not groups else 1

    print('批次 %s：多形组 %d，裁决表 %d 条，豁免源句 %d 个'
          % (batch, len(groups), len(T), len(exempt)))
    missing = [s for s, _ in groups if s not in T]
    if missing:
        warn('有 %d 个多形组未裁决（脚本中止，防漏判）：' % len(missing))
        for s in missing[:10]:
            print('     %s' % s[:110])
        return 1
    extra = [s for s in T if s not in {x for x, _ in groups}]
    if extra:
        warn('有 %d 条裁决项不在多形组里（可能已收敛，请确认表没写错）：' % len(extra))
        for s in extra[:5]:
            print('     %s' % s[:110])

    if not a.apply:
        print('\n-- 契约核对（预检；加 --apply 才会改写）--')
        hard = 0
        for s in groups:
            tgt = T[s]
            msgs = []
            if check_contract(s, tgt, cidx, msgs, a.strict):
                hard += 1
            for m in msgs:
                print(m)
        print('契约硬失败 %d 条' % hard)
        if hard:
            return 1
        print('\n预检通过。确认裁决无误后加 --apply。')
        return 0

    msgs_all, hard = [], 0
    for s in groups:
        msgs = []
        if check_contract(s, T[s], cidx, msgs, a.strict):
            hard += 1
        msgs_all.extend(msgs)
    for m in msgs_all:
        print(m)
    if hard:
        warn('契约硬失败 %d 条，中止（--strict 已开）' % hard)
        return 1

    changed, pf_changed, outside = 0, 0, []
    shards = sorted(bdir.glob('map-part-*.json'))
    for s, rs in groups:
        tgt = T[s]
        for k, old in rs:
            if old == tgt:
                continue
            if owner.get(int(k)) != batch and not a.allow_cross_batch:
                outside.append((k, owner.get(int(k))))
                continue
            mp[k] = {'translation': tgt, 'status': 'TRANSLATED',
                     'confidence': (mp[k] or {}).get('confidence', 'MEDIUM')}
            changed += 1
    if outside:
        warn('待改 idx 有 %d 个不属于 %s，需先走 close_round：%s'
             % (len(outside), batch, outside[:8]))
        return 1

    for s, tgt in PF.items():
        for k, v in list(mp.items()):
            i = int(k)
            if i < len(srcs) and srcs[i] == s and (v or {}).get('translation') != tgt:
                mp[k] = {'translation': tgt, 'status': 'TRANSLATED',
                         'confidence': (v or {}).get('confidence', 'MEDIUM')}
                pf_changed += 1

    with io.open(mf, 'w', encoding='utf-8', newline='') as f:
        json.dump(mp, f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert json.loads(mf.read_text(encoding='utf-8')) == mp, 'map.json 读回不一致'

    # 必须一并写穿到分片文件。map.json 是 map-part-*.json 的合并产物；
    # 只改 map.json 的话，任何一次重跑 consume_batch（分片重合并）都会用未收敛的分片
    # 把收敛成果覆盖回去。实测踩过：写回后被一次重合并冲掉，批次侧从此与盘面脱节。
    sh = 0
    for sp in shards:
        obj = json.loads(sp.read_text(encoding='utf-8'))
        hit = 0
        for k, v in obj.items():
            if k in mp and (v or {}).get('translation') != mp[k]['translation']:
                obj[k] = dict(mp[k])
                hit += 1
        if hit:
            with io.open(sp, 'w', encoding='utf-8', newline='') as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
                f.write('\n')
            assert json.loads(sp.read_text(encoding='utf-8')) == obj, '%s 读回不一致' % sp
            sh += hit

    print('\n%s 改写 %d 行（%d 组）+ 豁免同 prompt %d 行；写穿分片 %d 行（%d 个文件）-> %s'
          % (batch, changed, len(groups), pf_changed, sh, len(shards), mf))

    left = multiform()
    print('剩余同源异形组 %d' % len(left))
    for s, rs in left:
        print('  !! %s\n     canonical: %s\n     %s: %s'
              % (s[:70], sorted(canon_forms.get(s, {})), batch, sorted({t for _, t in rs})))
    return 0 if not left else 1


if __name__ == '__main__':
    sys.exit(main())
