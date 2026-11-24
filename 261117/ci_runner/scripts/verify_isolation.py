"""Run only the reviewed P0 Docker tests; never execute fixtures on the host."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=ROOT / 'artifacts/isolation-latest.json',
                        help='replace the generated evidence JSON under project docs/ or artifacts/')
    args = parser.parse_args()
    path = args.evidence.resolve()
    if not any(path.is_relative_to(ROOT / directory) for directory in ('docs', 'artifacts')):
        parser.error('evidence must be under this project docs/ or artifacts/')
    env = os.environ.copy()
    env.update(CI_RUN_DOCKER_TESTS='1', CI_ISOLATION_EVIDENCE=str(path))
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-ra',
                           str(ROOT / 'tests/test_isolation.py')], cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
