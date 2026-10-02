# -*- coding: utf-8 -*-
r"""把批次侧产物对齐到 canonical：map.json / map-part-*.json / translation.json。

为什么需要这一步：收敛只改 map.json，而 map.json 是 map-part-*.json 的合并产物。
任何一次重跑 consume_batch（分片重合并）都会用未收敛的分片把 map.json 覆盖回去，
收敛成果当场蒸发。canonical 不受影响（写回早已完成），但批次侧从此与盘面脱节，
下一次按批次判组、审查、收口都会读到错的东西。

所以：写回完成后，把该批所有 idx 的译文在
  · map.json
  · map-part-*.json（收敛成果必须落到分片，否则下一次合并会再冲掉）
  · translation.json（translations[] 里的 translation 字段）
三处统一成 canonical 的现值。默认先 --check 报差异，确认后再 --apply。

用法：
  py -3 sync_batch_artifacts.py --stem <STEM> --batch <B>            # 只报差异
  py -3 sync_batch_artifacts.py --stem <STEM> --batch <B> --apply    # 落盘
  py -3 sync_batch_artifacts.py --stem <STEM> --scan                # 扫全库，一次解析列出所有脱节批次
"""
import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def load(p):
    with io.open(p, encoding='utf-8') as f:
        return json.load(f)


def save(p, obj):
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
    assert load(p) == obj, '读回不一致：%s' % p


def canon_dests(stem):
    """idx -> 已译译文；未译行返回 None。

    必须拿 Source 一起比：xTranslator 源 XML 的 <Dest> 里放的是英文原文，
    只看 Dest 非空会把「还没写回的行」当成有译文，产生一片假阳性。
    """
    base = Path('mods') / (stem + '.esp')
    out = []
    for s in ET.parse(base / (stem + '_english_chinese_translated.xml')).getroot().iter('String'):
        src = (s.findtext('Source') or '').strip()
        dst = (s.findtext('Dest') or '').strip()
        out.append(dst if (dst and dst != src) else None)
    return out


def scan(stem, apply=False):
    """一次解析 canonical，列出所有批次产物与盘面脱节的情况。"""
    dest = canon_dests(stem)
    broot = Path('.work') / stem / 'batches'
    drifted, total = [], 0
    for bdir in sorted(x for x in broot.iterdir() if x.is_dir()):
        idxf = bdir / 'index.txt'
        if not idxf.is_file():
            continue
        idx = {int(x) for x in idxf.read_text(encoding='utf-8').split() if x.strip()}
        want = {str(i): dest[i] for i in idx if 0 <= i < len(dest) and dest[i]}
        if not want:
            continue
        files, rows = [], []
        mf = bdir / 'map.json'
        if mf.is_file():
            mp = load(mf)
            bad = [k for k, v in want.items() if k in mp and (mp[k] or {}).get('translation') != v]
            if bad:
                files.append('map.json(%d)' % len(bad))
                rows.append((mf, mp, bad))
        for pf in sorted(bdir.glob('map-part-*.json')):
            mp = load(pf)
            bad = [k for k, v in want.items() if k in mp and (mp[k] or {}).get('translation') != v]
            if bad:
                files.append('%s(%d)' % (pf.name, len(bad)))
                rows.append((pf, mp, bad))
        tf = bdir / 'translation.json'
        if tf.is_file():
            tj = load(tf)
            bad = []
            for r in tj.get('translations') or []:
                if isinstance(r, dict):
                    k = str(r.get('xml_index'))
                    if k in want and (r.get('translation') or '') != want[k]:
                        bad.append(k)
            if bad:
                files.append('translation.json(%d)' % len(bad))
                rows.append((tf, 'TRANSLATIONS', bad))
        if files:
            drifted.append(bdir.name)
            total += sum(len(x[2]) for x in rows)
            print('%-12s %s' % (bdir.name, ' '.join(files)))
            if apply:
                for p, obj, bad in rows:
                    if obj == 'TRANSLATIONS':
                        o = load(p)
                        for r in o.get('translations') or []:
                            if isinstance(r, dict) and str(r.get('xml_index')) in want:
                                r['translation'] = want[str(r.get('xml_index'))]
                        save(p, o)
                    else:
                        for k in bad:
                            if k in obj:
                                obj[k]['translation'] = want[k]
                        save(p, obj)
    print('\n脱节批次 %d 个，共 %d 行%s：%s'
          % (len(drifted), total, '（已同步）' if apply else '',
             ' '.join(drifted) if drifted else '（无）'))
    return drifted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stem', required=True)
    ap.add_argument('--batch')
    ap.add_argument('--scan', action='store_true', help='扫全库，一次解析')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    if a.scan and not a.batch:
        scan(a.stem, apply=a.apply)
        return 0
    if not a.batch:
        print('!! 需要 --batch，或用 --scan 扫全库')
        return 1
    bdir = Path('.work') / a.stem / 'batches' / a.batch

    idxf = bdir / 'index.txt'
    if not idxf.is_file():
        print('!! 缺批次索引：%s' % idxf)
        return 1
    idx = {int(x) for x in idxf.read_text(encoding='utf-8').split() if x.strip()}
    dest = canon_dests(a.stem)

    want = {}
    for i in idx:
        if 0 <= i < len(dest) and dest[i]:
            want[str(i)] = dest[i]
    print('%s：index %d，canonical 已译 %d' % (a.batch, len(idx), len(want)))

    diffs = {}

    mf = bdir / 'map.json'
    if mf.is_file():
        mp = load(mf)
        bad = [k for k, v in want.items() if k in mp and (mp[k] or {}).get('translation') != v]
        if bad:
            diffs['map.json'] = bad
            print('map.json：%d 行与 canonical 不一致 %s' % (len(bad), bad[:8]))

    for pf in sorted(bdir.glob('map-part-*.json')):
        mp = load(pf)
        bad = [k for k, v in want.items() if k in mp and (mp[k] or {}).get('translation') != v]
        if bad:
            diffs[pf.name] = bad
            print('%s：%d 行与 canonical 不一致 %s' % (pf.name, len(bad), bad[:8]))

    tf = bdir / 'translation.json'
    if tf.is_file():
        tj = load(tf)
        rows = tj.get('translations') or []
        bad = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            k = str(r.get('xml_index'))
            if k in want and (r.get('translation') or '') != want[k]:
                bad.append(k)
        if bad:
            diffs['translation.json'] = bad
            print('translation.json：%d 行与 canonical 不一致 %s' % (len(bad), bad[:8]))

    if not diffs:
        print('批次侧产物与 canonical 完全一致，无需处理。')
        return 0
    if not a.apply:
        print('\n以上共 %d 个文件有差异。确认后加 --apply 落盘。' % len(diffs))
        return 1

    for name, bad in diffs.items():
        p = bdir / name
        obj = load(p)
        if name == 'translation.json':
            for r in obj.get('translations') or []:
                if isinstance(r, dict):
                    k = str(r.get('xml_index'))
                    if k in want:
                        r['translation'] = want[k]
        else:
            for k in bad:
                if k in obj:
                    obj[k]['translation'] = want[k]
        save(p, obj)
        print('已同步 %s（%d 行）' % (name, len(bad)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
