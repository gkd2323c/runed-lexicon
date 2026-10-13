#!/usr/bin/env python3
"""Pre-execution guard: stop redirects that would leave a device-named file.

"Send this output nowhere" is a host property, not a portable idiom. The same
token is a device in one shell and an ordinary filename in another, so a habit
learned in one host lands in the next host as a real file:

    host                    void device        what the wrong spelling leaves
    cmd.exe                 >nul 2>nul         (nothing — the only host it fits)
    POSIX (Git Bash, WSL)   2>/dev/null        a real file named `nul`
    PowerShell              >$null (no space)  a real file named `$null`

Both failure forms are on record in this repo under corpus/engineering-incidents:
a PowerShell/CMD mix-up left `$null`, and a Git Bash command left `nul`. Each
file existed the instant its command started, which is why this guard sits in
front of execution rather than behind it — no push-time or review-time check can
see a file that non-execution would have prevented.

Behaviour
---------
Block-only, on purpose. A rewrite (the hosts' `updatedInput` channel) is honoured
by the hosts that implement it and silently ignored by the ones that do not,
which would leave the artefact-writing path open on the silent host. A block is
honoured everywhere and carries the replacement text the agent needs.

Fail-open. Unparseable stdin, a payload with no command, an unknown tool, a
missing interpreter — every one of those paths exits 0 and lets the command run.
The guard's only job is to be right about the cases it names.

Known false positive: the patterns cannot tell a redirect from quoted prose, so a
command that merely *mentions* `> nul` inside a string is blocked too. That is
the deliberate side of the trade: a needless retry, not a missing file.

Usage
-----
  python tools/command-guard.py --hook          # PreToolUse hook (event on stdin)
  python tools/command-guard.py --check "..."   # one command, verdict on stdout
  python tools/command-guard.py --self-test     # table-driven self check
"""
import json
import re
import sys

# `>nul`, `2>nul`, `> NUL`, `>>nul`, `nul.txt` — any redirect whose target is the
# reserved device name. Only cmd.exe reads it as a device.
RE_NUL = re.compile(r"(?i)>\s*nul\b")
# PowerShell's null device written with a separating space: PowerShell expands
# `$null` to an empty path, so the redirect writes a file named `$null`. The
# device form has no space: `>$null`.
RE_DOLLAR_NULL = re.compile(r"(?i)>\s+\$null\b")
# Anything mentioning cmd is treated as an explicit cmd context: the wrappers
# that legitimise `nul` all name it, while the commands that misuse it do not.
# Deliberately loose — a miss here costs a file, a needless block costs a retry.
RE_CMD_HOST = re.compile(r"(?i)\bcmd(\.exe)?\b")

NUL_REASON = (
    "`>nul` / `2>nul` is a device only under cmd.exe. In this host a POSIX "
    "shell (Git Bash, MSYS, WSL) writes a real file named `nul`. Use "
    "`>/dev/null` / `2>/dev/null`, or route the command through `cmd /c`."
)
DOLLAR_NULL_REASON = (
    "`> $null` (space before `$null`) writes a real file named `$null`. "
    "PowerShell's null device is `>$null`, with no space."
)


def reasons(command):
    """Why this command is known to leave a device-named file behind."""
    if not isinstance(command, str) or not command:
        return []
    found = []
    if RE_NUL.search(command) and not RE_CMD_HOST.search(command):
        found.append(NUL_REASON)
    if RE_DOLLAR_NULL.search(command):
        found.append(DOLLAR_NULL_REASON)
    return found


def _block(found):
    # ensure_ascii keeps the payload pure ASCII: the hook host may decode our
    # stdout under a Windows code page, and escaped text survives every one.
    print(json.dumps({"decision": "block",
                      "reason": "command-guard: " + " ".join(found)},
                     ensure_ascii=True))
    return 2


def hook():
    """PreToolUse: read the event on stdin, block a command known to misfire."""
    try:
        # The host's stdin/our stdout are UTF-8; without this a Windows Python
        # decodes the event under the locale code page, fails to parse it, and
        # fail-opens on every call — a guard that never fires.
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        event = json.load(sys.stdin)
        tool_input = event.get("tool_input") or {}
        command = tool_input.get("command") or tool_input.get("script") or ""
    except Exception:
        return 0                      # unreadable event: never block
    found = reasons(command)
    return _block(found) if found else 0


def check(argv):
    found = reasons(" ".join(argv))
    if found:
        for line in found:
            print(line)
        print("=> block")
        return 2
    print("=> allow")
    return 0


SELF_TEST = [
    # (command, expect_block)
    ("ls -la", False),
    ("py -3 tools/pre-push-check.py --hygiene-only", False),
    ("py -3 tools/command-guard.py --self-test", False),
    ('cmd /c "where.exe npx >nul 2>nul"', False),
    ('cmd.exe /d /s /c "py -3 x.py 2>nul & echo done"', False),
    ('pwsh -NoProfile -Command "Get-Date 2>$null"', False),
    ("git commit -m \"fix command guard\"", False),
    ('cd "C:/repo" && ls .work/ 2>nul', True),
    ("mkdir -p _tmp/data/nsr-review 2>nul; ls _tmp/data/nsr-review 2>nul", True),
    ('findstr /c:"rows" _tmp/data/nsr-review/INFO-146-canonical.txt 2>nul', True),
    ('type f.txt | find /c /v "" > nul', True),
    ('powershell -Command "do-thing > $null"', True),
    # Kept visible rather than papered over: prose that looks like a redirect.
    ('echo "see > nul for the old spelling"', True),
]


def self_test():
    bad = 0
    for command, expect_block in SELF_TEST:
        got = bool(reasons(command))
        if got != expect_block:
            bad += 1
            print("FAIL  expect=%s got=%s  %s"
                  % ("block" if expect_block else "allow",
                     "block" if got else "allow", command))
    print("self-test: %d/%d ok" % (len(SELF_TEST) - bad, len(SELF_TEST)))
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2:
        print("usage: command-guard.py --hook | --check <command> | --self-test")
        return 2
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "--hook":
        return hook()
    if mode == "--check":
        return check(rest)
    if mode == "--self-test":
        return self_test()
    print("unknown mode: %s" % mode)
    return 2


if __name__ == "__main__":
    sys.exit(main())
