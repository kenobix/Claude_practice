"""Opt-in real Docker checks for the Worker preparation boundary (no start)."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    env = os.environ.copy()
    env['CI_RUN_PREPARATION_TESTS'] = '1'
    raise SystemExit(subprocess.run([
        sys.executable, '-m', 'pytest', '-q', '-ra',
        str(ROOT / 'tests/test_container.py'), str(ROOT / 'tests/test_container_docker.py')],
        cwd=ROOT, env=env).returncode)
