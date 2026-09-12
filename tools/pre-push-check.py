#!/usr/bin/env python3
"""本地 pre-push 校验：复刻 .github/workflows/ci.yml 的四个步骤。

与 ci.yml 保持同步：改 CI 时同步改这里，改这里时同步检查 CI。
四个步骤（与 CI 同名同序）：
  1. Unit tests (unittest discover per skill)
  2. Standalone regression scripts
  3. Skill quick validation (all skills)
  4. Syntax floor check (whole repo)

第 4 步的具体版本由运行环境决定，语义恒定：编译全库 .py，回答
「这个解释器版本能不能解析仓库里所有 Python」。CI 每条支持版本的腿
各跑一次（最低腿 3.10 因此是真正的下限）；本地则挑本机可用的最低版本，
把高版本语法在推送前就挡下来。

用法：
  python tools/pre-push-check.py            # 跑全部四步，失败 exit 1
  python tools/pre-push-check.py --syntax-only    # 只跑第 4 步（CI 调用入口）
  python tools/pre-push-check.py --install-hook   # 安装 .git/hooks/pre-push

环境变量 SYNTAX_FLOOR_PY 可指定用于第 4 步的解释器路径。
"""
import glob
import os
import re
import stat
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUICK_VALIDATE = os.path.join(
    ROOT, ".agents", "skills", "skill-creator", "scripts", "quick_validate.py")
STANDALONE_SCRIPTS = [
    os.path.join(ROOT, ".agents", "skills", "xtranslator-xml-writer",
                 "scripts", "test_patch_mode.py"),
    os.path.join(ROOT, ".agents", "skills", "xtranslator-xml-writer",
                 "scripts", "test_incremental_mode.py"),
    os.path.join(ROOT, ".agents", "skills", "noninfo-batch-planner",
                 "scripts", "test_plan_noninfo.py"),
    os.path.join(ROOT, ".agents", "skills", "translation-review-tools",
                 "scripts", "test_review_tools.py"),
    os.path.join(ROOT, ".agents", "skills", "translation-quality-gate",
                 "scripts", "selftest_corpus.py"),
    os.path.join(ROOT, ".agents", "skills", "translation-fidelity-scan",
                 "scripts", "test_fidelity_scan.py"),
    os.path.join(ROOT, "corpus", "translation-regression",
                 "scripts", "validate_corpus.py"),
]
# Floor-version syntax check: the repo must still parse below this line.
# 3.12 is where PEP 701 landed (f-strings may reuse their own quote char),
# so a 3.10/3.11 leg is what actually catches that class of regression.
FLOOR_MAX = (3, 12)

