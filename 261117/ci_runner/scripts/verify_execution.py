"""Disposable DB regression plus opt-in fixed-fixture execution checks."""
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    env = os.environ.copy()
    env['CI_RUN_EXECUTION_TESTS'] = '1'
    raise SystemExit(subprocess.run([sys.executable, str(Path(__file__).with_name('verify_db.py'))],
                                   env=env).returncode)
