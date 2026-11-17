"""Trusted local fixtures and fail-closed guard. Execute only inside Docker."""
import json
import os
from pathlib import Path
import signal
import sys
import time


def guard():
    # A stalled control-plane DB call must not leave the guard waiting forever.
    # Linux PID 1 may ignore default terminating signals: install a handler.
    signal.signal(signal.SIGALRM, lambda signum, frame: os._exit(124))
    signal.alarm(10)
    expected = {'cpu.max': '50000 100000', 'memory.max': '134217728',
                'memory.swap.max': '0', 'pids.max': '64'}
    observed = {name: (Path('/sys/fs/cgroup') / name).read_text().strip() for name in expected}
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    if (observed != expected or os.getuid() != 65534 or os.getgid() != 65534
            or int(status['CapEff'], 16) != 0 or int(status['CapBnd'], 16) != 0
            or status['NoNewPrivs'].strip() != '1' or status['Seccomp'].strip() != '2'):
        raise ValueError('runtime restrictions do not match')
    # Only the reviewed guard runs before this acknowledgement. No job work yet.
    print(json.dumps({'ready': 1, 'limits': observed}), flush=True)
    if sys.stdin.buffer.readline(4) != b'GO\n':
        raise ValueError('execution gate not released')
    signal.alarm(0)


def main():
    mode = sys.argv[1]
    if mode not in ('success', 'failure', 'timeout', 'logs'):
        raise ValueError('unknown fixed fixture')
    guard()
    if mode == 'success':
        print('{"ok": true}', flush=True)
    elif mode == 'failure':
        print('fixed failure', file=sys.stderr, flush=True)
        return 1
    elif mode == 'timeout':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    else:
        for _ in range(128):
            os.write(1, b'o' * 16384)
            os.write(2, b'e' * 16384)
        time.sleep(60)
    return 0


if __name__ == '__main__':
    try:
        result = main()
    except (OSError, ValueError, KeyError):
        print('runtime preflight refused', file=sys.stderr, flush=True)
        result = 78
    raise SystemExit(result)
