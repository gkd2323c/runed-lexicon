# -*- coding: utf-8 -*-
"""豁免通道桩 runner：stub judge 后单次调用 semantic_gate.main()（供 test_waiver_channel 子进程调用）。"""
import importlib.util
import os
import sys

res, con, wv, rep = sys.argv[1:5]
os.environ['TYPESAFE_API_KEY'] = 'stub'
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('sg', os.path.join(HERE, 'semantic_gate.py'))
sg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sg)


def fake_judge(api_key, idx, src, tr, hits):
    return {'idx': idx, 'term': 0.1, 'semantic': 0.9, 'p_pass': 0.1, 'p_fail': 0.8,
            'issue_type': 'semantic', 'issue_conf': 0.9, 'contract_hits': [], 'usage': None,
            'verdicts': [('FAIL', 'SEMANTIC_ERROR', 'semantic=0.90 桩判定')]}


sg.judge_unit = fake_judge
sys.argv = ['sg', '--result', res, '--xml', res, '--contract', con,
            '--waivers', wv, '--batch', 'STUB', '--report', rep]
sys.exit(sg.main())
