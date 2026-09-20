# -*- coding: utf-8 -*-
"""normalize_charset.py — 将译文中的非简体字符规范化为简体形式（CHAR001 同源）。

背景：translation-quality-gate 的 CHAR001 检测「dest 经 zh-cn 转换表
（zh2Hans + zh2CN）后发生变化的字符」（如「」→“”、繁体字、台港用词），
但它只报不改（"Detection != conversion"）。本工具复用同一 vendored 表
（zh_cn_conv.json）与同一算法（quality_gate._zh_convert_cn，运行期动态加载，
保证与门禁零漂移），生成确定性的修正值。

关键保真点：CHAR001 对「已简化字符的上下文误报」（R11，如「什么正」中的
「么」被表误转「幺」）在报告层是过滤掉的；本工具的替换必须同样过滤——
只替换「真实差异」字符位，绝不把正确简体改成表怪癖形态。

模式：
  --result <translation.json>   批次结果（可重复）
  --xml <canonical.xml>         已写回 canonical（只读，0-based String 序号全库扫描）
  --fixes-out <path>            输出 apply_fixes 兼容的 fixes JSON：
                                { "<idx>": {"new": "<全量新值>", "notes": "charset normalize"} }

退出码：0 = 完成且无差异；1 = 完成且发现差异（--fixes-out 同样以 1 表示
「有差异、fixes 已生成」）；2 = 用法/读入错误。

安全边界：不修改任何输入文件；只写 --fixes-out 指定路径。长度不等的转换
（罕见）不做自动替换，列为 manual review。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
QG_DIR = HERE.parents[1] / 'translation-quality-gate' / 'scripts'
QG_PATH = QG_DIR / 'quality_gate.py'


def load_gate_module():
    """动态加载 quality_gate 模块（CHAR001 的权威实现），零漂移复用。"""
    if str(QG_DIR) not in sys.path:
        sys.path.insert(0, str(QG_DIR))
    spec = importlib.util.spec_from_file_location('_qg_for_charset', QG_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {QG_PATH}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def normalize_value(dest: str, qg) -> tuple[str, list, list]:
    """Return (new_value, changed_pairs, manual_reasons).

    changed_pairs: [(old_char, new_char), ...] —— 仅含真实差异（R11 误报已过滤）。
    manual_reasons: 需要人工处理的原因列表（如长度不等）。
    """
    if not dest:
        return dest, [], []
    conv = qg._zh_convert_cn(dest)
    if conv == dest:
        return dest, [], []
    if len(conv) != len(dest):
        return dest, [], ['length-differs(convert result length != source length)']
    out = list(dest)
    pairs = []
    for i, (a, b) in enumerate(zip(dest, conv)):
        if a == b:
            continue
        # R11：a 自身已是简体（单字转换不变）→ 属上下文误报，保留原字符
        if qg._CONV_DATA.get(a, a) == a:
            continue
        out[i] = b
        pairs.append((a, b))
    new = ''.join(out)
    if new == dest:
        return dest, [], []  # 全部差异均为误报过滤后的等值结果
    # 自校验：修正后不应再被 CHAR001 判为有问题
    if qg.charset_issue(new):
        return dest, [], ['post-check-still-dirty(manual review)']
    return new, pairs, []


def scan_result(path: Path, qg):
    data = json.loads(path.read_text(encoding='utf-8'))
    units = data.get('translations') or []
    diffs, manuals = [], []
    for u in units:
        idx = u.get('xml_index')
        dst = u.get('translation') or ''
        new, pairs, manual = normalize_value(dst, qg)
        if manual:
            manuals.append((idx, manual[0], dst))
        elif pairs:
            diffs.append((idx, pairs, dst, new))
    return diffs, manuals


def scan_xml(path: Path, qg):
    tree = ET.parse(str(path))
    diffs, manuals = [], []
    for i, node in enumerate(tree.getroot().findall('.//String')):
        dest_node = node.find('Dest')
        dst = dest_node.text if dest_node is not None and dest_node.text else ''
        if not dst.strip():
            continue
        new, pairs, manual = normalize_value(dst, qg)
        if manual:
            manuals.append((i, manual[0], dst))
        elif pairs:
            diffs.append((i, pairs, dst, new))
    return diffs, manuals


def fmt_pairs(pairs):
    return ', '.join(f'{a!r}->{b!r}' for a, b in pairs[:12]) + (' …' if len(pairs) > 12 else '')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--result', action='append', default=[], help='translation-result JSON（可重复）')
    ap.add_argument('--xml', default=None, help='canonical xTranslator XML（只读全库扫描）')
    ap.add_argument('--fixes-out', default=None, help='输出 apply_fixes 兼容的 fixes JSON')
    ap.add_argument('--limit', type=int, default=0, help='最多列出 N 处差异（0=全部）')
    args = ap.parse_args()
    if not args.result and not args.xml:
        ap.error('provide --result and/or --xml')

    qg = load_gate_module()
    all_diffs, all_manuals = [], []
    for r in args.result:
        p = Path(r)
        if not p.is_file():
            print(f'error: not found: {p}', file=sys.stderr)
            return 2
        d, m = scan_result(p, qg)
        print(f'== {p}')
        for idx, pairs, old, new in d[: (args.limit or None)]:
            print(f'   [{idx}] {fmt_pairs(pairs)}')
        for idx, reason, old in m:
            print(f'   [{idx}] MANUAL: {reason}')
        print(f'   差异 {len(d)} 处，manual {len(m)} 处')
        all_diffs += [(p.name, x) for x in d]
        all_manuals += [(p.name, x) for x in m]

    if args.xml:
        p = Path(args.xml)
        if not p.is_file():
            print(f'error: not found: {p}', file=sys.stderr)
            return 2
        d, m = scan_xml(p, qg)
        print(f'== {p}')
        for idx, pairs, old, new in d[: (args.limit or None)]:
            print(f'   [xml-index:{idx}] {fmt_pairs(pairs)}')
        for idx, reason, old in m:
            print(f'   [xml-index:{idx}] MANUAL: {reason}')
        print(f'   差异 {len(d)} 处，manual {len(m)} 处')
        all_diffs += [(p.name, x) for x in d]
        all_manuals += [(p.name, x) for x in m]

    if args.fixes_out:
        fixes = {}
        for src, (idx, pairs, old, new) in all_diffs:
            fixes[str(idx)] = {'new': new, 'notes': 'charset normalize'}
        Path(args.fixes_out).write_text(
            json.dumps(fixes, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'fixes: {len(fixes)} 条 -> {args.fixes_out}')

    total = len(all_diffs)
    print(f'总计：差异 {total} 处，manual {len(all_manuals)} 处')
    return 1 if (total or all_manuals) else 0


if __name__ == '__main__':
    raise SystemExit(main())
