#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""write_translations 互斥闸的回归测试。

原实现只在「锁已存在」时拒绝，锁不存在就直接 `return 0` 放行——于是常态
（没人跑流水线）下任何直接调用的写回都能写 canonical。实测的并发写损坏正是
这条通道：A 直接调 writer（无锁放行）与 B 同时起流水线（建锁）两路同写。

这不是「检查有没有锁」，是**互斥**：没有锁的人得先占位。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_translations import guard_pipeline_lock, release_pipeline_lock  # noqa: E402


class GuardPipelineLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(root)
        self.addCleanup(lambda: os.chdir(self.cwd))
        self.stem = "demo"
        self.lockp = root / ".work" / self.stem / "reports" / "pipeline.lock"
        self._saved = os.environ.pop("RUNED_PIPELINE_TOKEN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["RUNED_PIPELINE_TOKEN"] = self._saved
        else:
            os.environ.pop("RUNED_PIPELINE_TOKEN", None)

    def _write_lock(self, token="other-token", pid=1234):
        self.lockp.parent.mkdir(parents=True, exist_ok=True)
        self.lockp.write_text(json.dumps(
            {"token": token, "pid": pid, "phase": "consume B1", "started": "t"}),
            encoding="utf-8")

    def test_no_stem_is_noop(self):
        self.assertEqual(guard_pipeline_lock(None, acquire=True), (0, None))

    def test_readonly_path_does_not_take_lock(self):
        """--check-only 是只读预检，不该占锁，否则白挡一条真流水线。"""
        rc, held = guard_pipeline_lock(self.stem, acquire=False)
        self.assertEqual((rc, held), (0, None))
        self.assertFalse(self.lockp.exists())

    def test_write_path_takes_lock_when_absent(self):
        rc, held = guard_pipeline_lock(self.stem, acquire=True)
        self.assertEqual(rc, 0)
        self.assertEqual(held, self.lockp)
        self.assertTrue(self.lockp.is_file())
        info = json.loads(self.lockp.read_text(encoding="utf-8"))
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(os.environ["RUNED_PIPELINE_TOKEN"], info["token"])

    def test_held_token_passes_through_when_lock_exists(self):
        """流水线父进程持 token 调子 writer：锁在、token 匹配 → 放行且不接管。"""
        self._write_lock(token="tok-parent")
        os.environ["RUNED_PIPELINE_TOKEN"] = "tok-parent"
        self.assertEqual(guard_pipeline_lock(self.stem, acquire=True), (0, None))

    def test_foreign_lock_rejects(self):
        self._write_lock()
        rc, held = guard_pipeline_lock(self.stem, acquire=True)
        self.assertEqual(rc, 2)
        self.assertIsNone(held)

    def test_release_removes_own_lock(self):
        _, held = guard_pipeline_lock(self.stem, acquire=True)
        release_pipeline_lock(held)
        self.assertFalse(self.lockp.exists())

    def test_release_does_not_touch_foreign_lock(self):
        """锁被换成了别人的，不能误删。"""
        _, held = guard_pipeline_lock(self.stem, acquire=True)
        self._write_lock(token="someone-else", pid=999)
        release_pipeline_lock(held)
        self.assertTrue(self.lockp.is_file())

    def test_release_none_is_noop(self):
        release_pipeline_lock(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
