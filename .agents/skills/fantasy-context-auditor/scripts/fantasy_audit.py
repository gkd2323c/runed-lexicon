# -*- coding: utf-8 -*-
"""奇幻违和检测器（fantasy-context-auditor）——语义层现代/现实内容扫描。

用本地 qwen3.5:9b 判断译文是否在奇幻语境中明显出戏（提到现实世界才有的事物/
概念）。定位：词表（TERM004 unconditional）漏网的语义层兜底，预筛排序器，
输出候选供 Agent 人眼复核，不自动修改任何文件。

用法（项目根目录）:
  python .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --file <res.json|translation.json>
  python .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --xml <canonical.xml> [--limit N] [--rec INFO]
  python .agents/skills/fantasy-context-auditor/scripts/fantasy_audit.py --stdin   # 每行一句

选项:
  --model     Ollama 模型（默认 qwen3.5:9b，需支持 think:false）
  --host      Ollama 地址（默认 http://127.0.0.1:11434）
  --cache     缓存 JSON 路径（默认 _tmp/data/fantasy-audit-cache.json，同句不重查）
  --no-cache  禁用缓存
  --limit N   最多查 N 句（0=不限）
  --json      输出 JSON 候选列表（默认人读文本）
  --threshold 出戏判定阈值（默认 直接取模型回答含"出戏"）

安全边界:
  - 只读。不修改 XML/译文/词库。
  - 输出为候选（预筛），不自动 FAIL；最终裁决归 Agent。
  - 需要 Ollama 在线 + qwen3.5:9b（或等效支持 think:false 的模型）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HOST = 'http://127.0.0.1:11434'
MODEL = 'qwen3.5:9b'
CACHE = '_tmp/data/fantasy-audit-cache.json'

PREFIX = (
    '这是一款剑与魔法奇幻游戏（类似上古卷轴：中世纪世界，有龙、法师、城堡、'
    '兽人、精灵、炼金术。游戏内有完整的九圣灵信仰体系（斯坦达尔、玛拉、塔洛斯、'
    '凯娜瑞斯等诸神）、圣骑士、祭司、神殿，有王国、领主、商会、税收、军队、'
    '报告文书、骑士团。没有现代科技、互联网、现代企业制度）。'
    '下面是一句游戏台词。判断它是否明显出戏（提到现实世界才有的事物或概念，'
    '在奇幻世界显得荒唐）。只输出：正常 或 出戏。'
)


def load_cache(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    json.dump(cache, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, path)


def ask(text: str, model: str, host: str, timeout: int = 300) -> str:
    """system/user 模板调用 /api/chat；think:false 必须放顶层（放 options 会输出空）。"""
    payload = {'model': model,
               'messages': [
                   {'role': 'system', 'content': PREFIX},
                   {'role': 'user', 'content': text},
               ],
               'stream': False,
               'think': False,
               'options': {'temperature': 0, 'num_predict': 10}}
    req = urllib.request.Request(host + '/api/chat',
                                 data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        msg = json.loads(r.read().decode()).get('message', {})
        return (msg.get('content') or '').strip()


def audit(texts: list[str], model: str, host: str, cache: dict) -> list[dict]:
    out = []
    for text in texts:
        t = text.strip()
        if not t or len(t) < 4:
            continue
        if t in cache:
            verdict = cache[t]
        else:
            try:
                verdict = ask(t, model, host)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f'ERR {t[:20]}: {e}', file=sys.stderr)
                continue
            cache[t] = verdict
        if '出戏' in verdict:
            out.append({'text': t, 'verdict': verdict})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='Fantasy-context violation auditor')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--file', help='worker res JSON / translation result JSON')
    src.add_argument('--xml', help='xTranslator XML 路径')
    src.add_argument('--stdin', action='store_true', help='逐行读 stdin')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--host', default=HOST)
    ap.add_argument('--cache', default=CACHE)
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--rec', default='INFO', help='--xml 模式过滤 REC 前缀')
    a = ap.parse_args()

    cache = {} if a.no_cache else load_cache(a.cache)

    texts = []
    labels = {}
    if a.stdin:
        texts = [l.rstrip('\n') for l in sys.stdin if l.strip()]
    elif a.file:
        d = json.load(open(a.file, encoding='utf-8'))
        items = d.get('translations') or []
        for it in items:
            tr = it.get('translation') or it.get('dest') or ''
            if tr and tr != (it.get('source') or ''):
                texts.append(tr)
                labels[tr] = str(it.get('xml_index') or it.get('id') or '')
    elif a.xml:
        t = ET.parse(a.xml)
        for s in t.getroot().findall('.//String'):
            src = s.findtext('Source') or ''
            dst = s.findtext('Dest') or ''
            rec = s.findtext('REC') or ''
            if src != dst and (not a.rec or rec.startswith(a.rec)):
                texts.append(dst)
                labels[dst] = s.findtext('EDID') or str(len(labels))

    if a.limit > 0:
        texts = texts[:a.limit]

    hits = audit(texts, a.model, a.host, cache)
    if not a.no_cache:
        save_cache(a.cache, cache)

    if a.json:
        print(json.dumps([{'label': labels.get(h['text'], ''), 'text': h['text'],
                           'verdict': h['verdict']} for h in hits],
                         ensure_ascii=False, indent=1))
    else:
        print(f'扫描 {len(texts)} 句，出戏候选 {len(hits)}：')
        for h in hits:
            lab = labels.get(h['text'], '')
            print(f'  [{lab}] {h["text"][:80]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
