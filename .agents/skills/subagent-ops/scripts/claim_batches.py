# -*- coding: utf-8 -*-
r"""claim_batches.py — 多会话/多线并发推进同一 MOD 时的批次领地台账。

并发治理的最小机械层：
  - 每个会话开工前把要翻的批次段「领走」，台账落 `.work/<stem>/context/<stem>-lines.json`
  - 认领时机械校验两件事，任一不过即拒绝：
      a) 批次未被其他 owner 认领（不重复派工）
      b) 认领批次的源句集合与其他 owner 已认领批次的源句集合无交集（防跨线同源分裂）
  - 台账写入用 O_EXCL 锁保护，防两个会话同时改台账

设计约束（AGENTS.md §2.1 并发条款的执行层）：
  - 锁 canonical 已有 pipeline.lock；本脚本不重复那个功能，只管「工作面分配」
  - 只读计划文件的 srcs 字段，不碰 canonical、不碰批次产物
  - owner 标识由调用方自定（建议：会话名 + 日期，如 "hanako-20261015"）

用法：
  # 认领批次段（连续区间）
  py -3 claim_batches.py --stem <S> --owner <NAME> --range INFO-001 INFO-050

  # 认领离散批次
  py -3 claim_batches.py --stem <S> --owner <NAME> --batches INFO-001 INFO-003 NI-CELL-001

  # 只校验不认领（试算冲突）
  py -3 claim_batches.py --stem <S> --owner <NAME> --batches ... --check

  # 查看台账
  py -3 claim_batches.py --stem <S> --list

  # 释放自己名下的批次
  py -3 claim_batches.py --stem <S> --owner <NAME> --release [--batches ...]

  # 指定计划文件（非 INFO 批次用 noninfo 计划）
  py -3 claim_batches.py --stem <S> --plan .work/<S>/context/<S>-noninfo-batches.json ...

退出码：0 成功 / 1 冲突或参数错 / 2 锁占用。
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

WORK = Path('.work')


def _lock_path(work: Path) -> Path:
    return work / 'context' / 'lines.lock'


def _acquire_lock(work: Path, timeout: float = 5.0):
    """O_EXCL 台账锁；超时返回 None。"""
    lp = _lock_path(work)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({'pid': os.getpid()}).encode())
            os.close(fd)
            return lp
        except FileExistsError:
            if time.time() > deadline:
                return None
            time.sleep(0.1)


def _release_lock(lp: Path) -> None:
    try:
        lp.unlink()
    except OSError:
        pass


def _load_plan(plan_path: Path):
    """读批次计划 -> {batch_id: set(srcs)}"""
    data = json.loads(plan_path.read_text(encoding='utf-8'))
    out = {}
    for b in data.get('batches', []):
        bid = b['id']
        srcs = set(s for s in b.get('srcs', []) if s)
        out[bid] = srcs
    return out


def _expand_range(plan_ids: list[str], lo: str, hi: str) -> list[str]:
    """--range A B：按计划文件内的顺序取 A..B 闭区间。"""
    try:
        i, j = plan_ids.index(lo), plan_ids.index(hi)
    except ValueError as e:
        raise SystemExit(f'error: 批次 {e} 不在计划里')
    if i > j:
        i, j = j, i
    return plan_ids[i:j + 1]


def _load_ledger(ledger_path: Path) -> dict:
    if not ledger_path.is_file():
        return {'schema': 1, 'claims': {}}
    return json.loads(ledger_path.read_text(encoding='utf-8'))


def _save_ledger(ledger_path: Path, data: dict) -> None:
    ledger_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')


def cmd_list(args) -> int:
    work = WORK / args.stem
    ledger_path = work / 'context' / f'{args.stem}-lines.json'
    ledger = _load_ledger(ledger_path)
    claims = ledger.get('claims', {})
    if not claims:
        print('(empty ledger)')
        return 0
    for owner, bids in sorted(claims.items()):
        print(f'{owner}: {len(bids)} batches')
        for b in sorted(bids):
            print(f'  {b}')
    return 0


def cmd_claim(args) -> int:
    work = WORK / args.stem
    plan_path = Path(args.plan) if args.plan else (
        work / 'context' / f'{args.stem}-info-batches.json')
    if not plan_path.is_file():
        print(f'error: 计划文件不存在: {plan_path}', file=sys.stderr)
        return 1
    plan = _load_plan(plan_path)
    plan_ids = list(plan.keys())

    if args.range:
        bids = _expand_range(plan_ids, args.range[0], args.range[1])
    else:
        bids = list(args.batches or [])
    if not bids:
        print('error: 未指定批次（--batches 或 --range）', file=sys.stderr)
        return 1
    for b in bids:
        if b not in plan:
            print(f'error: 批次 {b} 不在计划 {plan_path.name} 里', file=sys.stderr)
            return 1

    ledger_path = work / 'context' / f'{args.stem}-lines.json'
    lp = _acquire_lock(work)
    if lp is None:
        print('error: 台账锁占用（另一会话正在操作），稍后重试', file=sys.stderr)
        return 2
    try:
        ledger = _load_ledger(ledger_path)
        claims = ledger.setdefault('claims', {})

        # 校验 a：批次未被他人认领
        taken = {}
        for owner, owned in claims.items():
            if owner == args.owner:
                continue
            for b in owned:
                taken[b] = owner
        dup = [b for b in bids if b in taken]
        if dup:
            for b in dup:
                print(f'conflict: {b} 已被 {taken[b]} 认领', file=sys.stderr)
            return 1

        # 校验 b：源句集合不相交
        my_srcs = set()
        for b in bids:
            my_srcs |= plan[b]
        for owner, owned in claims.items():
            if owner == args.owner:
                continue
            other_srcs = set()
            for b in owned:
                if b in plan:
                    other_srcs |= plan[b]
            inter = my_srcs & other_srcs
            if inter:
                print(f'conflict: 与 {owner} 的已认领批次共享 {len(inter)} 条源句：',
                      file=sys.stderr)
                for s in sorted(inter)[:10]:
                    print(f'  {s[:90]}', file=sys.stderr)
                print('  → 同源句跨线会分裂译形；换内容不相交的批，或先收敛再认领',
                      file=sys.stderr)
                return 1

        if args.check:
            print(f'OK: {len(bids)} 批可认领（仅校验，未写台账）')
            return 0

        claims.setdefault(args.owner, [])
        for b in bids:
            if b not in claims[args.owner]:
                claims[args.owner].append(b)
        _save_ledger(ledger_path, ledger)
        print(f'claimed {len(bids)} batches -> owner={args.owner}')
        print(f'ledger: {ledger_path}')
        return 0
    finally:
        _release_lock(lp)


def cmd_release(args) -> int:
    work = WORK / args.stem
    ledger_path = work / 'context' / f'{args.stem}-lines.json'
    lp = _acquire_lock(work)
    if lp is None:
        print('error: 台账锁占用', file=sys.stderr)
        return 2
    try:
        ledger = _load_ledger(ledger_path)
        claims = ledger.get('claims', {})
        mine = claims.get(args.owner, [])
        if not mine:
            print(f'error: {args.owner} 名下无批次', file=sys.stderr)
            return 1
        if args.batches:
            drop = set(args.batches)
            claims[args.owner] = [b for b in mine if b not in drop]
            print(f'released {len(mine) - len(claims[args.owner])} batches')
        else:
            del claims[args.owner]
            print(f'released all {len(mine)} batches of {args.owner}')
        _save_ledger(ledger_path, ledger)
        return 0
    finally:
        _release_lock(lp)


def main() -> int:
    ap = argparse.ArgumentParser(description='批次领地台账：多线并发的工作面分配与同源校验')
    ap.add_argument('--stem', required=True)
    ap.add_argument('--owner', help='会话标识（认领/释放必填）')
    ap.add_argument('--plan', help='批次计划 JSON 路径（默认取 info 计划）')
    ap.add_argument('--batches', nargs='*', help='离散批次号')
    ap.add_argument('--range', nargs=2, metavar=('LO', 'HI'), help='连续批次区间')
    ap.add_argument('--check', action='store_true', help='只校验不认领')
    ap.add_argument('--list', action='store_true', help='查看台账')
    ap.add_argument('--release', action='store_true', help='释放名下批次')
    args = ap.parse_args()

    if args.list:
        return cmd_list(args)
    if args.release:
        if not args.owner:
            print('error: --release 需要 --owner', file=sys.stderr)
            return 1
        return cmd_release(args)
    if not args.owner:
        print('error: 认领需要 --owner', file=sys.stderr)
        return 1
    return cmd_claim(args)


if __name__ == '__main__':
    sys.exit(main())
