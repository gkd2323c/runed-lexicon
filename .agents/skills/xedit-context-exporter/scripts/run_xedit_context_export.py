#!/usr/bin/env python3
"""Run xEdit headlessly enough for deterministic dialogue-context export.

The source plugin and real game Data directory are never modified.  xEdit sees a
project-local staging Data directory containing symlinks to the required files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SKILL_ROOT = Path(__file__).resolve().parents[1]
PASCAL_SCRIPT = Path(__file__).with_name("ExportDialogueContext.pas")
DEFAULT_XEDIT_DIR = PROJECT_ROOT / "tools" / "xEdit"
WORK_ROOT = PROJECT_ROOT / ".work" / "xedit-context-exporter"
STAGE_DATA = WORK_ROOT / "Data"
CACHE_DIR = WORK_ROOT / "Cache"
TEMP_DIR = WORK_ROOT / "Temp"
PLUGINS_TXT = WORK_ROOT / "plugins.txt"
XEDIT_OUTPUT_NAME = "runed_lexicon_dialogue_context.json"
CAPTURED_OUTPUT = WORK_ROOT / "captured_dialogue_context.json"
TARGET_PLUGIN_CONFIG = STAGE_DATA / "runed_lexicon_target_plugin.txt"


class ExportError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def read_tes4_masters(plugin: Path) -> list[str]:
    """Read MAST subrecords from the TES4 header without parsing the full plugin."""
    data = plugin.read_bytes()
    if len(data) < 24 or data[:4] != b"TES4":
        raise ExportError(f"Not a Bethesda TES4-format plugin: {plugin}")

    record_size = int.from_bytes(data[4:8], "little")
    start = 24
    end = start + record_size
    if end > len(data):
        raise ExportError(f"Truncated TES4 header in: {plugin}")

    masters: list[str] = []
    pos = start
    extended_size: int | None = None
    while pos + 6 <= end:
        signature = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 6], "little")
        pos += 6

        if signature == b"XXXX":
            if size != 4 or pos + 4 > end:
                raise ExportError(f"Malformed XXXX subrecord in TES4 header: {plugin}")
            extended_size = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
            continue

        actual_size = extended_size if extended_size is not None else size
        extended_size = None
        if pos + actual_size > end:
            raise ExportError(f"Malformed TES4 subrecord in: {plugin}")

        payload = data[pos : pos + actual_size]
        pos += actual_size
        if signature == b"MAST":
            name = payload.split(b"\0", 1)[0].decode("ascii", errors="strict")
            if not name:
                raise ExportError(f"Empty MAST name in: {plugin}")
            masters.append(name)

    return masters


def discover_game_data(explicit: str | None) -> Path:
    if explicit:
        data_dir = resolve_path(explicit)
        if not data_dir.is_dir():
            raise ExportError(f"Skyrim Data directory does not exist: {data_dir}")
        return data_dir

    if os.name != "nt":
        raise ExportError("Automatic Skyrim SE Data discovery is currently Windows-only")

    try:
        import winreg

        registry_candidates = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
        ]
        access_modes = [winreg.KEY_READ | winreg.KEY_WOW64_32KEY, winreg.KEY_READ | winreg.KEY_WOW64_64KEY]
        for hive, key_name in registry_candidates:
            for access in access_modes:
                try:
                    with winreg.OpenKey(hive, key_name, 0, access) as key:
                        installed, _ = winreg.QueryValueEx(key, "Installed Path")
                except OSError:
                    continue
                data_dir = Path(installed) / "Data"
                if data_dir.is_dir():
                    return data_dir.resolve()
    except ImportError:
        pass

    raise ExportError(
        "Could not discover Skyrim Special Edition Data directory; use --game-data"
    )


def discover_xedit(explicit: str | None) -> Path:
    if explicit:
        exe = resolve_path(explicit)
        if not exe.is_file():
            raise ExportError(f"xEdit executable does not exist: {exe}")
        return exe

    candidates = [
        DEFAULT_XEDIT_DIR / "xTESEdit64.exe",
        DEFAULT_XEDIT_DIR / "xTESEdit.exe",
        DEFAULT_XEDIT_DIR / "SSEEdit64.exe",
        DEFAULT_XEDIT_DIR / "SSEEdit.exe",
    ]
    for exe in candidates:
        if exe.is_file():
            return exe.resolve()
    raise ExportError(f"No xEdit executable found under: {DEFAULT_XEDIT_DIR}")


def resolve_context_plugin(raw: str, game_data: Path) -> Path:
    """Resolve a context-only plugin without assuming it is a target master."""
    supplied = Path(raw)
    candidates: list[Path] = []
    if supplied.is_absolute():
        candidates.append(supplied)
    else:
        candidates.append(PROJECT_ROOT / supplied)
        candidates.append(game_data / supplied)

    for candidate in candidates:
        if candidate.is_file():
            candidate = candidate.resolve()
            if candidate.suffix.lower() not in {".esp", ".esm", ".esl"}:
                raise ExportError(f"Unsupported context plugin extension: {candidate}")
            return candidate
    raise ExportError(f"Context plugin not found: {raw}")


def collect_context_load_order(
    context_plugins: list[Path],
    target_plugin: Path,
    target_masters: list[str],
    game_data: Path,
) -> list[Path]:
    """Return extra dependencies/plugins in dependency-first order.

    Target masters and the target plugin are treated as already loaded. Extra
    dependencies are context only; they are not rewritten into the target's
    TES4 master list.
    """
    loaded = {name.casefold() for name in target_masters}
    loaded.add(target_plugin.name.casefold())
    visiting: set[str] = set()
    ordered: list[Path] = []

    def visit(plugin_path: Path) -> None:
        key = plugin_path.name.casefold()
        if key in loaded:
            return
        if key in visiting:
            raise ExportError(f"Cyclic context-plugin dependency involving: {plugin_path.name}")
        visiting.add(key)
        for master in read_tes4_masters(plugin_path):
            master_key = master.casefold()
            if master_key in loaded:
                continue
            master_path = game_data / master
            if not master_path.is_file():
                raise ExportError(
                    f"Context plugin {plugin_path.name} requires missing master: {master_path}"
                )
            visit(master_path.resolve())
        visiting.remove(key)
        loaded.add(key)
        ordered.append(plugin_path)

    for context_plugin in context_plugins:
        visit(context_plugin)
    return ordered


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def make_symlink(link: Path, target: Path) -> None:
    if link.exists() or link.is_symlink():
        remove_path(link)
    try:
        link.symlink_to(target, target_is_directory=False)
    except OSError as exc:
        raise ExportError(
            f"Could not create staging symlink {link} -> {target}: {exc}. "
            "Enable Windows Developer Mode or provide an environment where file symlinks are allowed."
        ) from exc


def prepare_staging(
    plugin: Path,
    masters: list[str],
    game_data: Path,
    context_files: list[Path],
) -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    STAGE_DATA.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    expected_names = set(masters + [plugin.name] + [path.name for path in context_files])
    expected_names.add(TARGET_PLUGIN_CONFIG.name)
    for child in STAGE_DATA.iterdir():
        if child.name == XEDIT_OUTPUT_NAME:
            child.unlink(missing_ok=True)
            continue
        if child.name not in expected_names:
            remove_path(child)

    for master in masters:
        source = (game_data / master).resolve()
        if not source.is_file():
            raise ExportError(f"Required master is missing from Skyrim Data: {source}")
        make_symlink(STAGE_DATA / master, source)

    make_symlink(STAGE_DATA / plugin.name, plugin)
    for context_file in context_files:
        make_symlink(STAGE_DATA / context_file.name, context_file)

    TARGET_PLUGIN_CONFIG.write_text(plugin.name + "\n", encoding="utf-8")
    PLUGINS_TXT.write_text(
        "".join(
            f"*{name}\n"
            for name in masters + [plugin.name] + [path.name for path in context_files]
        ),
        encoding="utf-8",
    )


def run_xedit(
    exe: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    output_path = STAGE_DATA / XEDIT_OUTPUT_NAME
    output_path.unlink(missing_ok=True)
    CAPTURED_OUTPUT.unlink(missing_ok=True)

    command = [
        str(exe),
        "-SSE",
        f"-D:{STAGE_DATA}",
        f"-P:{PLUGINS_TXT}",
        "-autoload",
        f"-script:{PASCAL_SCRIPT}",
        "-autoexit",
        f"-T:{TEMP_DIR}",
        f"-C:{CACHE_DIR}",
    ]

    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + timeout_seconds
    last_size: int | None = None
    stable_since: float | None = None

    try:
        while time.monotonic() < deadline:
            if output_path.is_file():
                size = output_path.stat().st_size
                if size == last_size and size > 0:
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 1.0:
                        # Capture the finished JSON before xEdit begins its
                        # shutdown cleanup.  In xEdit 4.1.5f the staging copy
                        # can disappear while the process is closing even
                        # though the script wrote it successfully.
                        shutil.copy2(output_path, CAPTURED_OUTPUT)
                        # xEdit 4.1.5f has been observed to remain idle in
                        # SSEScript mode even after a startup script writes its
                        # final output.  The output marker is therefore our
                        # completion signal.  Give -autoexit a brief chance,
                        # then terminate this read-only process if necessary.
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            try:
                                process.wait(timeout=5.0)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5.0)
                        return subprocess.CompletedProcess(
                            command, process.returncode or 0, ""
                        )
                else:
                    last_size = size
                    stable_since = None

            returncode = process.poll()
            if returncode is not None:
                if output_path.is_file() and not CAPTURED_OUTPUT.is_file():
                    shutil.copy2(output_path, CAPTURED_OUTPUT)
                return subprocess.CompletedProcess(command, returncode, "")
            time.sleep(0.5)
    finally:
        if process.poll() is None and time.monotonic() >= deadline:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)

    raise ExportError(
        f"xEdit did not produce the expected output within {timeout_seconds} seconds. "
        "This can be a slow first cache build or a blocked xEdit dialog; no source plugin was modified."
    )


def validate_payload(payload: object, plugin: Path) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ExportError("xEdit output root is not a JSON object")
    plugin_obj = payload.get("plugin")
    if not isinstance(plugin_obj, dict) or plugin_obj.get("filename") != plugin.name:
        raise ExportError(
            f"xEdit output target mismatch: expected {plugin.name!r}, got {plugin_obj!r}"
        )
    dialogues = payload.get("dialogues")
    if not isinstance(dialogues, list):
        raise ExportError("xEdit output is missing dialogues array")
    return payload


def write_final_output(payload: dict[str, object], output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise ExportError(f"Output already exists: {output}. Use --force to replace it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin", help="Original ESP/ESM/ESL to inspect")
    parser.add_argument("--output", help="Destination JSON path")
    parser.add_argument("--game-data", help="Skyrim Special Edition Data directory")
    parser.add_argument("--xedit", help="Path to xEdit executable")
    parser.add_argument(
        "--context-plugin",
        action="append",
        default=[],
        help=(
            "Load an additional plugin as read-only context without treating it as a target master. "
            "Repeat for multiple plugins. A bare filename is resolved from Skyrim Data."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Maximum xEdit runtime in seconds (default: 600)",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing output")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    return args


def main() -> int:
    args = parse_args()
    try:
        plugin = resolve_path(args.plugin)
        if not plugin.is_file() or plugin.suffix.lower() not in {".esp", ".esm", ".esl"}:
            raise ExportError(f"Target plugin does not exist or has unsupported extension: {plugin}")

        output = (
            resolve_path(args.output)
            if args.output
            else plugin.with_name(f"{plugin.stem}_dialogue_context.json")
        )
        game_data = discover_game_data(args.game_data)
        xedit = discover_xedit(args.xedit)
        masters = read_tes4_masters(plugin)
        requested_context_plugins = [
            resolve_context_plugin(raw, game_data) for raw in args.context_plugin
        ]
        context_files = collect_context_load_order(
            requested_context_plugins, plugin, masters, game_data
        )
        before_hash = sha256(plugin)

        prepare_staging(plugin, masters, game_data, context_files)
        result = run_xedit(xedit, args.timeout)

        raw_output = CAPTURED_OUTPUT
        if not raw_output.is_file():
            staging_output = STAGE_DATA / XEDIT_OUTPUT_NAME
            if staging_output.is_file():
                raw_output = staging_output
        if not raw_output.is_file():
            raise ExportError(
                "xEdit exited without producing the expected JSON output. "
                f"Exit code: {result.returncode}."
            )

        payload = validate_payload(json.loads(raw_output.read_text(encoding="utf-8-sig")), plugin)
        after_hash = sha256(plugin)
        if after_hash != before_hash:
            raise ExportError("Source plugin hash changed during read-only export")

        write_final_output(payload, output, args.force)
        raw_output.unlink(missing_ok=True)
        CAPTURED_OUTPUT.unlink(missing_ok=True)

        print(f"Plugin: {plugin}")
        print(f"Masters: {', '.join(masters) if masters else '<none>'}")
        if requested_context_plugins:
            print(
                "Context plugins: "
                + ", ".join(path.name for path in requested_context_plugins)
            )
        print(f"Dialogue records: {len(payload['dialogues'])}")
        info_count = sum(
            len(item.get("infos", []))
            for item in payload["dialogues"]
            if isinstance(item, dict)
        )
        print(f"INFO records: {info_count}")
        print(f"Source SHA-256 unchanged: {before_hash}")
        print(f"Wrote: {output}")
        return 0
    except (ExportError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

