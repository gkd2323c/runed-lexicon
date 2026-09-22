# -*- coding: utf-8 -*-
"""语义门豁免通道端到端回归测试（合成 result + 桩 judge，不调真实 API）。

场景一：101/102 都判 FAIL，101 有豁免 → 101 WAIVED、102 仍 FAIL、rc=1。
场景二：101 译文变更 → 豁免失配 → 101/102 全 FAIL、waived 空。

用法：py -3 test_waiver_channel.py
退出码：0 = 全过；1 = 断言失败。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SG = os.path.join(HERE, 'semantic_gate.py')
RUNNER = os.path.join(HERE, '_waiver_runner.py')


def main():
    with tempfile.TemporaryDirectory() as td:
        res = os.path.join(td, 'result.json')
        con = os.path.join(td, 'contract.json')
        wv = os.path.join(td, 'waivers.json')
        rep1 = os.path.join(td, 'report1.json')
        rep2 = os.path.join(td, 'report2.json')

        r = {'translations': [
            {'xml_index': 101, 'source': 'S one.', 'translation': '甲。', 'status': 'TRANSLATED'},
            {'xml_index': 102, 'source': 'S two.', 'translation': '乙。', 'status': 'TRANSLATED'},
        ]}
        io.open(con, 'w', encoding='utf-8').write('{"terms": {}}')
        io.open(res, 'w', encoding='utf-8').write(json.dumps(r, ensure_ascii=False))

        subprocess.run([sys.executable, SG, '--result', res, '--xml', con, '--contract', con,
                        '--waivers', wv, '--waive', '101:桩测试'], check=True)

        def run(rep):
            out = subprocess.run([sys.executable, RUNNER, res, con, wv, rep],
                                 capture_output=True, text=True, encoding='utf-8')
            return out.returncode

        rc1 = run(rep1)
        p1 = json.load(open(rep1, encoding='utf-8'))
        print('场景一 rc:', rc1, '| verdict:', p1['verdict'],
              '| fails:', [f['where'] for f in p1['fails']],
              '| waived:', [(w['where'], w['reason']) for w in p1['waived']])
        assert rc1 == 1 and p1['verdict'] == 'FAIL'
        assert [f['where'] for f in p1['fails']] == ['102'], '102 无豁免必须仍拦'
        assert len(p1['waived']) == 1 and p1['waived'][0]['where'] == '101'
        assert p1['waived'][0]['reason'] == '桩测试'

        r['translations'][0]['translation'] = '甲改了。'
        io.open(res, 'w', encoding='utf-8').write(json.dumps(r, ensure_ascii=False))
        rc2 = run(rep2)
        p2 = json.load(open(rep2, encoding='utf-8'))
        print('场景二 rc:', rc2, '| verdict:', p2['verdict'],
              '| fails:', [f['where'] for f in p2['fails']], '| waived:', p2['waived'])
        assert rc2 == 1 and p2['verdict'] == 'FAIL', '译文变更后旧豁免必须失效'
        assert sorted(f['where'] for f in p2['fails']) == ['101', '102']
        assert p2['waived'] == [], '失配豁免不得计为 WAIVED'

    print('豁免通道回归：端到端降级 + 改译失效 全部 OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
