"""P0-only Docker measurement harness; never imported by the API or Worker."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'dev.ci-runner.p0-session'
MODES = frozenset(('boundary', 'cpu', 'memory', 'pids', 'tmpfs', 'logs', 'timeout', 'success', 'failure', 'preflight_rejection'))
LOG_LIMIT = 1024 * 1024
TIMEOUT = 10


@dataclass
class Result:
    stdout: bytes
    stderr: bytes
    record: dict

    def json(self):
        return json.loads(self.stdout)


class IsolationCheck:
    def __init__(self):
        self.session = uuid.uuid4().hex
        self.image = f'ci-runner-p0-probes:{self.session}'
        self.env = os.environ.copy()
        self.env['CI_P0_HOST_CANARY'] = 'public-marker-must-not-be-inherited'
        self.report = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
                       'scope': 'trusted finite P0 probes only; not an untrusted-code runner',
                       'cases': [], 'session': self.session}
        self.built = False

    def docker(self, *args, check=True, timeout=30):
        return subprocess.run(['docker', *args], check=check, capture_output=True,
                              text=True, env=self.env, timeout=timeout)

    def setup(self):
        self.report['engine'] = json.loads(self.docker('info', '--format',
            '{{json .}}').stdout)
        # Whitelist fields; never persist Docker info containing unrelated paths/configuration.
        self.report['engine'] = {key: self.report['engine'][key] for key in
                                ('ServerVersion', 'CgroupVersion', 'CgroupDriver',
                                 'OperatingSystem', 'Architecture', 'SecurityOptions', 'KernelVersion')}
        assert self.report['engine']['CgroupVersion'] == '2', 'cgroup v2 required; refusing probes'
        self.report['source_sha256'] = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted([*(ROOT / 'fixtures/isolation').iterdir(),
                                ROOT / 'tests/isolation_support.py', ROOT / 'tests/test_isolation.py',
                                ROOT / 'scripts/verify_isolation.py']) if path.is_file()
        }
        self.docker('build', '--network=none', '--label', f'{LABEL}={self.session}',
                    '--tag', self.image, str(ROOT / 'fixtures/isolation'), timeout=180)
        self.built = True
        self.report['image_id'] = self.docker('image', 'inspect', self.image,
                                             '--format', '{{.Id}}').stdout.strip()
        self.report['git_head_at_execution'] = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()

    def run(self, mode):
        if mode not in MODES:
            raise ValueError('only reviewed fixed P0 probe names are allowed')
        cid = None
        process = None
        selector = selectors.DefaultSelector()
        out, err = bytearray(), bytearray()
        observed = {'stdout': 0, 'stderr': 0}
        reason = 'completed'
        started = time.monotonic()
        record = {'mode': mode, 'log_limit_bytes': LOG_LIMIT, 'timeout_seconds': TIMEOUT}
        self.report['cases'].append(record)
        try:
            memory_limit = '64m' if mode == 'preflight_rejection' else '128m'
            command = 'cpu' if mode == 'preflight_rejection' else mode
            cid = self.docker(
                'create', '--name', f'ci-runner-p0-{self.session[:12]}-{mode}',
                '--label', f'{LABEL}={self.session}', '--user', '65534:65534',
                '--cpus', '0.5', '--memory', memory_limit, '--memory-swap', memory_limit,
                '--pids-limit', '64', '--network', 'none', '--read-only',
                '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
                '--log-driver', 'none', self.image, command).stdout.strip()
            record['container_id'] = cid
            # No inherited environment values are passed as docker --env options.
            started = time.monotonic()
            process = subprocess.Popen(['docker', 'start', '--attach', cid],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       stdin=subprocess.DEVNULL, env=self.env)
            for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                if reason == 'completed' and time.monotonic() - started >= TIMEOUT:
                    reason = 'timeout'
                    self.docker('kill', cid)
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    observed[key.data] += len(chunk)
                    target = out if key.data == 'stdout' else err
                    remaining = max(0, LOG_LIMIT - len(out) - len(err))
                    target.extend(chunk[:remaining])
                    if reason == 'completed' and sum(observed.values()) > LOG_LIMIT:
                        reason = 'log_limit'
                        # Kill the container, not merely the attached Docker client.
                        self.docker('kill', cid, check=False)
                if time.monotonic() - started > TIMEOUT + 10:
                    raise TimeoutError('attach did not close after container deadline/kill')
            process.wait(timeout=5)
            info = json.loads(self.docker('inspect', cid).stdout)[0]
            assert not info['State']['Running'] and info['State']['Pid'] == 0
            assert info['State']['Status'] == 'exited', 'container failed to start'
            config = info['HostConfig']
            record.update(
                reason=reason, elapsed_seconds=time.monotonic() - started,
                exit_code=info['State']['ExitCode'], oom_killed=info['State']['OOMKilled'],
                state_pid=info['State']['Pid'],
                retained_bytes={'stdout': len(out), 'stderr': len(err)},
                observed_bytes=observed,
                retained_sha256={'stdout': hashlib.sha256(out).hexdigest(),
                                 'stderr': hashlib.sha256(err).hexdigest()},
                settings={key: config[key] for key in ('NanoCpus', 'Memory', 'MemorySwap',
                    'PidsLimit', 'NetworkMode', 'ReadonlyRootfs', 'CapDrop', 'SecurityOpt',
                    'LogConfig', 'Binds', 'Tmpfs', 'Privileged', 'PidMode', 'IpcMode',
                    'PortBindings', 'Devices', 'DeviceRequests')},
                mounts=info['Mounts'], user=info['Config']['User'],
            )
            if len(out) < 8192 and mode not in ('logs',):
                record['stdout_text'] = out.decode('utf-8', errors='replace')
            if len(err) < 8192:
                record['stderr_text'] = err.decode('utf-8', errors='replace')
            return Result(bytes(out), bytes(err), record)
        finally:
            selector.close()
            if cid:
                # Operate only on the ID created above. No global prune or name-based deletion.
                self.docker('rm', '--force', '--volumes', cid)
                record['removed'] = not self.docker('ps', '-aq', '--filter', f'id={cid}').stdout.strip()
                assert record['removed'], 'container still exists after removal'
            if process:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def close(self, failures):
        try:
            remaining = self.docker('ps', '-aq', '--filter', f'label={LABEL}={self.session}').stdout.split()
            self.report['remaining_containers_before_final_cleanup'] = remaining
            for cid in remaining:
                self.docker('rm', '--force', '--volumes', cid)
            assert not remaining, 'per-case cleanup was incomplete'
        finally:
            if self.built:
                self.docker('image', 'rm', self.image)
            self.report['pytest_failures_at_teardown'] = failures
            self.report['finished_at'] = datetime.now(timezone.utc).isoformat()
            evidence = os.environ.get('CI_ISOLATION_EVIDENCE')
            if evidence:
                path = Path(evidence).resolve()
                # Evidence output is confined to this project's docs or ignored artifacts.
                assert any(path.is_relative_to(ROOT / d) for d in ('docs', 'artifacts'))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.report, indent=2) + '\n')
