# -*- coding: utf-8 -*-
"""test_gen_form_tables.py — gen_form_convergence_tables / fill_table_rationale 的回归用例。

表生成器直接决定跳批收敛的方向与范围，错了会一次改几十行，所以覆盖：
按锚分流、锚不匹配时跳过（而不是硬替换）、**以源句为键**（同源族跨批时每批都拿到同一条映射）、
同源句算出两个目标即中止、目标形与现形相同的无意义规则拒绝、dry-run 不落盘。

用 unittest（CI 的 discover 步骤认字面量 unittest）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
GEN = HERE / 'gen_form_convergence_tables.py'
FILL = HERE / 'fill_table_rationale.py'

# 权柄 跨两锚：authority 2 行、power 2 行、authority 但已是别的形 1 行
ROWS = [
    ('It does not settle authority.', '它不裁定权柄。'),          # authority -> 威权
    ('Do not grant more authority.', '别再给他更多权柄。'),       # authority -> 威权
    ('Power changes hands.', '权柄易手。'),                       # power -> 权力
    ('A temple is an instrument of power.', '神殿是权柄的器具。'),  # power -> 权力
    ('A quiet authority.', '安静的威权。'),                        # 目标形，不动
]


def build_xml(path):
    root = ET.Element('Translations')
    for src, dst in ROWS:
        s = ET.SubElement(root, 'String')
        ET.SubElement(s, 'Source').text = src
        ET.SubElement(s, 'Dest').text = dst
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.xml = self.tmp / 'canon.xml'
        build_xml(self.xml)
        self.bdir = self.tmp / 'batches'
        for bid, idxs in (('BATCH-A', [0, 2]), ('BATCH-B', [1, 3, 4])):
            d = self.bdir / bid
            d.mkdir(parents=True)
            (d / 'index.txt').write_text(''.join(f'{i}\n' for i in idxs), encoding='utf-8')
        self.out = self.tmp / 'tabs'

    def gen(self, *rules, extra=()):
        argv = ['--stem', 'X', '--xml', str(self.xml), '--batches-dir', str(self.bdir),
                '--out-dir', str(self.out)]
        for r in rules:
            argv += ['--rule', r]
        return self._run(GEN, *argv, *extra)

    @staticmethod
    def _run(script, *args):
        r = subprocess.run([sys.executable, str(script), *args], capture_output=True)
        return r.returncode, r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')


class TestGenFormTables(Base):
    RULES = ('权柄:authority->威权', '权柄:power->权力')

    def test_splits_by_anchor(self):
        rc, out = self.gen(*self.RULES, extra=('--apply',))
        self.assertEqual(rc, 0, out)
        self.assertIn('权柄:authority->威权: 2 行', out)
        self.assertIn('权柄:power->权力: 2 行', out)
        tbl = {}
        for p in self.out.glob('table-*.json'):
            tbl.update(json.loads(p.read_text(encoding='utf-8'))['table'])
        self.assertEqual(tbl['It does not settle authority.'], '它不裁定威权。')
        self.assertEqual(tbl['Power changes hands.'], '权力易手。')
        # 已是目标形的一行不得被拉进来
        self.assertNotIn('A quiet authority.', tbl)

    def test_keyed_by_source_not_row(self):
        """同源族跨批时，每个相关批次都要拿到同一条 源句->目标 映射。"""
        xml2 = self.tmp / 'c2.xml'
        root = ET.Element('Translations')
        for _ in range(2):
            s = ET.SubElement(root, 'String')
            ET.SubElement(s, 'Source').text = 'Shared authority sentence here.'
            ET.SubElement(s, 'Dest').text = '共享句是权柄。'
        ET.ElementTree(root).write(xml2, encoding='utf-8', xml_declaration=True)
        d2 = self.tmp / 'b2'
        for bid, idxs in (('BATCH-A', [0]), ('BATCH-B', [1])):
            d = d2 / bid
            d.mkdir(parents=True)
            (d / 'index.txt').write_text(''.join(f'{i}\n' for i in idxs), encoding='utf-8')
        rc, out = self._run(GEN, '--stem', 'X', '--xml', str(xml2), '--batches-dir', str(d2),
                            '--out-dir', str(self.out), '--rule', '权柄:authority->威权', '--apply')
        self.assertEqual(rc, 0, out)
        self.assertIn('2 批', out)
        for bid in ('BATCH-A', 'BATCH-B'):
            t = json.loads((self.out / f'table-{bid}.json').read_text(encoding='utf-8'))
            self.assertEqual(t['table']['Shared authority sentence here.'], '共享句是威权。')

    def test_dry_run_writes_nothing(self):
        rc, out = self.gen(*self.RULES)
        self.assertEqual(rc, 0, out)
        self.assertIn('dry-run', out)
        self.assertEqual(list(self.out.glob('table-*.json')), [])

    def test_rejects_noop_rule(self):
        rc, out = self.gen('权柄:authority->权柄')
        self.assertEqual(rc, 1)
        self.assertIn('规则无意义', out)

    def test_rejects_bad_syntax(self):
        rc, out = self.gen('这不是规则')
        self.assertEqual(rc, 1)
        self.assertIn('规则格式应为', out)

    def test_same_source_two_shapes_aborts(self):
        """同一源句**现有两形**时必须中止。

        这不是「规则自相矛盾」——同一源句必然匹配同一条规则（命中即 break）。
        真正的问题是机械替换会忠实地把这个同源分裂保留下来，那不叫收敛。
        """
        xml3 = self.tmp / 'c3.xml'
        root = ET.Element('Translations')
        for dst in ('它不裁定权柄。', '权柄易手，比宗旨易手容易。'):
            s = ET.SubElement(root, 'String')
            ET.SubElement(s, 'Source').text = 'An authority line.'
            ET.SubElement(s, 'Dest').text = dst
        ET.ElementTree(root).write(xml3, encoding='utf-8', xml_declaration=True)
        d3 = self.tmp / 'b3' / 'BATCH-A'
        d3.mkdir(parents=True)
        (d3 / 'index.txt').write_text('0\n1\n', encoding='utf-8')
        rc, out = self._run(GEN, '--stem', 'X', '--xml', str(xml3), '--batches-dir', str(d3.parent),
                            '--out-dir', str(self.out), '--rule', '权柄:authority->威权')
        self.assertEqual(rc, 1)
        self.assertIn('同一源句现有两形', out)


class TestFillTableRationale(Base):
    RAT = {
        '权柄:authority->威权': '裁断位取威权：判据是主形须只服务一个锚，票数不作依据。',
        '权柄:power->权力': 'power 全库已定为义位分立，权位义应作权力。',
    }

    def _rat_file(self, data=None):
        p = self.tmp / 'rat.json'
        p.write_text(json.dumps(self.RAT if data is None else data, ensure_ascii=False),
                     encoding='utf-8')
        return str(p)

    def test_fills_and_is_idempotent(self):
        rc, out = self.gen('权柄:authority->威权', '权柄:power->权力', extra=('--apply',))
        self.assertEqual(rc, 0, out)
        args = ('--dir', str(self.out), '--rationale-file', self._rat_file())
        rc, out = self._run(FILL, *args)
        self.assertEqual(rc, 0, out)
        p = self.out / 'table-BATCH-A.json'
        first = json.loads(p.read_text(encoding='utf-8'))['rationale']
        self.assertTrue(first)
        self.assertEqual(first['It does not settle authority.'],
                         self.RAT['权柄:authority->威权'])
        # 再跑一次不得堆出重复 / 改写判据
        rc, out = self._run(FILL, *args)
        self.assertEqual(rc, 0, out)
        self.assertEqual(first, json.loads(p.read_text(encoding='utf-8'))['rationale'])

    def test_unknown_target_form_fails_loudly(self):
        rc, out = self.gen('权柄:authority->威权', extra=('--apply',))
        self.assertEqual(rc, 0, out)
        rc, out = self._run(FILL, '--dir', str(self.out), '--rationale-file',
                            self._rat_file({'别的一条规则->权威': 'x'}))
        self.assertEqual(rc, 1)
        self.assertIn('找不到任何已知目标形', out)

    def test_empty_rationale_rejected(self):
        rc, out = self.gen('权柄:authority->威权', extra=('--apply',))
        self.assertEqual(rc, 0, out)
        rc, out = self._run(FILL, '--dir', str(self.out), '--rationale-file',
                            self._rat_file({'权柄:authority->威权': '   '}))
        self.assertEqual(rc, 1)
        self.assertIn('判据为空', out)

    def test_ambiguous_target_rejected(self):
        rc, out = self.gen('权柄:authority->威权', '权柄:power->权力', extra=('--apply',))
        self.assertEqual(rc, 0, out)
        # 两条不同规则都指向目标形「威权」-> 歧义必须拒绝（另一目标形「权力」仍可判定，
        # 这样报的是歧义而不是「找不到目标形」）
        rc, out = self._run(FILL, '--dir', str(self.out), '--rationale-file',
                            self._rat_file({'权柄:authority->威权': 'a',
                                            '别:power->威权': 'b',
                                            '权柄:power->权力': 'c'}))
        self.assertEqual(rc, 1)
        self.assertIn('歧义', out)


if __name__ == '__main__':
    unittest.main()
