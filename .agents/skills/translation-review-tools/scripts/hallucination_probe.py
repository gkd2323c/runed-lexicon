#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长文本抗幻觉机械探针：报可疑信号，不判语义。

对已译 canonical 逐行跑四类**显性**信号，供人工/模型精读定位。
定位是**候选发现器**：输出需人核，命中不等于错误（见「已知误报」）。

探针
----
P1 段落数少于源文   —— BOOK/MESG 类多段文本可能被吞段（合段属 LOW，非内容丢失）
P2 译文长度比异常   —— 去排版标签后 中文/英文 低于 0.20 疑截断、高于 0.62 疑增译
                      **仅在长文本上有效**：短口语行（INFO）中文天然比英文短得多，
                      实测 --min-len 120 时 40 条 P2 全为正常压缩而非截断；
                      默认门槛 300 即为此故，调低门槛时请人工核 P2 全部输出。
P3 数字语义缺失     —— 源文阿拉伯数字在译文中既无原形也无中文数词对应
P4 性别代词强冲突   —— 源文只出现单一性别、译文只出现相反性别

不做
----
- 不判断译文质量、用词、语序、修辞（那是语义精读的活）
- **不做专名编造检测**：实测信噪比极差（Artaeum 546/546 全误报）。词表用连写形而源文
  分词（`soulgem` vs `soul gem`）、项目规则性补全（`the Eye`→「玛格纳斯之眼」）都会
  造成误报。该维度交语义精读。

已知误报（中文无词边界等固有限制，需人工核销）
------------------------------------------------
- P3：中文习惯表达不写数字形。`2 meters`→「两米」、`80%`→「八成」已由 `cn_number_variants`
  覆盖（「两」「X成」）；仍无法机械穷举的俗语会报，需人工核销。
- P1：合段排版（源文两段并作译文一段）会报，但内容通常完整。
- P4：作者笔误会报（如源文 `He's been here` 实指女性角色）。报的是"不一致"，
  归因需人判。

用法
----
    py -3 hallucination_probe.py --xml mods/<plugin>/<plugin>_english_chinese_translated.xml
    py -3 hallucination_probe.py --xml ... --min-len 300 --json out.json

只读：不修改任何文件。
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# --- P1 段落 ---
P_TAG_RE = re.compile(r"<p\b", re.I)
BLANK_SPLIT_RE = re.compile(r"\n\s*\n")
# --- P2 长度 ---
TAG_RE = re.compile(r"<[^>]+>")
# --- P3 数字 ---
NUM_RE = re.compile(r"\b(\d+)\b")
CN_DIGITS = "零一二三四五六七八九"
# --- P4 性别 ---
MALE_EN = re.compile(r"\b(he|him|his|himself)\b", re.I)
FEMALE_EN = re.compile(r"\b(she|her|hers|herself)\b", re.I)
MALE_ZH = re.compile(r"他")
FEMALE_ZH = re.compile(r"她")

RATIO_LOW, RATIO_HIGH = 0.20, 0.62


