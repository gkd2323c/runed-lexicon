# -*- coding: utf-8 -*-
"""claim_batches.py 行为锁定。

锁住的核心契约：
  1. 未被认领且源句不相交的批次可以成功认领，台账写入
  2. 已被别人认领的批次拒绝（conflict 退出码 1）
  3. 与他人已认领批次共享源句的批次拒绝（同源分裂防御）
  4. --check 只校验不写台账
  5. --release 释放名下批次
  6. --range 展开为闭区间
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / 'claim_batches.py'


def build_plan(path: Path, batches: list[dict]) -> None:
    """写一个最小批次计划文件。"""
    path.write_text(json.dumps({
        'batch_count': len(batches),
        'batches': batches,
    }, ensure_ascii=False), encoding='utf-8')


def run(cwd: Path, *argv) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=str(cwd), capture_output=True, text=True, encoding='utf-8')


class ClaimBatchesTest(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.work = root / '.work' / 'T'
        (self.work / 'context').mkdir(parents=True)
        self.plan = self.work / 'context' / 'T-info-batches.json'
        build_plan(self.plan, [
            {'id': 'INFO-001', 'srcs': ['hello', 'world'], 'idx': [0, 1]},
            {'id': 'INFO-002', 'srcs': ['foo', 'bar'], 'idx': [2, 3]},
            {'id': 'INFO-003', 'srcs': ['shared line', 'baz'], 'idx': [4, 5]},
            {'id': 'INFO-004', 'srcs': ['shared line', 'qux'], 'idx': [6, 7]},
        ])
        self.cwd = root

    def tearDown(self):
        self.td.cleanup()

    def test_claim_writes_ledger(self):
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-001')
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = json.loads(
            (self.work / 'context' / 'T-lines.json').read_text(encoding='utf-8'))
        self.assertIn('alice', ledger['claims'])
        self.assertEqual(ledger['claims']['alice'], ['INFO-001'])

    def test_duplicate_claim_rejected(self):
        run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-001')
        r = run(self.cwd, '--stem', 'T', '--owner', 'bob', '--batches', 'INFO-001')
        self.assertEqual(r.returncode, 1)
        self.assertIn('已被 alice 认领', r.stderr)

    def test_shared_source_rejected(self):
        """INFO-003 和 INFO-004 共享 'shared line'；alice 领了 003，bob 领 004 必须被拒。"""
        run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-003')
        r = run(self.cwd, '--stem', 'T', '--owner', 'bob', '--batches', 'INFO-004')
        self.assertEqual(r.returncode, 1)
        self.assertIn('共享', r.stderr)

    def test_same_owner_can_extend(self):
        """同一 owner 追加认领，只要不与别人冲突。"""
        run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-001')
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-002')
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = json.loads(
            (self.work / 'context' / 'T-lines.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(ledger['claims']['alice']), ['INFO-001', 'INFO-002'])

    def test_check_does_not_write(self):
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-001',
                '--check')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('仅校验', r.stdout)
        self.assertFalse((self.work / 'context' / 'T-lines.json').exists())

    def test_release(self):
        run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-001',
            'INFO-002')
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice', '--release',
                '--batches', 'INFO-001')
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = json.loads(
            (self.work / 'context' / 'T-lines.json').read_text(encoding='utf-8'))
        self.assertEqual(ledger['claims']['alice'], ['INFO-002'])

    def test_range_expansion(self):
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice',
                '--range', 'INFO-002', 'INFO-004')
        # INFO-003/004 共享源句，领这一整段应该成功（同一 owner 内部不算冲突）
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = json.loads(
            (self.work / 'context' / 'T-lines.json').read_text(encoding='utf-8'))
        self.assertEqual(ledger['claims']['alice'],
                         ['INFO-002', 'INFO-003', 'INFO-004'])

    def test_unknown_batch_rejected(self):
        r = run(self.cwd, '--stem', 'T', '--owner', 'alice', '--batches', 'INFO-999')
        self.assertEqual(r.returncode, 1)
        self.assertIn('不在计划', r.stderr)


if __name__ == '__main__':
    unittest.main()
