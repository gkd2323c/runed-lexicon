#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translation-protect 回归测试。只跑内存数据，不碰真实项目文件。

    py -3 .agents/skills/translation-protect/scripts/test_protect_text.py
"""
import sys

from protect_text import mask_unit, restore_unit, verify_unit


# ------------------------------------------------------------------ 掩码 / 还原

def test_round_trip():
    src = "Take <Alias=Target>'s sword, %s.\n"
    r = mask_unit(src)
    assert r["bucket"] == "MASK", r
    assert r["tokens"] == ["<Alias=Target>", "%s", "\n"], r
    assert r["masked"] == "Take ⟪0⟫'s sword, ⟪1⟫.⟪2⟫", r["masked"]
    assert restore_unit(r["masked"], r["masked"], r["tokens"]) == src


def test_plain_text_no_tokens():
    r = mask_unit("Just an ordinary line.")
    assert r["bucket"] == "MASK", r
    assert r["tokens"] == [] and r["masked"] == "Just an ordinary line.", r


def test_duplicate_tokens_get_distinct_index():
    r = mask_unit("%s and %s")
    assert r["tokens"] == ["%s", "%s"], r
    assert r["masked"] == "⟪0⟫ and ⟪1⟫", r["masked"]


def test_index_alignment():
    r = mask_unit("a<Alias=X>b%dc<font face='x'>e")
    assert r["tokens"] == ["<Alias=X>", "%d", "<font face='x'>"], r
    assert r["masked"] == "a⟪0⟫b⟪1⟫c⟪2⟫e", r["masked"]


def test_typed_form():
    src = "Pay %s and {0}."
    r = mask_unit(src, form="typed")
    assert r["masked"] == "Pay ⟪P0⟫ and ⟪B1⟫.", r["masked"]
    assert restore_unit(r["masked"], r["masked"], r["tokens"]) == src


def test_longbook_syntax_compatible():
    # long-book-translation 的 ⟪F数字⟫ 语法也能被还原
    assert restore_unit("⟪F0⟫", "⟪F0⟫", ["[pagebreak]"]) == "[pagebreak]"


# ------------------------------------------------------------------ 三桶

def test_exclude_resource_path():
    for s in ["textures/armor/iron.dds", "0001A2B3", "[0001A2B3]"]:
        r = mask_unit(s)
        assert r["bucket"] == "EXCLUDE", (s, r)
        assert r["masked"] is None, (s, r)


def test_pagebreak_is_masked_not_gray():
    r = mask_unit("Page one[pagebreak]Page two")
    assert r["bucket"] == "MASK", r
    assert r["tokens"] == ["[pagebreak]"], r


def test_gray_action_bracket():
    r = mask_unit("[Show Ring]")
    assert r["bucket"] == "GRAY", r
    assert "non_engine_bracket_tag" in r["gray_reasons"], r


def test_gray_gender_var():
    r = mask_unit("Hello {He}.")
    assert r["bucket"] == "GRAY", r
    assert "gender_or_context_var" in r["gray_reasons"], r
    assert r["tokens"] == ["{He}"], r


def test_gray_unknown_angle_tag():
    r = mask_unit("Use <MyCustomTag> now")
    assert r["bucket"] == "GRAY", r
    assert "non_engine_angle_tag" in r["gray_reasons"], r


# ------------------------------------------------------------------ 校验口径

def test_multiset_missing_caught():
    f = verify_unit("A ⟪0⟫ B ⟪1⟫", "甲 ⟪0⟫ 乙")
    assert f and f[0]["kind"] == "sentinel_multiset_mismatch", f
    assert f[0]["missing"] == ["⟪1⟫"], f


def test_multiset_extra_caught():
    f = verify_unit("A ⟪0⟫", "甲 ⟪0⟫ ⟪0⟫")
    assert f and f[0]["kind"] == "sentinel_multiset_mismatch", f
    assert f[0]["extra"] == ["⟪0⟫"], f


def test_legit_reorder_passes_by_default():
    # 中文语序把参数提到别名之前：合法重排，默认必须放行
    masked = "Kill ⟪0⟫ with ⟪1⟫."
    translated = "用 ⟪1⟫ 杀死 ⟪0⟫。"
    assert verify_unit(masked, translated) == []


def test_reorder_only_flagged_with_check_order():
    masked = "Kill ⟪0⟫ with ⟪1⟫."
    translated = "用 ⟪1⟫ 杀死 ⟪0⟫。"
    f = verify_unit(masked, translated, check_order=True)
    assert f and f[0]["kind"] == "sentinel_reordered", f


def test_multiset_beats_order_when_both_wrong():
    # 既漏又乱：报多重集（更根本），不报顺序
    f = verify_unit("⟪0⟫ ⟪1⟫", "⟪1⟫")
    assert f and f[0]["kind"] == "sentinel_multiset_mismatch", f


# ------------------------------------------------------------------ runner

TESTS = [
    test_round_trip,
    test_plain_text_no_tokens,
    test_duplicate_tokens_get_distinct_index,
    test_index_alignment,
    test_typed_form,
    test_longbook_syntax_compatible,
    test_exclude_resource_path,
    test_pagebreak_is_masked_not_gray,
    test_gray_action_bracket,
    test_gray_gender_var,
    test_gray_unknown_angle_tag,
    test_multiset_missing_caught,
    test_multiset_extra_caught,
    test_legit_reorder_passes_by_default,
    test_reorder_only_flagged_with_check_order,
    test_multiset_beats_order_when_both_wrong,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        else:
            print(f"  ok   {fn.__name__}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