def cn_number(n: int) -> str:
    """1..99999 -> 中文数词（常规写法），用于判断数字是否被意译。"""
    if n == 0:
        return "零"
    if n < 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + (CN_DIGITS[n - 10] if n > 10 else "")
    if n < 100:
        return CN_DIGITS[n // 10] + "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    if n < 1000:
        s = CN_DIGITS[n // 100] + "百"
        r = n % 100
        if r == 0:
            return s
        return s + ("零" + CN_DIGITS[r] if r < 10 else cn_number(r))
    if n < 10000:
        s = CN_DIGITS[n // 1000] + "千"
        r = n % 1000
        if r == 0:
            return s
        return s + ("零" + cn_number(r) if r < 100 else cn_number(r))
    s = cn_number(n // 10000) + "万"
    r = n % 10000
    if r == 0:
        return s
    return s + ("零" + cn_number(r) if r < 1000 else cn_number(r))


def cn_number_variants(n: int) -> set[str]:
    """n 的中文可能写法（超过「cn_number 唯一形」的部分不计入误报）。

    中文习惯与机械数字转写不完全一致：
      - 2 常写「两」而非「二」（2 meters -> 两米）；
      - 百分比常说「X成」（80% -> 八成）。
    这两类都是正常的意译，不当数字缺失报。
    """
    out = {cn_number(n)}
    if n == 2:
        out.add("两")
    if n >= 10 and n % 10 == 0 and n <= 100:
        out.add(cn_number(n // 10) + "成")
    return out


def para_count(text: str) -> int:
    """段落数：取 <p> 标签数与空行分段数的较大者（容忍两种排版）。"""
    tags = len(P_TAG_RE.findall(text))
    blanks = len([b for b in BLANK_SPLIT_RE.split(text) if b.strip()])
    return max(tags, blanks)


def visible_len(text: str) -> int:
    """去排版标签后的可见字符数。

    标签不翻译（源译两侧 1:1 相同），计入会稀释中英密度比：短文本叠加大量
    <font> 时 ratio 被抬到 0.6+，全是假阳性（实测 6 份学院文凭）。
    """
    return len(TAG_RE.sub("", text))


def probe_row(idx, rec, edid, src, dst, min_len):
    flags = []
    if not src or src == dst or len(src) < min_len:
        return flags

    # P1
    sp, dp = para_count(src), para_count(dst)
    if sp >= 2 and dp < sp:
        flags.append((idx, "P1-段落少于源文", f"{dp} < {sp}", rec, edid))

    # P2
    ratio = visible_len(dst) / max(visible_len(src), 1)
    if ratio < RATIO_LOW:
        flags.append((idx, "P2-译文过短", f"ratio={ratio:.2f}", rec, edid))
    elif ratio > RATIO_HIGH:
        flags.append((idx, "P2-译文过长", f"ratio={ratio:.2f}", rec, edid))

    # P3
    lost = []
    for tok in sorted({int(x) for x in NUM_RE.findall(src)}):
        if str(tok) in dst:
            continue
        if any(v in dst for v in cn_number_variants(tok)):
            continue
        lost.append(tok)
    if lost:
        flags.append((idx, "P3-数字语义缺失", f"缺{sorted(lost)}", rec, edid))

    # P4：仅报"源文单一性别 / 译文相反性别"的强冲突
    m_en, f_en = bool(MALE_EN.search(src)), bool(FEMALE_EN.search(src))
    m_zh, f_zh = bool(MALE_ZH.search(dst)), bool(FEMALE_ZH.search(dst))
    if m_en and not f_en and f_zh and not m_zh:
        flags.append((idx, "P4-源男译女", "", rec, edid))
    elif f_en and not m_en and m_zh and not f_zh:
        flags.append((idx, "P4-源女译男", "", rec, edid))

    return flags


def main():
    ap = argparse.ArgumentParser(description="长文本抗幻觉机械探针（只读）")
    ap.add_argument("--xml", required=True, help="canonical 译文 XML")
    ap.add_argument("--min-len", type=int, default=300,
                    help="只检查源文长度 >= 该值的行（默认 300；抗幻觉主要针对长文本，"
                         "调低到 120 附近会引入大量中文压缩类 P2 噪音）")
    ap.add_argument("--json", default=None, help="把候选写入 JSON 路径")
    a = ap.parse_args()

    ss = list(ET.parse(a.xml).getroot().iter("String"))
    flags = []
    for i, s in enumerate(ss):
        flags.extend(probe_row(
            i, s.findtext("REC") or "", s.findtext("EDID") or "",
            s.findtext("Source") or "", s.findtext("Dest") or "", a.min_len,
        ))

    print(f"检查 {len(ss)} 行（源文 >= {a.min_len} 字符）| 候选 {len(flags)}")
    for kind, n in Counter(f[1] for f in flags).most_common():
        print(f"  {kind}: {n}")
    print()
    for idx, kind, detail, rec, edid in flags:
        d = f" | {detail}" if detail else ""
        print(f"[{idx}] {kind}{d} | {rec} | {edid[:44]}")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([
                {"xml_index": i, "kind": k, "detail": d, "rec": r, "edid": e}
                for i, k, d, r, e in flags
            ], fh, ensure_ascii=False, indent=1)
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
