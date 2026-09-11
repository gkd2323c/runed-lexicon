#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fidelity_scan.py 回归测试（standalone 风格，直接 python 运行）。

覆盖：
  1. load_contract 返回**预编译** pattern（防回归到 re.escape 字符串 →
     每次 re.search 现场编译；该缺陷曾使全库扫描 293s，缺陷线 10 倍）
  2. check_units 禁形命中/不命中与锚点整词语义
  3. check_punctuation 终标类别与 lost/added/changed 方向
  4. 性能冒烟：合成 250 禁形 × 2500 行，断言 < 3s

登记于 tools/pre-push-check.py STANDALONE_SCRIPTS 与 ci.yml（两处同步）。
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fidelity_scan as fs  # noqa: E402


def _write_contract(path, n_bans):
    bans = []
    for i in range(n_bans):
        bans.append({
            "english": "Anchor%d" % i,
            "forbidden": ["坏形%d" % i],
            "target": "好形%d" % i,
            "reason": "test",
        })
    contract = {"terms": {}, "global_bans": bans}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False)


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="fidelity-test-")
    try:
        contract_path = os.path.join(tmp, "contract.json")
        _write_contract(contract_path, 3)
        bans = fs.load_contract(contract_path)

        # 1. 结构：pattern 必须已编译
        if not bans or not isinstance(bans[0][0], re.Pattern):
            failures.append("load_contract 未返回预编译 pattern（性能回归风险）")

        # 2. 禁形命中 / 锚点语义
        fs.BANS = bans
        units = [
            ("u1", "Anchor0 here", "这里有坏形0"),          # 命中：锚点+禁形
            ("u2", "Other text", "这里有坏形0"),             # 锚点不在源文 → 不报
            ("u3", "Anchor1 here", "这里有好形1"),           # 无禁形 → 不报
            ("u4", "Anchor2 came", "came 坏形2"),            # 命中
        ]
        fails, _ = fs.check_units(units)
        hit_ids = sorted(f["id"] for f in fails)
        if hit_ids != ["u1", "u4"]:
            failures.append("check_units 命中集错误: %r" % (hit_ids,))

        # 锚点整词：Anchor0 不应被 "XAnchor0" 命中
        if fs._anchor_hit("XAnchor0", "Anchor0"):
            failures.append("_anchor_hit 未做整词边界")
        if not fs._anchor_hit("the Anchors", "Anchor"):
            failures.append("_anchor_hit 复数形未命中")

        # 3. check_punctuation
        punits = [
            ("p1", "Talk to him.", "与他交谈"),        # lost: period -> none
            ("p2", "Talk to him.", "与他交谈。"),      # 同类 -> 不报
            ("p3", "He is here", "他在这儿。"),        # added: none -> period
            ("p4", "Talk to him.", "与他交谈？"),      # changed: period -> question
            ("p5", "Fine!", "好"),                     # lost: exclam -> none
            ("p6", "Talk to him.", "Talk to him."),    # KEEP 同行 -> 不报
        ]
        punct = fs.check_punctuation(punits)
        got = {p["id"]: p["dir"] for p in punct}
        if got != {"p1": "lost", "p3": "added", "p4": "changed", "p5": "lost"}:
            failures.append("check_punctuation 方向判定错误: %r" % (got,))
        # 4. QUST:NNAM 缺句末标点 → 机械 FAIL（裁决 A：统一保留句号）
        qunits = [
            ("q1", "Go through the portal.", "穿过传送门", "QUST:NNAM"),    # lost -> fail
            ("q2", "Go through the portal.", "穿过传送门。", "QUST:NNAM"),  # ok
            ("q3", "Talk to him.", "与他交谈", "INFO:NAM1"),               # lost 但非 QUST -> 仅报
            ("q4", "He is here", "他在这儿", "QUST:NNAM"),                 # 源文无标点 -> 不报
        ]
        qp = fs.check_punctuation(qunits)
        qby = {x["id"]: x for x in qp}
        if "q1" not in qby or qby["q1"]["dir"] != "lost" or qby["q1"]["rec"] != "QUST:NNAM":
            failures.append(f"check_punctuation QUST lost 未报: {qby.get('q1')}")
        if "q2" in qby or "q4" in qby:
            failures.append("check_punctuation 误报合规 QUST 行")
        if "q3" not in qby:
            failures.append("check_punctuation 漏报 INFO 标点差异")
        qf = fs.qust_punct_fails(qp)
        if [x["id"] for x in qf] != ["q1"]:
            failures.append(f"qust_punct_fails 筛选错误: {[x['id'] for x in qf]}")
        if fs.qust_punct_fails([qby["q3"]]) != []:
            failures.append("qust_punct_fails 未排除非 QUST 行")
        # 尾随引号应被剥离后再判类
        if fs._terminal_category("他说“好！”") != "exclam":
            failures.append("_terminal_category 未剥离尾随引号")

        # 5. 性能冒烟：250 禁形 × 2500 行
        perf_contract = os.path.join(tmp, "perf.json")
        _write_contract(perf_contract, 250)
        fs.BANS = fs.load_contract(perf_contract)
        perf_units = []
        for i in range(2500):
            perf_units.append(("x%d" % i, "Anchor%d text" % (i % 250),
                               "普通译文 %d" % i))
        t0 = time.time()
        fs.check_units(perf_units)
        dt = time.time() - t0
        if dt > 3.0:
            failures.append("性能冒烟超阈值: %.2fs (>3s，疑似现场编译回归)" % dt)
        else:
            print("perf: 250 bans x 2500 units = %.2fs" % dt)

        if failures:
            for f in failures:
                print("FAIL:", f)
            return 1
        print("fidelity_scan tests: all passed")
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