# Fallback-only: used when git is unavailable, since git normally supplies the
# exact first-party file list (tracked + untracked, ignored excluded).
SYNTAX_SKIP_DIRS = {".git", "_tmp", "__pycache__", "node_modules", ".venv",
                    "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                    ".work", "_quarantine"}
SYNTAX_SKIP_REL = {os.path.join("tools", "xEdit"),
                   os.path.join("tools", "Mutagen")}

HOOK_PATH = os.path.join(ROOT, ".git", "hooks", "pre-push")
HOOK_BODY = """#!/bin/sh
# auto-installed by tools/pre-push-check.py --install-hook
# push 前复刻 CI 三步，失败则阻断推送。
python tools/pre-push-check.py
"""


def run(cmd, cwd=ROOT):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, time.time() - t0


def step_unittest(failures):
    print("=== [1/4] Unit tests (unittest discover per skill)")
    found = 0
    pattern = os.path.join(ROOT, ".agents", "skills", "*", "scripts")
    for d in sorted(glob.glob(pattern)):
        tests = glob.glob(os.path.join(d, "test_*.py"))
        if not tests:
            continue
        has_unittest = False
        for f in tests:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if "unittest" in fh.read():
                    has_unittest = True
                    break
        if not has_unittest:
            print(f"--- skip discover (standalone-style only): {d}")
            continue
        print(f"--- unittest discover: {d}")
        rc, out, err, dt = run(
            [sys.executable, "-m", "unittest", "discover", "-s", d, "-p", "test_*.py"])
        print(f"    rc={rc} ({dt:.1f}s) {((err or out).strip().splitlines() or [''])[-1]}")
        found += 1
        if rc != 0:
            failures.append(f"unittest discover: {d}")
    if found == 0:
        failures.append("no unittest-style tests found (CI would exit 1)")
    print(f"    discover suites: {found}")


def step_standalone(failures):
    print("=== [2/4] Standalone regression scripts")
    for script in STANDALONE_SCRIPTS:
        rel = os.path.relpath(script, ROOT)
        print(f"--- {rel}")
        rc, out, err, dt = run([sys.executable, script])
        tail = ((out or "") + (err or "")).strip().splitlines()
        print(f"    rc={rc} ({dt:.1f}s) {(tail or ['(no output)'])[-1][:200]}")
        if rc != 0:
            failures.append(f"standalone: {rel}")


def step_quick_validate(failures):
    print("=== [3/4] Skill quick validation (all skills)")
    checked = 0
    pattern = os.path.join(ROOT, ".agents", "skills", "*")
    for d in sorted(glob.glob(pattern)):
        if not os.path.isfile(os.path.join(d, "SKILL.md")):
            continue
        print(f"--- quick_validate: {d}")
        rc, out, err, dt = run([sys.executable, QUICK_VALIDATE, d])
        msg = ((out or "") + (err or "")).strip().splitlines()
        print(f"    rc={rc} ({dt:.1f}s) {(msg or [''])[-1][:200]}")
        checked += 1
        if rc != 0:
            failures.append(f"quick_validate: {d}")
    print(f"    skills checked: {checked}")


def _walk_py_files():
    """Fallback walk when git is unavailable."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SYNTAX_SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT)
        if rel != "." and any(rel == p or rel.startswith(p + os.sep)
                              for p in SYNTAX_SKIP_REL):
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def iter_repo_py_files():
    """First-party .py files, as defined by git: tracked plus untracked-but-not-ignored.

    Git is the single source of truth for what belongs to the repo, so the set
    follows .gitignore automatically instead of duplicating its rules here.
    Local-only trees (.work/ runtime artefacts, .gitignore'd skills) stay out,
    which keeps this check equal to what a fresh clone would see.
    """
    try:
        p = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
             "--", "*.py"],
            cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return _walk_py_files()
    if p.returncode != 0:
        return _walk_py_files()
    names = [n for n in p.stdout.decode("utf-8", "replace").split("\0") if n]
    return sorted(os.path.join(ROOT, n.replace("/", os.sep)) for n in names)


def compile_repo_files():
    """Compile every first-party .py: (file_count, [(path, lineno, msg)])."""
    files = iter_repo_py_files()
    bad = []
    for path in files:
        try:
            # utf-8-sig mirrors how Python itself opens source files: a UTF-8
            # BOM is stripped, so a BOM'd file that imports fine does not
            # fail here as a bogus syntax error.
            with open(path, encoding="utf-8-sig") as fh:
                src = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            bad.append((path, 0, f"cannot read: {exc}"))
            continue
        try:
            compile(src, path, "exec")
        except SyntaxError as exc:
            bad.append((path, exc.lineno or 0, exc.msg or "syntax error"))
    return len(files), bad


def _python_version(cmd):
    """Version tuple if cmd runs an interpreter, else None."""
    probe = "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    try:
        p = subprocess.run(cmd + ["-c", probe], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", (p.stdout or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def _uv_interpreter(minor):
    """uv-managed interpreter argv, or None. uv writes diagnostics to stderr
    (a broken .venv in the cwd is common), so only stdout is trusted here."""
    try:
        p = subprocess.run(["uv", "python", "find", f"3.{minor}"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed([x.strip() for x in (p.stdout or "").splitlines() if x.strip()]):
        if os.path.isfile(line):
            return [line]
    return None


def find_floor_interpreter():
    """Lowest usable interpreter below FLOOR_MAX, as (argv, version).

    Lowest wins on purpose: 3.10 is the strictest test of whether the repo
    still parses on the CI floor leg. Returns (None, None) when this machine
    has nothing below the floor.
    """
    candidates = []
    env = os.environ.get("SYNTAX_FLOOR_PY")
    if env:
        candidates.append([env])
    for minor in (10, 11):
        candidates.append(["py", f"-3.{minor}"])
        candidates.append([f"python3.{minor}"])
        uv_cmd = _uv_interpreter(minor)
        if uv_cmd:
            candidates.append(uv_cmd)
    candidates.append([sys.executable])

    found = []
    seen = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        ver = _python_version(cmd)
        if ver and (ver[0], ver[1]) < FLOOR_MAX:
            found.append((ver, cmd))
    if not found:
        return None, None
    found.sort(key=lambda item: item[0])
    return found[0][1], found[0][0]


def run_syntax_only():
    """Compile the repo with the interpreter running this file."""
    count, bad = compile_repo_files()
    ver = ".".join(str(x) for x in sys.version_info[:3])
    for path, lineno, msg in bad:
        print(f"{os.path.relpath(path, ROOT)}:{lineno}: {msg}")
    print(f"syntax check: {count} files under Python {ver}; "
          f"errors: {len(bad)}")
    return 1 if bad else 0


def step_syntax_floor(failures):
    print("=== [4/4] Syntax floor check (whole repo)")
    exe, ver = find_floor_interpreter()
    ver_str = ".".join(str(x) for x in ver) if ver else "?"
    if exe is None:
        floor = f"{FLOOR_MAX[0]}.{FLOOR_MAX[1]}"
        print(f"--- SKIPPED: no Python below {floor} on this machine; "
              f"set SYNTAX_FLOOR_PY to pin one")
        print(f"    (CI still runs this on every supported version leg)")
        return
    print(f"--- interpreter: {' '.join(exe)} ({ver_str})")
    rc, out, err, dt = run(exe + [os.path.abspath(__file__), "--syntax-only"])
    for line in ((out or "") + (err or "")).strip().splitlines():
        print(f"    {line}")
    print(f"    rc={rc} ({dt:.1f}s)")
    if rc != 0:
        failures.append(f"syntax floor (Python {ver_str})")


def install_hook():
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        print("no .git directory; hook not installed")
        return 1
    with open(HOOK_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(HOOK_BODY)
    try:
        st = os.stat(HOOK_PATH)
        os.chmod(HOOK_PATH, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    print(f"installed {HOOK_PATH}")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--install-hook":
        return install_hook()
    if len(sys.argv) > 1 and sys.argv[1] == "--syntax-only":
        return run_syntax_only()
    t0 = time.time()
    failures = []
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("missing dependency: pyyaml (CI installs pyyaml zhconv; "
              "run: pip install pyyaml zhconv)")
        return 1
    step_unittest(failures)
    step_standalone(failures)
    step_quick_validate(failures)
    step_syntax_floor(failures)
    dt = time.time() - t0
    print(f"=== done in {dt:.1f}s")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL OK (mirrors CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
