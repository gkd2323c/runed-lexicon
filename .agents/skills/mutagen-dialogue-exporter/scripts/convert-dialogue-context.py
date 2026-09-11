# -*- coding: utf-8 -*-
"""convert-dialogue-context.py — Mutagen 导出 JSON → xEdit 风格 dialogue context JSON。

translation-context-builder 消费的是 xEdit 风格结构（`dialogues[]` + `infos[].form_id`
+ `editor_id`），而本项目导出链路产出的是 Mutagen 结构（`topics[]` + `infos[].formKey`）。
本脚本做两层适配：

1. 结构映射：topics→dialogues、formKey→form_id、speakerName/speakerFromCondition→
   speaker_candidates、questEdid→quest_edid。
2. FormID 前缀推导：Mutagen formKey 只有 6 位局部 FormID（如 `00F5AE:Artaeum.esp`），
   而 XML EDID 写的是 8 位 load-order FormID（如 `[0600F5AE]`）。前缀（load 位）不写死：
   从目标 XML 采样括号 EDID，与 Mutagen 局部 FormID 交叉验证命中率，取最高频前缀。

用法:
  python convert-dialogue-context.py <plugin-stem> <moddir> [output.json]
  例: python convert-dialogue-context.py Artaeum mods/Artaeum.esp
      → 读 .work/Artaeum/context/Artaeum-mutagen-dialogue.json
      → 写 .work/Artaeum/context/Artaeum-dialogue-context.json

输出契约（确定性）：.work/<stem>/context/<stem>-dialogue-context.json，同名覆盖，禁止版本后缀。
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

stem = sys.argv[1] if len(sys.argv) > 1 else "Druadach"
moddir = sys.argv[2] if len(sys.argv) > 2 else f"mods/{stem}.esm"
out_path = sys.argv[3] if len(sys.argv) > 3 else f".work/{stem}/context/{stem}-dialogue-context.json"

root = Path(".").resolve()
src = root / f".work/{stem}/context/{stem}-mutagen-dialogue.json"
xml = root / moddir / f"{stem}_english_chinese.xml"

with src.open("r", encoding="utf-8") as f:
    mutagen = json.load(f)


def local_form_id(form_key):
    """'00F5AE:Artaeum.esp' -> '00F5AE'（不校验，交给后续匹配）。"""
    if not form_key:
        return ""
    m = re.match(r"^([0-9A-Fa-f]{6}):", form_key)
    return m.group(1).upper() if m else ""


# ---- 前缀推导：采集 XML 括号 EDID × Mutagen 局部 FormID 的交叉命中 ----
mutagen_locals = {
    local_form_id(info.get("formKey"))
    for t in mutagen.get("topics", [])
    for info in t.get("infos", [])
    if info.get("formKey")
}
mutagen_locals.discard("")

prefix_votes = Counter()
if xml.exists():
    tree = ET.parse(xml)
    for s in tree.getroot().findall(".//String"):
        edid = (s.findtext("EDID") or "").strip()
        m = re.fullmatch(r"\[([0-9A-Fa-f]{8})\]", edid)
        if not m:
            continue
        full = m.group(1).upper()
        if full[2:] in mutagen_locals:
            prefix_votes[full[:2]] += 1

if not prefix_votes:
    raise SystemExit(
        f"无法推导 FormID 前缀：XML 括号 EDID 与 Mutagen 局部 FormID 无交叉命中。"
        f"检查 {xml} 与 {src} 是否是同一插件、同一代次。"
    )

prefix, votes = prefix_votes.most_common(1)[0]
total_votes = sum(prefix_votes.values())
print(f"FormID 前缀推导: {prefix}（{votes}/{total_votes} 交叉命中；候选 {dict(prefix_votes)}）")

# xTranslator 的插件内 FormID = 前缀 + 6 位局部 FormID
def to_form_id(form_key):
    local = local_form_id(form_key)
    if not local:
        return ""
    return prefix + local


def speaker_candidates(info):
    cands = []
    if info.get("speakerName"):
        cands.append({"name": info["speakerName"], "origin": "ANAM"})
    if info.get("speakerFromCondition"):
        cands.append({"name": info["speakerFromCondition"], "origin": "GetIsID"})
    if not cands and info.get("speaker"):
        cands.append({"form_key": info["speaker"], "origin": "ANAM-formkey"})
    return cands


dialogues = []
for t in mutagen.get("topics", []):
    infos = []
    for info in t.get("infos", []):
        infos.append({
            "form_id": to_form_id(info.get("formKey")),
            "editor_id": info.get("edid") or "",
            "prompt": info.get("prompt") or "",
            "responses": info.get("responses") or [],
            "speaker_candidates": speaker_candidates(info),
            "conditions": info.get("conditions") or [],
            "previous_info_form_id": to_form_id(info.get("prev")) if info.get("prev") else "",
        })
    dialogues.append({
        "editor_id": t.get("edid") or "",
        "form_id": to_form_id(t.get("formKey")),
        "topic": t.get("topic") or "",
        "category": t.get("category") or "",
        "subtype": t.get("subtype") or "",
        "quest_edid": t.get("questEdid") or "",
        "branch": to_form_id(t.get("branch")) if t.get("branch") else "",
        "infos": infos,
    })

out = {
    "source": str(src.relative_to(root)).replace("\\", "/"),
    "generatedBy": "convert-dialogue-context",
    "form_id_prefix": prefix,
    "dialogues": dialogues,
}
with (root / out_path).open("w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

total_infos = sum(len(d["infos"]) for d in dialogues)
with_fid = sum(1 for d in dialogues for i in d["infos"] if i["form_id"])
print(f"dialogues: {len(dialogues)}, infos: {total_infos}, with form_id: {with_fid}")
print(f"-> {out_path}")
