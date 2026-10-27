# -*- coding: utf-8 -*-
"""MOD 路径解析的唯一事实来源。

背景（失效机制）：项目里长期存在一批工具按 `mods/<stem>.esp/` 硬拼 MOD 目录名，
而 stem 是 `.work/` 下的工作名，与 `mods/` 下的目录名可以完全不同
（`mods/Maslea.esm` 的 stem 是 `Maslea`，`mods/More Thalmor Dossiers` 的 stem 是 `MTD）。
按 stem 拼 `.esp` 的失效不是"报错"，而是**静默给出一个不存在的路径**：
  1. 写回类工具（close_round 等）在 `apply_fixes` 阶段报 `--translated-xml 不存在`，
     此时修正尚未写进 canonical，损失可控但整轮失败；
  2. 只读类工具（prune_close_patch 等）报 `canonical 不存在` 后退出码 2，
     把一次本该执行的剪枝静默跳过；
  3. 更坏的方向是回落分支 `next(..., stem)`：在 `mods/` 下新建一个只装产物的
     孤儿目录，工具报 PASS 而真正的 MOD 目录里始终没有 canonical。

结论：路径不猜。调用方显式给出，或由源 XML 路径反推；两者都没有时**报错退出**，
不回落、不静默、不新建目录。

规则：
  - 调用方给了显式路径（--xml / --translated-xml / --canonical），一律以它为准。
  - 没有显式路径时，从源 XML 路径反推 MOD 目录（源 XML 必在 `mods/<plugin>/` 下）。
  - 源 XML 也没有时，报错退出并说明必须显式传参。
"""
from __future__ import annotations

from pathlib import Path

EXTENSIONS = (".esp", ".esm", ".esl")


def resolve_mod_dir(explicit: str | None, stem: str, root: Path) -> Path:
    """解析 MOD 目录（mods/ 下的那一层）。

    explicit 为显式传入的 MOD 目录名或路径；stem 为 .work 工作名；root 为仓库根。
    显式优先；都没有时按候选目录探测，探测不到报错退出。
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else root / p
    for cand in (stem, *(f"{stem}{ext}" for ext in EXTENSIONS)):
        if (root / "mods" / cand).is_dir():
            return root / "mods" / cand
    raise SystemExit(
        f"error: 无法确定 MOD 目录。stem={stem!r} 在 mods/ 下没有对应目录，"
        f"也没有收到显式 --mod/--xml 参数。\n"
        f"  请显式指定 MOD 目录（如 --mod mods/Maslea.esm）或源 XML 路径"
        f"（如 --xml mods/Maslea.esm/Maslea_english_chinese.xml）。"
    )


def resolve_source_xml(explicit: str | None, stem: str, mod_dir: Path) -> Path:
    """解析源 XML（<stem>_english_chinese.xml）。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else p
    cand = mod_dir / f"{stem}_english_chinese.xml"
    if cand.is_file():
        return cand
    raise SystemExit(
        f"error: 源 XML 不存在: {cand}\n"
        f"  请用 --xml 显式指定源 XML 路径。"
    )


def resolve_canonical(explicit: str | None, stem: str, mod_dir: Path,
                      source_xml: Path | None = None) -> Path:
    """解析 canonical 写回产物路径。

    优先显式；否则由源 XML 反推（<源文件名>_translated.xml，与 stem 无关）；
    源 XML 也没有时按 stem 拼，但**拼完必须存在**，不存在即报错——绝不新建目录。
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else p
    if source_xml is not None and source_xml.is_file():
        return source_xml.parent / f"{source_xml.stem}_translated.xml"
    cand = mod_dir / f"{stem}_english_chinese_translated.xml"
    if cand.is_file():
        return cand
    raise SystemExit(
        f"error: canonical 不存在: {cand}\n"
        f"  请用 --translated-xml/--canonical 显式指定，或确认该 MOD 已完成首次写回。"
    )
