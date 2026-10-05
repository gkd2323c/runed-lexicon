#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consume_batch 的 confidence 归一化回归。

失效形态：项目定义的合法枚举是**大写** `HIGH`/`MEDIUM`/`LOW`
（`translation-executor` SKILL），但 object 形态的 `map.json` 原先原样透传，
子代理写小写 `high`/`medium`/`low` 时会**静默**混进 `translation.json`
——按大写比较的统计与门禁于是漏掉这批行，且没有任何环节报错。
实测两个子代理在同一轮里就分成了小写与大写两种写法。

修复：`flatten_map` 归一到规范枚举，非法值直接报错。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consume_batch import CONFIDENCE_VALUES, flatten_map  # noqa: E402


class ConfidenceEnumTests(unittest.TestCase):
    def test_values_are_uppercase(self):
        self.assertEqual(CONFIDENCE_VALUES, {"HIGH", "MEDIUM", "LOW"})

    def test_no_lowercase_in_the_set(self):
        """护栏：不得有人把值改回小写。"""
        for v in CONFIDENCE_VALUES:
            self.assertEqual(v, v.upper())


class FlattenConfidenceTests(unittest.TestCase):
    def _one(self, conf):
        raw = {"1": {"translation": "甲", "status": "TRANSLATED", "confidence": conf}}
        return flatten_map(raw)

    def test_lowercase_is_normalised(self):
        """核心回归：小写必须被归一，不得静默放行。"""
        for low, up in (("high", "HIGH"), ("medium", "MEDIUM"), ("low", "LOW")):
            filled, err = self._one(low)
            self.assertIsNone(err, f"{low} 不该报错")
            self.assertEqual(filled["1"]["confidence"], up)

    def test_mixed_case_is_normalised(self):
        filled, err = self._one("MeDiUm")
        self.assertIsNone(err)
        self.assertEqual(filled["1"]["confidence"], "MEDIUM")

    def test_surrounding_whitespace_is_trimmed(self):
        filled, err = self._one("  high  ")
        self.assertIsNone(err)
        self.assertEqual(filled["1"]["confidence"], "HIGH")

    def test_already_uppercase_unchanged(self):
        filled, err = self._one("HIGH")
        self.assertIsNone(err)
        self.assertEqual(filled["1"]["confidence"], "HIGH")

    def test_illegal_value_errors(self):
        """非法枚举必须报错，而不是悄悄当成某个值。"""
        filled, err = self._one("very high")
        self.assertIsNotNone(err)
        self.assertIn("confidence", err)
        self.assertEqual(filled, {})

    def test_missing_confidence_passes_through(self):
        """缺省即 HIGH（executor 语义），不在此处补，交由下游默认值处理。"""
        filled, err = flatten_map({"1": {"translation": "甲", "status": "TRANSLATED"}})
        self.assertIsNone(err)
        self.assertNotIn("confidence", filled["1"])

    def test_other_fields_preserved(self):
        filled, err = flatten_map({
            "1": {"translation": "甲", "status": "TRANSLATED",
                  "confidence": "low", "notes": "说明", "waived_tokens": ["[X]"]},
        })
        self.assertIsNone(err)
        self.assertEqual(filled["1"]["notes"], "说明")
        self.assertEqual(filled["1"]["waived_tokens"], ["[X]"])


class FlattenShapeTests(unittest.TestCase):
    """归一化不得改变原有的两种输入形态处理。"""

    def test_flat_string_gets_defaults(self):
        filled, err = flatten_map({"1": "甲", "2": "  乙  "})
        self.assertIsNone(err)
        self.assertEqual(filled["1"], {"translation": "甲", "status": "TRANSLATED",
                                        "confidence": "HIGH"})
        self.assertEqual(filled["2"]["translation"], "乙")  # 去空白

    def test_empty_string_errors(self):
        filled, err = flatten_map({"1": "   "})
        self.assertIsNotNone(err)
        self.assertEqual(filled, {})

    def test_bad_type_errors(self):
        filled, err = flatten_map({"1": 42})
        self.assertIsNotNone(err)
        self.assertEqual(filled, {})

    def test_original_dict_not_mutated(self):
        """输入对象不得被就地改写——map.json 仍是子代理的原始产物。"""
        entry = {"translation": "甲", "status": "TRANSLATED", "confidence": "low"}
        raw = {"1": entry}
        flatten_map(raw)
        self.assertEqual(entry["confidence"], "low")
        self.assertEqual(raw["1"], entry)


if __name__ == "__main__":
    unittest.main()
