# -*- coding: utf-8 -*-
"""lint_terms.py 回归测试（unittest 风格，进 CI discover）。

锁定行为：
- 引号/括号栈式配对：未闭合、多余、错序 → FAIL；正常嵌套（含「」）→ 无报
- 直角引号在目标形字段 FAIL；note 里「」为惯例不报
- 不可见/控制字符 FAIL
- 半角双引号贴中文 FAIL；半角单引号贴中文 WARN（音译撇号）
- 目标形繁体 FAIL；forbidden 繁体 WARN（刻意防护形）
- DUP_ENGLISH / FORBIDDEN_SELF / FORBIDDEN_EMPTY FAIL
- CLI 退出码：FAIL>0 → 1；仅 WARN → 0

Run: python -m unittest discover -s .agents/skills/term-contract-compiler/scripts -p "test_*.py"
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import lint_terms as LT

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "lint_terms.py"

HAS_TRAD = LT.to_simplified("約") != "約"


def codes(issues, severity=None):
    return [x["code"] for x in issues if severity is None or x["severity"] == severity]


class PairCheckTest(unittest.TestCase):
    def test_unclosed_left_quote(self):
        issues = LT.check_pairs("“唤风者“约根")
        self.assertEqual([c for c, _, _ in issues].count("QUOTE_UNPAIRED"), 2)

    def test_balanced_quotes_ok(self):
        self.assertEqual(LT.check_pairs("“唤风者”约根"), [])

    def test_nested_corner_quotes_ok(self):
        s = "《归来之歌》卷七「他开创了称为『圆环』的亲信顾问传统」"
        self.assertEqual(LT.check_pairs(s), [])

    def test_extra_closing(self):
        issues = LT.check_pairs("好”")
        self.assertEqual([c for c, _, _ in issues], ["QUOTE_UNPAIRED"])

    def test_wrong_order(self):
        issues = LT.check_pairs("（前文【未完）后文】")
        self.assertTrue(any(c == "QUOTE_ORDER" for c, _, _ in issues))

    def test_paren_balanced(self):
        self.assertEqual(LT.check_pairs("（备注；【注】）"), [])


class ScanStringTest(unittest.TestCase):
    def _scan(self, s, strict=True, field="zh"):
        return LT.scan_string(s, strict=strict, field=field, entry="E", file="F")

    def test_invisible_char_fail(self):
        issues = self._scan("约\u200b根")
        self.assertIn("INVISIBLE", codes(issues, "fail"))

    def test_control_char_fail(self):
        issues = self._scan("约\x07根")
        self.assertIn("CONTROL_CHAR", codes(issues, "fail"))

    def test_corner_quote_fail_in_strict(self):
        issues = self._scan("「约根」")
        self.assertIn("CORNER_QUOTE", codes(issues, "fail"))

    def test_corner_quote_ok_in_note(self):
        issues = LT.scan_string("作「劫之异常体」", strict=False, field="note",
                                entry="E", file="F")
        self.assertNotIn("CORNER_QUOTE", codes(issues))
        self.assertEqual(codes(issues, "fail"), [])

    def test_corner_quote_unclosed_in_note_fail(self):
        issues = LT.scan_string("作「劫之异常", strict=False, field="note",
                                entry="E", file="F")
        self.assertIn("QUOTE_UNPAIRED", codes(issues, "fail"))

    def test_ascii_double_quote_cjk_fail(self):
        issues = self._scan('"唤风者"约根')
        self.assertIn("ASCII_QUOTE_CJK", codes(issues, "fail"))

    def test_ascii_apostrophe_cjk_warn(self):
        issues = self._scan("杰'扎格")
        self.assertEqual(codes(issues, "fail"), [])
        self.assertIn("ASCII_APOSTROPHE_CJK", codes(issues, "warn"))

    def test_halfwidth_punct_warn(self):
        issues = self._scan("等等,还有")
        self.assertIn("HALFWIDTH_PUNCT", codes(issues, "warn"))

    def test_latin_in_zh_warn(self):
        issues = self._scan("约CHIM根")
        self.assertIn("LATIN_IN_ZH", codes(issues, "warn"))

    @unittest.skipUnless(HAS_TRAD, "繁简转换依赖（opencc/zhconv）不可用")
    def test_traditional_in_zh_fail(self):
        issues = self._scan("約根")
        self.assertIn("TRADITIONAL", codes(issues, "fail"))

    @unittest.skipUnless(HAS_TRAD, "繁简转换依赖（opencc/zhconv）不可用")
    def test_traditional_in_forbidden_warn(self):
        issues = self._scan("黛爾芬", strict=True, field="forbidden[4]")
        self.assertNotIn("TRADITIONAL", codes(issues, "fail"))
        self.assertIn("TRADITIONAL", codes(issues, "warn"))

    def test_english_field_skips_visible_checks(self):
        issues = LT.scan_string('Neeth & "His" Past', strict=True, field="english",
                                entry="E", file="F", check_visible=False)
        self.assertEqual(issues, [])


class DocLevelTest(unittest.TestCase):
    def test_dup_english_fail(self):
        data = {"terms": [
            {"english": "Skeleton Key", "zh": "万能钥匙"},
            {"english": "Skeleton Key", "zh": "骷髅钥匙"},
        ]}
        issues = LT.lint_terms_doc(data, "T")
        self.assertIn("DUP_ENGLISH", codes(issues, "fail"))

    def test_forbidden_self_fail(self):
        data = {"terms": [{"english": "E", "zh": "约根", "forbidden": ["约根"]}]}
        issues = LT.lint_terms_doc(data, "T")
        self.assertIn("FORBIDDEN_SELF", codes(issues, "fail"))

    def test_forbidden_empty_fail(self):
        data = {"terms": [{"english": "E", "zh": "约根", "forbidden": ["  "]}]}
        issues = LT.lint_terms_doc(data, "T")
        self.assertIn("FORBIDDEN_EMPTY", codes(issues, "fail"))


class CliTest(unittest.TestCase):
    def _run(self, doc, name="terms.json"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / name
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--terms", str(p)],
            capture_output=True, text=True, encoding="utf-8")

    def test_exit1_on_fail(self):
        res = self._run({"terms": [{"english": "E", "zh": "“未闭合"}]})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("QUOTE_UNPAIRED", res.stdout)

    def test_exit0_on_warn_only(self):
        res = self._run({"terms": [{"english": "E", "zh": "杰'扎格"}]})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_exit0_clean(self):
        res = self._run({"terms": [{"english": "E", "zh": "约根"}]})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class RepoDataTest(unittest.TestCase):
    """仓库内数据自检：本仓的 global-forbidden-words.json 必须 lint fail=0。

    bans 为发布资产（随仓库），CI unittest discover 会执行本项；
    mods/ 与 dictionary/ 被本地排除，不在断言范围。"""

    REPO_ROOT = HERE.parents[3]

    def test_repo_bans_clean(self):
        bans = self.REPO_ROOT / "global-forbidden-words.json"
        if not bans.is_file():
            self.skipTest("global-forbidden-words.json 不在本仓库")
        issues = LT.lint_path("bans", str(bans))
        fails = [x for x in issues if x["severity"] == "fail"]
        msg = "\n".join(
            f"  [{x['entry']}].{x['field']} {x['code']}: {x['detail']}"
            for x in fails[:20])
        self.assertEqual(fails, [], f"仓库 bans 数据 lint FAIL:\n{msg}")


if __name__ == "__main__":
    unittest.main()
