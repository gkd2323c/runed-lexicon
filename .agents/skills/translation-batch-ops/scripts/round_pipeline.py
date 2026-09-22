# -*- coding: utf-8 -*-
"""round_pipeline.py — 一轮「验收→charset→预检→写回→段核对→快照」的唯一串行入口。

背景（2026-09-22 沉淀）：主会话手动并行编排 consume/写回/快照曾六次互相覆盖时序
（快照记入写前态 200/205/213/216/220、charset 读到 fill 前空文、统计读到写前态），
并行写 canonical 历史上曾回滚一批 44 行。本工具把整条收口链收敛为**单进程严格顺序**
执行，并持有 `.work/<stem>/reports/pipeline.lock` 独占锁；write_translations 与
progress_snapshot 内置同锁守卫（见各自 SKILL），持锁期间一切外部并发读写 canonical
的命令一律拒绝——并行在工具链层面不可发生。

步骤（--phases 可选子集，默认全跑，顺序固定）：
  consume  逐批 consume_batch（语义/契约 FAIL 即停，输出明细交主会话裁决后重跑）
  charset  逐批 normalize_charset 检查；有差异自动 apply 修复并重 consume 该批
  check    writer --check-only 预检（跨批 duplicate / scope / KEEP 冲突）
  write    逐批 write_translations --in-place 串行写回（任一批失败即停）
  verify   段核对：各批 idx 在 canonical 中 Source==Dest 计数必须为 0
  snapshot progress_snapshot --record（同进程内，写回落定后）

用法：
  py -3 round_pipeline.py --stem <stem> --batches INFO-XXX INFO-YYY \
      --xml mods/<mod>/<stem>_english_chinese.xml \
      --contract .work/<stem>/contracts/<stem>.compiled.json \
      [--phases consume,charset,check,write,verify,snapshot] \
      [--note "<快照备注>"] [--break-lock]

锁语义：
  - acquire 用 O_EXCL 创建锁文件（token/pid/phase/started）；已存在则拒绝启动，
    `--break-lock` 才清（用于上轮进程崩溃后的 stale 锁）。
  - 子进程通过 env RUNED_PIPELINE_TOKEN 继承 token；外部命令无 token → 被守卫拒绝。
  - finally 必然释放；异常/失败退出也释放（锁不跨进程遗留）。

退出码：0 全链 PASS；1 某步失败（现场明细已打印）；2 用法/锁冲突。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parent.parent
CONSUME = HERE / "consume_batch.py"
SNAPSHOT = HERE / "progress_snapshot.py"
NORMALIZE = SKILLS / "translation-review-tools" / "scripts" / "normalize_charset.py"
APPLY = SKILLS / "translation-review-tools" / "scripts" / "apply_fixes.py"
WRITER = SKILLS / "xtranslator-xml-writer" / "scripts" / "write_translations.py"

TOKEN_ENV = "RUNED_PIPELINE_TOKEN"
ALL_PHASES = ["consume", "charset", "check", "write", "verify", "snapshot"]


class Stop(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mod_dir_for(stem: str) -> str:
    cands = [stem, f"{stem}.esp", stem.removesuffix(".esp")]
    return next((m for m in cands if (Path("mods") / m).is_dir()), stem)


def run(cmd: list, phase: str) -> int:
    print(f"[{phase}] {' '.join(str(c) for c in cmd)}")
    r = subprocess.run([str(c) for c in cmd], env=os.environ.copy())
    return r.returncode


def fail_detail(report: Path) -> None:
    """consume 失败时打印 verify-report 的 fails 明细，便于主会话裁决。"""
    if not report.is_file():
        return
    try:
        d = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for f in d.get("fails", []):
        print(f"  FAIL {f.get('code', '')}: {f.get('detail', '')[:300]}")


class Lock:
    def __init__(self, path: Path):
        self.path = path
        self.token = uuid.uuid4().hex

    def acquire(self, break_stale: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if not break_stale:
                try:
                    info = json.loads(self.path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    info = {}
                raise Stop(
                    f"pipeline.lock 已存在（pid={info.get('pid')} phase={info.get('phase')} "
                    f"started={info.get('started')}）；确认无进程存活后用 --break-lock 清除",
                    code=2,
                )
            self.path.unlink()
        payload = json.dumps({
            "token": self.token,
            "pid": os.getpid(),
            "phase": "starting",
            "started": time_str(),
        }, ensure_ascii=False)
        # O_EXCL 创建，双保险
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, payload.encode("utf-8"))
        os.close(fd)
        os.environ[TOKEN_ENV] = self.token
        os.environ["RUNED_PIPELINE_LOCK"] = str(self.path.resolve())

    def phase(self, name: str) -> None:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            info = {}
        info["phase"] = name
        self.path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    def release(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        os.environ.pop(TOKEN_ENV, None)
        os.environ.pop("RUNED_PIPELINE_LOCK", None)


def time_str() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stem", required=True)
    ap.add_argument("--batches", nargs="+", required=True, help="批次号列表（执行顺序）")
    ap.add_argument("--xml", required=True, help="源 XML")
    ap.add_argument("--contract", required=True, help="编译契约 JSON")
    ap.add_argument("--phases", default=",".join(ALL_PHASES))
    ap.add_argument("--note", default="", help="快照备注")
    ap.add_argument("--break-lock", action="store_true", help="清除已存在的锁（仅 stale 时）")
    args = ap.parse_args()

    stem = args.stem
    mod = mod_dir_for(stem)
    canonical = Path(f"mods/{mod}/{stem}_english_chinese_translated.xml")
    work = Path(".work") / stem
    if not canonical.is_file():
        print(f"error: canonical 不存在: {canonical}", file=sys.stderr)
        return 2
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    bad = [p for p in phases if p not in ALL_PHASES]
    if bad or not phases:
        print(f"error: 非法 phases {bad}；可选 {ALL_PHASES}", file=sys.stderr)
        return 2

    lock = Lock(work / "reports" / "pipeline.lock")
    try:
        lock.acquire(break_stale=args.break_lock)
    except Stop as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code

    try:
        # ---- consume + charset（批次文件侧）----
        for b in args.batches:
            if "consume" in phases:
                lock.phase(f"consume {b}")
                rc = run([sys.executable, CONSUME, "--stem", stem, "--batch", b,
                          "--xml", args.xml, "--contract", args.contract], "consume")
                if rc != 0:
                    fail_detail(work / "reports" / f"{b}-verify-report.json")
                    raise Stop(f"{b} consume FAIL，已停在写回前；裁决（fix/waive）后重跑本命令")
            if "charset" in phases:
                lock.phase(f"charset {b}")
                tjson = work / "batches" / b / "translation.json"
                fixes = work / "batches" / b / "charset-fixes.json"
                if fixes.exists():
                    fixes.unlink()
                rc = run([sys.executable, NORMALIZE, "--result", str(tjson),
                          "--fixes-out", str(fixes)], "charset")
                if rc != 0:
                    raise Stop(f"{b} charset 检查进程异常（rc={rc}）")
                if fixes.exists():
                    try:
                        fd = json.loads(fixes.read_text(encoding="utf-8"))
                        has_fixes = bool(fd) if isinstance(fd, list) else bool(fd.get("fixes"))
                    except (json.JSONDecodeError, OSError):
                        has_fixes = False
                    if not has_fixes:
                        fixes.unlink(missing_ok=True)
                        has_fixes = False
                    else:
                        rc = run([sys.executable, APPLY, "--stem", stem, "--batch", b,
                                  "--fixes", str(fixes)], "charset-fix")
                        fixes.unlink(missing_ok=True)
                        if rc != 0:
                            raise Stop(f"{b} charset 修复 apply 失败")
                        rc = run([sys.executable, CONSUME, "--stem", stem, "--batch", b,
                                  "--xml", args.xml, "--contract", args.contract], "reconsume")
                        if rc != 0:
                            fail_detail(work / "reports" / f"{b}-verify-report.json")
                            raise Stop(f"{b} charset 修复后重 consume 仍 FAIL")

        results = [work / "batches" / b / "translation.json" for b in args.batches]
        writer_common = ["--xml", str(canonical), "--source-xml", args.xml,
                         "--report", str(work / "reports" / f"{stem}-writeback-report.json"),
                         "--force"]

        # ---- check-only 预检 ----
        if "check" in phases:
            lock.phase("check-only")
            cmd = [sys.executable, WRITER, *writer_common, "--in-place",
                   "--archive-to", str(work / "archive"), "--check-only"]
            for r in results:
                cmd += ["--result", str(r)]
            if run(cmd, "check") != 0:
                raise Stop("writer --check-only 预检失败，未写任何字节")

        # ---- 串行写回 ----
        written = []
        if "write" in phases:
            for b, rj in zip(args.batches, results):
                lock.phase(f"write {b}")
                cmd = [sys.executable, WRITER, *writer_common, "--in-place",
                       "--archive-to", str(work / "archive"), "--result", str(rj)]
                if run(cmd, "write") != 0:
                    raise Stop(f"{b} 写回失败；已停，后续批未写")
                written.append(b)

        # ---- 段核对 ----
        if "verify" in phases:
            lock.phase("verify")
            import xml.etree.ElementTree as ET
            rows = [s for s in ET.parse(canonical).getroot().iter("String")]
            bad_rows = []
            for b in args.batches:
                m = json.loads((work / "batches" / b / "map.json").read_text(encoding="utf-8"))
                u = sum(1 for k in m
                        if (rows[int(k)].findtext("Source") or "") == (rows[int(k)].findtext("Dest") or ""))
                if u:
                    bad_rows.append(f"{b} 未译残留 {u}/{len(m)}")
            if bad_rows:
                raise Stop("段核对失败：" + "; ".join(bad_rows))

        # ---- 快照（写回落定后，同进程）----
        if "snapshot" in phases:
            lock.phase("snapshot")
            snap_cmd = [sys.executable, SNAPSHOT, "--xml", str(canonical),
                        "--source-xml", args.xml,
                        "--plan", str(work / "context" / f"{stem}-info-batches.json"),
                        "--batches-dir", str(work / "batches"), "--record"]
            gaps = work / "context" / f"{stem}-gaps-batches.json"
            if gaps.is_file():
                snap_cmd += ["--plan", str(gaps)]
            if args.note:
                snap_cmd += ["--note", args.note]
            if run(snap_cmd, "snapshot") != 0:
                raise Stop("快照记录失败")

        digest = sha256_file(canonical)
        print("=" * 60)
        print(f"PIPELINE PASS  canonical={digest[:8]}  batches={','.join(args.batches)}"
              + (f"  written={','.join(written)}" if written else ""))
        return 0
    except Stop as e:
        print(f"PIPELINE STOP: {e}", file=sys.stderr)
        return e.code
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
