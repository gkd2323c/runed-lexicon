#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无内容可译判定：整条为空白，或整条为纯数字。

这类行没有可翻译的内容：切进批次后模型无从下手，写回后 Dest 仍等于 Source，
下一轮扫未译又被扫回来，形成不收敛的循环（实测全库 167 条长期停在此状态，
Artaeum / TheSidrat / BecomeKingofSkyrimTNG 的 42 条索引对账确认已在批次内）。

方向由官方词典先例定死，不是偏好：

  - 纯空白 111/111 全部 Dest == Source（保持原样，零改动）
  - 纯数字   4/4  全部 Dest == Source

与前导空格的方向相反，不要混为一谈：官方对前导空格源文 40 条里的 38 条
去掉了空格（那是引擎拼接片段，中文相连本就不需要空格），所以前导空格不属
本判据，也不该被拦。

口径统一：`scan_plan_gaps.load_all_rows` 原本已排除纯空白（`not source.strip()`）
但未排除纯数字；两个批次规划器则只排空串（`not source`），于是空白行会进批次、
却又不计入缺口。本模块给三者同一个判据。

只读判定，不修改任何数据。
"""
from __future__ import annotations


def is_untranslatable(source: str) -> bool:
    """整条源文无内容可译：全空白，或全数字。

    >>> is_untranslatable(' ')
    True
    >>> is_untranslatable('\\n\\n\\n')
    True
    >>> is_untranslatable('1')
    True
    >>> is_untranslatable('I am here')
    False
    >>> is_untranslatable('...')
    False

    最后一条是有意的：省略号要译成中文省略号（官方 243 条 `^\\.+$` 里 238 条
    已译），按纯符号形状一刀切会把它拦掉。
    """
    stripped = source.strip()
    return not stripped or stripped.isdigit()


__all__ = ["is_untranslatable"]
