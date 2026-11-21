"""Trusted, finite P0 probes. Run only inside the restricted test container."""
import errno
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

CGROUP = Path('/sys/fs/cgroup')


def read(name):
    return (CGROUP / name).read_text().strip()


def counters(name):
    return {key: int(value) for key, value in (line.split() for line in read(name).splitlines())}


def emit(value):
    print(json.dumps(value), flush=True)


def preflight():
    # Refuse stress work if limits are absent or different; never trust inspect alone.
    required = {'cpu.max': '50000 100000', 'memory.max': '134217728',
                'memory.swap.max': '0', 'pids.max': '64'}
    if os.getuid() != 65534:
        raise SystemExit('P0 preflight refused: uid')
    for name, expected in required.items():
        if read(name) != expected:
            raise SystemExit('P0 preflight refused: ' + name)


def denied(operation):
    try:
        operation()
    except OSError as exc:
        return exc.errno
    raise AssertionError('operation unexpectedly allowed')


def boundary():
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    readonly_errno = denied(lambda: open('/opt/write-canary', 'wb'))
    assert readonly_errno == errno.EROFS
    with open('/tmp/write-ok', 'wb') as stream:
        stream.write(b'fixed probe')
    Path('/tmp/write-ok').unlink()
    executable = Path('/tmp/noexec-check')
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    noexec_errno = denied(lambda: subprocess.run([str(executable)], check=True))
    executable.unlink()
    setuid_errno = denied(lambda: os.setuid(0))
    raw_socket_errno = denied(lambda: socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP))
    with socket.socket() as client:
        client.settimeout(0.5)
        network_errno = denied(lambda: client.connect(('192.0.2.1', 443)))
    with socket.socket(socket.AF_UNIX) as client:
        socket_errno = denied(lambda: client.connect('/var/run/docker.sock'))
    emit({
        'uid': os.getuid(), 'gid': os.getgid(),
        'cap_eff': status['CapEff'].strip(), 'cap_bnd': status['CapBnd'].strip(),
        'no_new_privs': status['NoNewPrivs'].strip(), 'seccomp': status['Seccomp'].strip(),
        'setuid_errno': setuid_errno, 'raw_socket_errno': raw_socket_errno,
        'readonly_errno': readonly_errno, 'tmp_write': True, 'tmp_noexec_errno': noexec_errno,
        'network_errno': network_errno, 'interfaces': sorted(os.listdir('/sys/class/net')),
        'docker_socket_errno': socket_errno,
        'docker_socket_present': Path('/var/run/docker.sock').exists(),
        'host_mount_present': Path('/host').exists(),
        'host_env_inherited': 'CI_P0_HOST_CANARY' in os.environ,
        'cpu_max': read('cpu.max'), 'memory_max': read('memory.max'),
        'memory_swap_max': read('memory.swap.max'), 'pids_max': read('pids.max'),
        'python_version': sys.version.split()[0],
    })


def cpu():
    before = counters('cpu.stat')
    wall = time.monotonic()
    process = time.process_time()
    until = wall + 3
    while time.monotonic() < until:
        pass
    elapsed = time.monotonic() - wall
    used = time.process_time() - process
    after = counters('cpu.stat')
    emit({'wall_seconds': elapsed, 'cpu_seconds': used, 'cpu_ratio': used / elapsed,
          'nr_throttled': after['nr_throttled'] - before['nr_throttled'],
          'throttled_usec': after['throttled_usec'] - before['throttled_usec']})


def memory():
    # Bounded to 192MiB even if the kernel ignored the checked cgroup limit.
    emit({'allocation_target_bytes': 192 * 1024 * 1024})
    blocks = []
    for _ in range(24):
        blocks.append(bytearray(8 * 1024 * 1024))
    raise AssertionError('128MiB memory limit did not stop a 192MiB allocation')


def pids():
    before = counters('pids.events')['max']
    children = []
    rejected = None
    try:
        # Finite fork loop, no recursive fork; children perform no work.
        for _ in range(80):
            try:
                child = os.fork()
            except OSError as exc:
                rejected = exc.errno
                break
            if child == 0:
                time.sleep(15)
                os._exit(0)
            children.append(child)
        emit({'children': len(children), 'fork_errno': rejected,
              'pids_current': int(read('pids.current')),
              'limit_events': counters('pids.events')['max'] - before})
    finally:
        for child in children:
            os.kill(child, signal.SIGKILL)
        for child in children:
            os.waitpid(child, 0)


def tmpfs():
    total = 0
    error = None
    try:
        with open('/tmp/fill', 'wb', buffering=0) as stream:
            for _ in range(20):
                total += stream.write(b'x' * 1024 * 1024)
    except OSError as exc:
        error = exc.errno
    finally:
        Path('/tmp/fill').unlink(missing_ok=True)
    emit({'written_bytes': total, 'write_errno': error})


def logs():
    # 4MiB total, emitted to both streams without a shell or external inputs.
    for _ in range(128):
        os.write(1, b'o' * 16384)
        os.write(2, b'e' * 16384)
    time.sleep(60)  # permit verification that the log monitor actually kills the process


def timeout():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)  # exercises the monitor's forced-kill path


def failure():
    print('fixed failure', file=sys.stderr)
    sys.exit(1)


MODES = {'boundary': boundary, 'cpu': cpu, 'memory': memory, 'pids': pids,
         'tmpfs': tmpfs, 'logs': logs, 'timeout': timeout, 'success': lambda: emit({'ok': True}),
         'failure': failure}

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode not in MODES:
        raise SystemExit('unknown fixed probe')
    preflight()
    MODES[mode]()
