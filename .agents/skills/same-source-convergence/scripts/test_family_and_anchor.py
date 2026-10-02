# -*- coding: utf-8 -*-
"""test_family_and_anchor.py — same_source_family / anchor_overlap 的回归用例。

两者的判读都直接决定收口方向（只改族里一行 = 净效果为零；跨锚撞形 = 方向反了），
所以用真实结构的小 XML 覆盖「跨批」「多锚」「孤例」「无锚命中」四类。

用 unittest（CI 的 discover 步骤认字面量 unittest）。
"""
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
FAMILY = HERE / 'same_source_family.py'
ANCHOR = HERE / 'anchor_overlap.py'

# 5 行：0/1/2 是 authority，3/4 是 power 但共用同一个中文形「权柄」；
# 5 是 authority 的孤例；6 是无锚命中。
ROWS = [
    ('INFO:NAM1', '[A]', 'He has no authority here.', '他在此没有权柄。'),
    ('INFO:NAM1', '[B]', 'He has no authority here.', '他在此没有权柄。'),
    ('INFO:NAM1', '[C]', 'She claims authority here.', '她在此主张威权。'),
    ('INFO:NAM1', '[D]', 'Power changes hands.', '权柄易手。'),
    ('INFO:NAM1', '[E]', 'Power changes hands more readily.', '权柄易手，更易转移。'),
    ('INFO:NAM1', '[F]', 'A lone authority stands.', '孤零零的威权。'),
    ('INFO:NAM1', '[G]', 'Nobody said anything about it.', '没人提过那份权柄。'),
]


def build_xml(path):
    root = ET.Element('Translations')
    for rec, edid, src, dst in ROWS:
        s = ET.SubElement(root, 'String')
        for tag, val in (('REC', rec), ('EDID', edid), ('Source', src), ('Dest', dst)):
            ET.SubElement(s, tag).text = val
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.xml = self.tmp / 'x.xml'
        build_xml(self.xml)
        self.bdir = self.tmp / 'batches'
        # idx 0/1 归 BATCH-A，idx 2 归 BATCH-B，其余不属任何已备料批
        for bid, idxs in (('BATCH-A', [0, 1]), ('BATCH-B', [2])):
            d = self.bdir / bid
            d.mkdir(parents=True)
            (d / 'index.txt').write_text(
                ''.join(f'{i}\n' for i in idxs), encoding='utf-8')

    def family(self, *args):
        return run(FAMILY, '--stem', 'X', '--xml', str(self.xml),
                   '--batches-dir', str(self.bdir), *args)


def run(script, *args):
    r = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True)
    # SystemExit('msg') 走 stderr，其余输出走 stdout——断言时两边都要看
    return r.returncode, r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')


class TestSameSourceFamily(Base):
    def test_finds_cross_batch_family(self):
        rc, out = self.family('--idx', '0')
        self.assertEqual(rc, 0, out)
        self.assertIn('同源族 2 行', out)
        # 族成员必须带上各自批次，否则无法据此拼 close_round 的 --batches
        self.assertIn('BATCH-A', out)

    def test_marks_singleton(self):
        rc, out = self.family('--idx', '5')
        self.assertEqual(rc, 0, out)
        self.assertIn('孤例', out)

    def test_out_of_range_reported_not_crashed(self):
        rc, out = self.family('--idx', '999')
        self.assertEqual(rc, 0, out)
        self.assertIn('越界', out)

    def test_requires_input(self):
        rc, out = self.family()
        self.assertEqual(rc, 1)
        self.assertIn('需 --idx', out)

    def test_missing_xml_fails_loudly(self):
        rc, out = run(FAMILY, '--stem', 'X', '--xml', str(self.tmp / 'nope.xml'),
                      '--batches-dir', str(self.bdir), '--idx', '0')
        self.assertEqual(rc, 1)
        self.assertIn('canonical XML 不存在', out)

    def test_summary_counts(self):
        # idx 0 族 2 行；idx 3 与 idx 4 源句不同（'Power changes hands.' vs
        # '...more readily.'），彼此不是同源族，idx 5 也是孤例
        rc, out = self.family('--idx', '0,5,3')
        self.assertEqual(rc, 0, out)
        self.assertIn('孤例 2 个 / 多行族 1 个', out)


class TestAnchorOverlap(Base):
    def test_groups_by_source_anchor(self):
        rc, out = run(ANCHOR, '--xml', str(self.xml), '--form', '权柄',
                      '--anchors', 'authority,power')
        self.assertEqual(rc, 0, out)
        # 「权柄」5 行 = authority 2 + power 2 + 无锚 1
        self.assertIn('共 5 行', out)
        self.assertIn('锚 authority: 2 行', out)
        self.assertIn('锚 power: 2 行', out)

    def test_flags_rows_without_anchor(self):
        rc, out = run(ANCHOR, '--xml', str(self.xml), '--form', '权柄',
                      '--anchors', 'authority,power')
        self.assertIn('源文不含任一锚', out)

    def test_form_absent_reports_zero(self):
        rc, out = run(ANCHOR, '--xml', str(self.xml), '--form', '不存在的形',
                      '--anchors', 'authority')
        self.assertEqual(rc, 0, out)
        self.assertIn('共 0 行', out)

    def test_multi_anchor_row_labelled(self):
        # 「孤零零的威权」只命中 authority，验证单一锚不会被误标成多锚
        rc, out = run(ANCHOR, '--xml', str(self.xml), '--form', '威权',
                      '--anchors', 'authority,power')
        self.assertIn('锚 authority: 2 行', out)
        self.assertNotIn('多锚', out)


if __name__ == '__main__':
    unittest.main()
