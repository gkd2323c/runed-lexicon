# -*- coding: utf-8 -*-
"""test_dump_and_append.py — dump_review_findings / append_adjudication_note 的回归用例。

两个都是每轮都要跑的（裁决视图 / 总档追加），落 `_tmp` 等于每轮重建。
覆盖：现译取自 canonical 而非批次 map、`--only` 过滤、`--full` 附加段、
notes 里混有字符串条目时的回读断言（已踩过的坑）、同 topic 替换不堆重复。

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
DUMP = HERE / 'dump_review_findings.py'
APPEND = HERE / 'append_adjudication_note.py'
REPO = HERE.parent.parent.parent  # .agents/skills/translation-review-tools -> repo root


def run(script, *args, cwd=None):
    r = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True, cwd=cwd)
    return r.returncode, r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')


class TestDumpReviewFindings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.xml = self.tmp / 'canon.xml'
        root = ET.Element('Translations')
        for src, dst in (('He kept it.', '他留着它。'), ('He lost it.', '他丢了它。')):
            s = ET.SubElement(root, 'String')
            ET.SubElement(s, 'Source').text = src
            ET.SubElement(s, 'Dest').text = dst
        ET.ElementTree(root).write(self.xml, encoding='utf-8', xml_declaration=True)

        self.rec = self.tmp / 'rec.json'
        self.rec.write_text(json.dumps({
            'batch': 'INFO-001', 'status': 'COMPLETE',
            'findings_summary': {'total_findings': 2},
            'findings': [
                {'xml_index': 0, 'severity': 'MAJOR', 'category': '源译错位',
                 'proposed': '他留下了它。', 'confidence': 'high', 'rationale': '量词。'},
                {'xml_index': 1, 'severity': 'MINOR', 'category': '搭配失当',
                 'proposed': '他把它丢了。', 'rationale': '宾语。'},
            ],
            'not_reported_as_defects': ['第 1 行通读无缺陷'],
            'line_risk_notes': ['第 0 行口语偏重'],
            'validation': {'charset_check': 'ok'},
        }, ensure_ascii=False), encoding='utf-8')

    def test_shows_source_now_prop(self):
        rc, out = run(DUMP, '--report', str(self.rec), '--xml', str(self.xml))
        self.assertEqual(rc, 0, out)
        self.assertIn('SRC  : He kept it.', out)
        self.assertIn('NOW  : 他留着它。', out)   # 来自 canonical
        self.assertIn('PROP : 他留下了它。', out)
        self.assertIn('WHY  : 量词。', out)

    def test_only_filters(self):
        rc, out = run(DUMP, '--report', str(self.rec), '--xml', str(self.xml), '--only', '1')
        self.assertEqual(rc, 0, out)
        self.assertIn('idx=1', out)
        self.assertNotIn('idx=0', out)

    def test_full_adds_extra_sections(self):
        rc, out = run(DUMP, '--report', str(self.rec), '--xml', str(self.xml), '--full')
        self.assertEqual(rc, 0, out)
        self.assertIn('not_reported_as_defects', out)
        self.assertIn('line_risk_notes', out)
        self.assertIn('charset_check', out)


class TestAppendAdjudicationNote(unittest.TestCase):
    """必须在仓库根跑（脚本按 mods/<stem>.esp 与 .work/<stem> 定位）。"""

    STEM = 'AppendNoteFixture'

    def setUp(self):
        self.rep = REPO / '.work' / self.STEM / 'reports'
        self.rep.mkdir(parents=True, exist_ok=True)
        self.mods = REPO / 'mods' / f'{self.STEM}.esp'
        self.mods.mkdir(parents=True, exist_ok=True)
        (self.mods / f'{self.STEM}_english_chinese_translated.xml').write_text(
            '<Translations><String><Source>a</Source><Dest>b</Dest></String></Translations>',
            encoding='utf-8')
        self.path = self.rep / f'{self.STEM}-review-adjudication.json'
        # 故意混入一个字符串条目（早期手写记录的真实形态）
        self.path.write_text(json.dumps({
            'stem': self.STEM, 'decisions': [{'id': 1}],
            'totals': {'close_rounds': [{'n': 1}], 'findings': 1},
            'notes': ['一条早期纯字符串记录', {'topic': '旧', 'body': 'x'}],
            'canonical_at_adjudication': 'deadbeef',
        }, ensure_ascii=False, indent=1), encoding='utf-8')

    def tearDown(self):
        # fixture 落在真实仓库里，测完必须清干净：工作区卫生是硬规则
        # （`mods/<plugin>/` 零杂物、`.work/` 只放过程资产）
        import shutil
        shutil.rmtree(REPO / '.work' / self.STEM, ignore_errors=True)
        shutil.rmtree(self.mods, ignore_errors=True)

    def _append(self, topic, body, *extra):
        return run(APPEND, '--stem', self.STEM, '--note-topic', topic,
                   '--note-body', body, *extra, cwd=str(REPO))

    def test_appends_and_survives_string_notes(self):
        rc, out = self._append('新 topic', '正文')
        self.assertEqual(rc, 0, out)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(len(data['notes']), 3)
        self.assertEqual(data['notes'][0], '一条早期纯字符串记录')  # 原样保留
        self.assertEqual(data['notes'][-1]['topic'], '新 topic')

    def test_same_topic_replaces_not_duplicates(self):
        self._append('同题', '第一版')
        rc, out = self._append('同题', '第二版')
        self.assertEqual(rc, 0, out)
        self.assertIn('替换', out)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(len(data['notes']), 3)
        self.assertEqual(data['notes'][-1]['body'], '第二版')

    def test_refreshes_canonical_and_verification(self):
        rc, out = self._append('校验刷新', '正文', '--verification', '门禁 PASS')
        self.assertEqual(rc, 0, out)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertNotEqual(data['canonical_at_adjudication'], 'deadbeef')
        self.assertEqual(data['totals']['verification'], '门禁 PASS')
        # 只碰这三处，decisions / close_rounds 必须原样
        self.assertEqual(data['decisions'], [{'id': 1}])
        self.assertEqual(data['totals']['close_rounds'], [{'n': 1}])


if __name__ == '__main__':
    unittest.main()
