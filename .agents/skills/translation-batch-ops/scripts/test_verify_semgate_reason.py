#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_subagent_batch.py 的语义门 UNCHECKED 原因提取。

事故锚定：真实环境里 `TYPESAFE_API_KEY` 一直在（107 字符，Process+User 双级），
真正缺的是项目根 `semantic-gate.config.json`（.gitignore 排除、从不提交）。
但 verify_subagent_batch 的 SEMGATE_UNCHECKED 警告把两种原因一律写成
「TYPESAFE_API_KEY 未设置」，于是「缺配置」被连续多轮误报成「缺密钥」，
并被下游派单卡与 PROGRESS/SOP 照抄放大。本组测试锁住「报真实原因」。

运行：
  py -3 -m unittest discover -s .agents/skills/translation-batch-ops/scripts -p "test_verify_semgate_reason.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_subagent_batch as vsb  # noqa: E402


class ReasonExtractionTest(unittest.TestCase):
    def test_missing_config_reason_is_reported_verbatim(self):
        out = ('{"verdict": "UNCHECKED", "units_checked": 0, "fail_count": 0, '
               '"warning_count": 0}\n'
               'SEMGATE_UNCHECKED: 语义门连接未配置（semantic-gate.config.json '
               '缺 api_url/model），本批语义层未检查（参考层缺失不阻塞）\n')
        got = vsb.semgate_uncheck_reason(out)
        self.assertIn("semantic-gate.config.json", got)
        self.assertIn("api_url/model", got)

    def test_missing_key_reason_is_distinguished(self):
        out = ('{"verdict": "UNCHECKED"}\n'
               'SEMGATE_UNCHECKED: api_key 未配置（semantic-gate.config.json 与 '
               'TYPESAFE_API_KEY 环境变量均缺），本批语义层未检查\n')
        got = vsb.semgate_uncheck_reason(out)
        self.assertIn("api_key", got)
        # 两种原因必须能被下游区分开
        self.assertNotIn("semantic-gate.config.json 缺", got)

    def test_never_asserts_key_is_unset_without_evidence(self):
        """没有 SEMGATE_UNCHECKED 行时不得断言「KEY 未设置」。"""
        got = vsb.semgate_uncheck_reason('{"verdict": "UNCHECKED"}\n')
        self.assertNotIn("TYPESAFE_API_KEY 未设置", got)
        self.assertIn("未返回具体原因", got)

    def test_empty_stdout_is_safe(self):
        got = vsb.semgate_uncheck_reason("")
        self.assertTrue(got)
        self.assertNotIn("TYPESAFE_API_KEY 未设置", got)

    def test_none_stdout_is_safe(self):
        self.assertTrue(vsb.semgate_uncheck_reason(None))


class LiveDiagnosticTest(unittest.TestCase):
    """用真实 semantic_gate 跑一次，确认本机报出的是「缺配置」而非「缺密钥」。

    这是本次误报的回归防线：若哪天配置补齐或环境变量被清掉，报文会随之改变，
    但**不得**再出现「密钥明明在、却说缺密钥」的情形。
    """

    def test_real_semgate_reports_config_not_key(self):
        semgate = (HERE.parent.parent / "translation-quality-gate" / "scripts"
                   / "semantic_gate.py")
        if not semgate.is_file():
            self.skipTest("semantic_gate.py 不在预期位置")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "r.json").write_text(json.dumps({"translations": []}),
                                       encoding="utf-8")
            (td / "x.xml").write_text("<root/>", encoding="utf-8")
            (td / "c.json").write_text("{}", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(semgate), "--result", str(td / "r.json"),
                 "--xml", str(td / "x.xml"), "--contract", str(td / "c.json"),
                 "--batch", "B1"],
                capture_output=True, text=True, encoding="utf-8")
            if "SEMGATE_UNCHECKED" not in (r.stdout or ""):
                self.skipTest("语义门已可运行（配置齐备），本用例不适用")
            detail = vsb.semgate_uncheck_reason(r.stdout)
            # 本机 api_key 在环境里，报文不得说缺 key
            import os
            if os.environ.get("TYPESAFE_API_KEY", "").strip():
                self.assertNotIn("api_key 未配置", detail)
                self.assertIn("连接未配置", detail)


if __name__ == "__main__":
    unittest.main()
