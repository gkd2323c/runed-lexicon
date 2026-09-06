"""Apply an explicit multi-file correction manifest using the existing filler.

All results are generated in a sibling staging directory. Publish only to a new
output directory after every filler succeeds; original inputs are never changed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


class SetFillError(Exception):
    pass


def fill_set(manifest_path, output_path):
    manifest_path = Path(manifest_path).resolve()
    output = Path(output_path).absolute()
    if output.exists():
        raise SetFillError('output directory already exists')
    if not output.parent.is_dir():
        raise SetFillError('output parent must already exist')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise SetFillError('manifest requires schema_version=1')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise SetFillError('manifest requires nonempty files array')
    jobs, names, sources = [], set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SetFillError('invalid manifest entry')
        for key in ('result', 'map', 'output_name'):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise SetFillError(f'missing {key}')
        name = entry['output_name']
        if Path(name).name != name or '/' in name or '\\' in name or ':' in name or not name.endswith('.json'):
            raise SetFillError('output_name must be a plain .json filename')
        if name.casefold() in names:
            raise SetFillError('duplicate output_name')
        names.add(name.casefold())
        result = (manifest_path.parent / entry['result']).resolve()
        mapping = (manifest_path.parent / entry['map']).resolve()
        if result in sources:
            raise SetFillError('duplicate result file')
        sources.add(result)
        if not result.is_file() or not mapping.is_file():
            raise SetFillError('result or map missing')
        for key in ('overwrite', 'by_source'):
            if key in entry and type(entry[key]) is not bool:
                raise SetFillError(f'{key} must be boolean')
        # Freeze inputs before subprocess work; each job reads its private copy.
        result_bytes, map_bytes = result.read_bytes(), mapping.read_bytes()
        data = json.loads(result_bytes)
        patch = json.loads(map_bytes)
        if not isinstance(data, dict) or not isinstance(data.get('translations'), list):
            raise SetFillError('invalid result schema')
        if not isinstance(patch, dict):
            raise SetFillError('map must be an object')
        if entry.get('overwrite') and any(not isinstance(v, dict) or 'expected_translation' not in v for v in patch.values()):
            raise SetFillError('overwrite maps require expected_translation on every entry')
        jobs.append((entry, result_bytes, map_bytes))
    stage = Path(tempfile.mkdtemp(prefix='.fill-set-', dir=output.parent))
    try:
        inputs = stage / 'inputs'
        ready = stage / 'ready'
        inputs.mkdir()
        ready.mkdir()
        filler = Path(__file__).with_name('fill_translations.py')
        for i, (entry, result_bytes, map_bytes) in enumerate(jobs):
            result_copy, map_copy = inputs / f'{i}-result.json', inputs / f'{i}-map.json'
            result_copy.write_bytes(result_bytes)
            map_copy.write_bytes(map_bytes)
            command = [sys.executable, str(filler), '--result', str(result_copy),
                       '--map', str(map_copy), '--output', str(ready / entry['output_name'])]
            if entry.get('overwrite'):
                command.append('--overwrite')
            if entry.get('by_source'):
                command.append('--by-source')
            completed = subprocess.run(command, capture_output=True)
            if completed.returncode:
                detail = (completed.stdout + completed.stderr).decode('utf-8', errors='replace')
                raise SetFillError(f'job {i} failed; no output directory published: {detail}')
        if output.exists():
            raise SetFillError('output appeared during processing; refusing publication')
        # Same-filesystem directory rename. This publishes copies, not an in-place transaction.
        os.rename(ready, output)
    finally:
        shutil.rmtree(stage)
    return len(jobs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        count = fill_set(args.manifest, args.output_dir)
        print(f'Published {count} result copies; originals unchanged; validation/semantic review still required')
        return 0
    except (SetFillError, OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
