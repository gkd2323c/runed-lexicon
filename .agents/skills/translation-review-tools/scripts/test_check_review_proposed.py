# -*- coding: utf-8 -*-
"""test_check_review_proposed.py — check_review_proposed 的回归用例。

覆盖 INFO-574 事故的三种形态：proposed 与现译逐字相同（散文里有改法）、
proposed 为空、xml_index 不是标量 int；外加 --allow-same 逃生门。
用 unittest（CI 的 discover 步骤认字面量 unittest）。
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).with_name('check_review_proposed.py')
sys.path.insert(0, str(SCRIPT.parent))
import check_review_proposed as crp  # noqa: E402


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.xml = {
            10: {'source': 'He kept it.', 'dest': '他留着它。'},
            11: {'source': 'He lost it.', 'dest': '他丢了它。'},
        }

    def test_same_as_now_is_unusable(self):
        kind, now = crp.classify({'xml_index': 10, 'proposed': '他留着它。'}, self.xml)
        self.assertEqual(kind, 'SAME_AS_NOW')
        self.assertEqual(now, '他留着它。')

    def test_whitespace_only_diff_still_same(self):
        kind, _ = crp.classify({'xml_index': 10, 'proposed': '  他留着它。  '}, self.xml)
        self.assertEqual(kind, 'SAME_AS_NOW')

    def test_empty_proposed(self):
        for val in (None, '', '   '):
            kind, _ = crp.classify({'xml_index': 10, 'proposed': val}, self.xml)
            self.assertEqual(kind, 'EMPTY', val)

    def test_non_int_index(self):
        for val in ('10', 10.0, [10]):
            kind, _ = crp.classify({'xml_index': val, 'proposed': '他留着它。'}, self.xml)
            self.assertEqual(kind, 'NOT_IN_BATCH', val)

    def test_real_change_is_usable(self):
        kind, _ = crp.classify({'xml_index': 10, 'proposed': '他留下了它。'}, self.xml)
        self.assertIsNone(kind)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.xml = self.tmp / 'x.xml'
        ET.ElementTree(ET.fromstring(
            '<Translations><String><REC>INFO</REC><EDID>[1]</EDID>'
            '<Source>He kept it.</Source><Dest>他留着它。</Dest></String></Translations>'
        )).write(self.xml, encoding='utf-8', xml_declaration=True)

    def _rec(self, name, findings):
        p = self.tmp / name
        p.write_text(json.dumps({'batch': 'INFO-001', 'findings': findings},
                                ensure_ascii=False), encoding='utf-8')
        return str(p)

    def _run(self, recs, *extra):
        cmd = [sys.executable, str(SCRIPT), '--xml', str(self.xml), '--reports', *recs, *extra]
        r = subprocess.run(cmd, capture_output=True)
        return r.returncode, r.stdout.decode('utf-8', 'replace')

    def test_all_usable_exits_zero(self):
        rec = self._rec('a.json', [{'xml_index': 0, 'proposed': '他留下了它。'}])
        rc, out = self._run([rec])
        self.assertEqual(rc, 0, out)
        self.assertIn('proposed 全部为可直接落盘', out)

    def test_same_as_now_exits_one(self):
        rec = self._rec('b.json', [{'xml_index': 0, 'proposed': '他留着它。'}])
        rc, out = self._run([rec])
        self.assertEqual(rc, 1, out)
        self.assertIn('SAME_AS_NOW', out)

    def test_allow_same_tolerates(self):
        rec = self._rec('c.json', [{'xml_index': 0, 'proposed': '他留着它。'}])
        rc, out = self._run([rec], '--allow-same')
        self.assertEqual(rc, 0, out)

    def test_empty_still_blocks_under_allow_same(self):
        rec = self._rec('d.json', [{'xml_index': 0, 'proposed': ''}])
        rc, out = self._run([rec], '--allow-same')
        self.assertEqual(rc, 1, out)
        self.assertIn('EMPTY', out)

    def test_no_findings_is_clean(self):
        rec = self._rec('e.json', [])
        rc, out = self._run([rec])
        self.assertEqual(rc, 0, out)
        self.assertIn('findings=0', out)


if __name__ == '__main__':
    unittest.main()
