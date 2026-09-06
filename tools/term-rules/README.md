# 术语转换规则集（term-rules）

xTranslator 术语转换规则文件，供 `skyrim-xml-tools termrules` 子命令做**只读启发式扫描**。

## 文件

- `术语转换规则.txt` — 27173 条 `StartRule/Search/Replace` 格式规则（约 3.4MB）。

## 性质（重要）

- 这是**外部译者积累的"旧译/非官方译名 → 新译"批量校准集**，不是项目 canonical
  术语资产，也不是官方词典。与 `dictionary/`（官方英中 XML 语料）和 `terms.json`
  （机器契约）地位不同。
- 规则集本身含个人偏好甚至错译，**不可盲信**。每条命中必须先经 `lookup` 对官方
  词典验证，有官方证据才改；与官方冲突的条目以官方为准。
- 已知反例：规则把 Tu'whacca（官方 图瓦卡）误写成"图尔瓦"；"农夫→农民"疑似
  个人偏好。这类命中应忽略。

## 用法

```text
python .agents/skills/skyrim-xml-tools/scripts/skyrim_xml_tools.py termrules \
  mods/<MOD>.esp/<MOD>_english_chinese_translated.xml \
  --rules tools/term-rules/术语转换规则.txt [--json]
```

工具只报"Dest 含旧译词 + 规则建议"，**绝不改翻译**。详见
`.agents/skills/skyrim-xml-tools/SKILL.md` 的 `termrules` 一节。

## Provenance

- 来源：用户上传（2026-09-04），原文件名「术语转换规则.txt」。
- 用途限定：作为 termrules 扫描的候选建议源；不作为直接改写依据。
- 再分发状态：未记录到足以确认公开再分发的授权，因此公共仓库的
  `.gitignore` 默认排除 `tools/term-rules/*.txt`。本 README 只记录接口与来源性质。
