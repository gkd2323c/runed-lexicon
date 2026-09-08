#!/usr/bin/env python3
"""本地 pre-push 校验：复刻 .github/workflows/ci.yml 的三个步骤。

与 ci.yml 保持同步：改 CI 时同步改这里，改这里时同步检查 CI。
三个步骤（与 CI 同名同序）：
  1. Unit tests (unittest discover per skill)
  2. Standalone regression scripts
  3. Skill quick validation (all skills)

用法：
  python tools/pre-push-check.py            # 跑全部三步，失败 exit 1
  python tools/pre-push-check.py --install-hook   # 安装 .git/hooks/pre-push
"""
import glob
import os
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
    os.path.join(ROOT, ".agents", "skills", "translation-quality-gate",
                 "scripts", "selftest_corpus.py"),
    os.path.join(ROOT, "corpus", "translation-regression",
                 "scripts", "validate_corpus.py"),
]
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
    print("=== [1/3] Unit tests (unittest discover per skill)")
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
    print("=== [2/3] Standalone regression scripts")
    for script in STANDALONE_SCRIPTS:
        rel = os.path.relpath(script, ROOT)
        print(f"--- {rel}")
        rc, out, err, dt = run([sys.executable, script])
        tail = ((out or "") + (err or "")).strip().splitlines()
        print(f"    rc={rc} ({dt:.1f}s) {(tail or ['(no output)'])[-1][:200]}")
        if rc != 0:
            failures.append(f"standalone: {rel}")


def step_quick_validate(failures):
    print("=== [3/3] Skill quick validation (all skills)")
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
