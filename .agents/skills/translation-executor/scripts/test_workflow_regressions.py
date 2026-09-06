"""Run all workflow-governance tests, fail if any suite fails; no live model calls."""
from pathlib import Path
import subprocess
import sys


def main():
    scripts = Path(__file__).resolve().parent
    local = scripts.parents[1] / 'local-model-translator/scripts'
    suites = [scripts / 'test_result_query.py', scripts / 'test_fill_precondition.py',
              scripts / 'test_fill_set.py', local / 'test_import_map.py',
              local / 'test_import_pipeline.py']
    failed = []
    for suite in suites:
        print(f'RUN {suite.name}', flush=True)
        if subprocess.run([sys.executable, str(suite)]).returncode:
            failed.append(suite.name)
    print('FAILED: ' + ', '.join(failed) if failed else 'PASS: all 5 workflow suites')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
