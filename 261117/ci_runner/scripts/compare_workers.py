"""P5 comparison against an owned PostgreSQL and real Docker, with fixed GitHub responses."""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
from multiprocessing import get_context
import os
from pathlib import Path
import platform
import signal
from statistics import quantiles
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from ci_runner import commit_source, container, fixture_worker, runs
from ci_runner.settings import POLICY, SCHEMA_VERSION
from verify_db import ROOT, disposable_database

# Test transport only. The Worker still performs its normal SHA, blob and DB checks.
sys.path.insert(0, str(ROOT / 'tests'))
from test_commit_source import GitHubFixture

ARM_ORDER = (1, 2, 2, 1, 1, 2)
SAMPLE_INTERVAL = 0.5
ARM_TIMEOUT = 45 * 60

ATTEMPTS = """
    SELECT r.id AS run_id, r.commit_sha, r.state AS run_state, r.completed_attempt_id,
           a.id AS attempt_id, a.attempt_no, a.worker_id, a.state AS attempt_state, a.reason,
           a.exit_code, a.finished_at,
           (1000 * EXTRACT(EPOCH FROM (a.started_at-a.claimable_at)))::double precision AS queue_ms,
           a.execution_ms, s.commit_sha AS source_commit_sha, s.source_sha256, s.image_id,
           l.attempt_id IS NOT NULL AS has_logs, f.attempt_id IS NOT NULL AS has_result
    FROM runs r LEFT JOIN attempts a ON a.run_id=r.id
    LEFT JOIN attempt_sources s ON s.attempt_id=a.id
    LEFT JOIN attempt_logs l ON l.attempt_id=a.id
    LEFT JOIN artifacts f ON f.attempt_id=a.id AND f.relative_name='result.json'
    WHERE r.id=ANY(%s) ORDER BY r.commit_sha, a.attempt_no
"""


class InvalidComparison(Exception):
    pass


def fixture_commits(count, phase):
    return [hashlib.sha1(f'ci-runner-comparison-v1/{phase}/{i}'.encode(),
                         usedforsecurity=False).hexdigest() for i in range(count)]


class FixedGitHub:
    def __init__(self, commits):
        self.responses = {}
        for sha in commits:
            self.responses.update(GitHubFixture(sha).responses)

    def get(self, endpoint):
        if endpoint not in self.responses:
            raise commit_source.SourceRejected('outside comparison data')
        return self.responses[endpoint]


def run_resident(worker_id, dsn, commits, output):
    os.environ['DATABASE_URL'] = dsn
    github = FixedGitHub(commits)
    commit_source._GitHub = lambda: github
    with (output.with_suffix('.out').open('w') as stdout, output.with_suffix('.err').open('w') as stderr,
          redirect_stdout(stdout), redirect_stderr(stderr)):
        raise SystemExit(fixture_worker.main(['--serve', '--worker-id', worker_id]))


@contextmanager
def workers(count, dsn, commits):
    ctx = get_context('fork')
    processes = []
    with TemporaryDirectory(prefix='ci-runner-p5-workers-') as directory:
        try:
            for index in range(1, count + 1):
                worker_id = f'comparison-{index}'
                output = Path(directory) / worker_id
                process = ctx.Process(target=run_resident, args=(worker_id, dsn, commits, output), name=worker_id)
                process.start()
                processes.append((process, output))
            yield processes
        finally:
            for process, _ in processes:
                if process.is_alive():
                    process.terminate()
            for process, _ in processes:
                process.join(timeout=300)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            errors = [(process.name, process.exitcode) for process, output in processes
                      if process.exitcode != 0 or (output.with_suffix('.err').exists()
                                                   and output.with_suffix('.err').stat().st_size)]
            for process, _ in processes:
                process.close()
            if errors:
                raise InvalidComparison(f'Worker exited abnormally: {errors}')


def enqueue(conn, commits):
    ids = []
    with conn.transaction():
        # Make all jobs visible together, with the same DB eligibility timestamp.
        for sha in commits:
            run, created = runs.upsert_run(conn, sha)
            if not created:
                raise InvalidComparison('comparison queue is not empty')
            ids.append(run.id)
    return ids


def cpu_counters():
    fields = Path('/proc/stat').read_text().splitlines()[0].split()[1:9]
    used = [int(field) for field in fields]
    return sum(used), used[3], used[4], used[7]


def cpu_pressure():
    value = Path('/proc/pressure/cpu').read_text().splitlines()[0].split()[-1]
    return int(value.removeprefix('total='))


def wait_snapshot(conn):
    return conn.execute(
        """SELECT COALESCE(wait_event_type, 'active/no_wait') AS kind,
                  COALESCE(wait_event,'') AS event, count(*) AS n
           FROM pg_stat_activity
           WHERE datname=current_database() AND application_name='ci-runner-intake' AND state='active'
           GROUP BY 1, 2"""
    ).fetchall()


def wait_for_runs(conn, ids, processes, observations=None):
    deadline = time.monotonic() + ARM_TIMEOUT
    next_progress = 25
    while time.monotonic() < deadline:
        for process, output in processes:
            if not process.is_alive():
                # Worker phase JSON is sanitized, but don't publish arbitrary stderr.
                raise InvalidComparison(f'{output.name} stopped during the batch')
        pending = conn.execute(
            "SELECT count(*) AS n FROM runs WHERE id=ANY(%s) AND state IN ('queued','running')", (ids,)
        ).fetchone()['n']
        if not pending:
            return conn.execute(ATTEMPTS, (ids,)).fetchall()
        if observations is not None:
            complete = len(ids) - pending
            if complete >= next_progress:
                print(f'  {complete}/{len(ids)} runs reached a terminal state', flush=True)
                next_progress = (complete // 25 + 1) * 25
            counts, totals = observations
            totals['snapshots'] += 1
            for row in wait_snapshot(conn):
                counts[row['kind']][row['event']] += row['n']
        time.sleep(SAMPLE_INTERVAL)
    raise InvalidComparison(f'batch deadline exceeded with {pending} pending jobs')


def accepted_result(row):
    return (row['run_state'] == row['attempt_state'] == 'succeeded'
            and row['reason'] == 'commit_success' and row['exit_code'] == 0 and row['attempt_no'] == 1
            and row['completed_attempt_id'] == row['attempt_id'] and row['queue_ms'] is not None
            and row['queue_ms'] >= 0 and row['execution_ms'] is not None and row['execution_ms'] >= 0
            and row['commit_sha'] == row['source_commit_sha'] and row['source_sha256'] == commit_source.DIGEST
            and row['image_id'] and row['image_id'].startswith('sha256:')
            and row['has_logs'] and row['has_result'])


def resource_counts(ids):
    # Observe only resources related to this benchmark's runs. No global prune.
    ids = {str(run_id) for run_id in ids}
    containers = container.docker('ps', '-a', '--filter', f'label={container.RUN_LABEL}', '--format',
                                  '{{.Label "dev.ci-runner.run"}}').stdout.splitlines()
    images = container.docker('image', 'ls', '--filter', f'label={container.LABEL}',
                              '--format', '{{.ID}}', '--no-trunc').stdout.splitlines()
    image_runs = []
    for image_id in set(images):
        image_runs.append(container.docker('image', 'inspect', image_id, '--format',
                                           '{{index .Config.Labels "dev.ci-runner.source-run"}}').stdout.strip())
    return {'containers': sum(run_id in ids for run_id in containers),
            'images': sum(run_id in ids for run_id in image_runs)}


def batch(conn, dsn, index, count, jobs):
    warm = fixture_commits(2, 'warmup')
    target = fixture_commits(jobs, 'measured')
    conn.execute('TRUNCATE runs CASCADE')
    with workers(count, dsn, warm + target) as processes:
        warm_ids = enqueue(conn, warm)
        warm_rows = wait_for_runs(conn, warm_ids, processes)
        if len(warm_rows) != len(warm) or not all(map(accepted_result, warm_rows)):
            raise InvalidComparison('warm-up did not produce one official success per run')
        counts, totals = defaultdict(Counter), Counter()
        before_cpu, before_pressure = cpu_counters(), cpu_pressure()
        ids = enqueue(conn, target)
        rows = wait_for_runs(conn, ids, processes, (counts, totals))
        after_cpu, after_pressure = cpu_counters(), cpu_pressure()
    resources = resource_counts(ids + warm_ids)
    official = [row for row in rows if row['completed_attempt_id'] == row['attempt_id'] and row['attempt_id']]
    success = [row for row in official if accepted_result(row)]
    ends = [row['finished_at'] for row in official]
    starts = conn.execute('SELECT min(available_at) AS first FROM runs WHERE id=ANY(%s)', (ids,)).fetchone()
    delta = [end - start for start, end in zip(before_cpu, after_cpu)]
    return {
        'type': 'batch', 'arm': index, 'workers': count, 'jobs': jobs, 'warmup_jobs': len(warm),
        'official_successes': len(success),
        'unsuccessful_runs': jobs - len(success),
        'run_states': dict(Counter({row['run_id']: row['run_state'] for row in rows}.values())),
        'attempts': sum(row['attempt_id'] is not None for row in rows),
        'attempts_by_worker': dict(Counter(row['worker_id'] for row in rows if row['attempt_id'])),
        'elapsed_seconds': (max(ends) - starts['first']).total_seconds() if ends else None,
        'queue_p95_ms': p95(success, 'queue_ms'),
        'execution_p95_ms': p95(success, 'execution_ms'),
        'db_active_samples': dict(counts), 'db_snapshots': totals['snapshots'],
        'cpu_busy_pct': round(100.0 * (delta[0] - delta[1] - delta[2] - delta[3]) / delta[0], 3),
        'cpu_iowait_pct': round(100.0 * delta[2] / delta[0], 3),
        'cpu_steal_pct': round(100.0 * delta[3] / delta[0], 3),
        'cpu_pressure_some_seconds': (after_pressure - before_pressure) / 1_000_000,
        'owned_resources_remaining': resources,
        'samples': rows,
    }


def p95(rows, field):
    values = [row[field] for row in rows]
    if not values:
        return None
    return round(quantiles(values, n=100, method='inclusive')[94] if len(values) > 1 else values[0], 3)


def environment(conn, order, jobs):
    dockerfile = ROOT / 'fixtures/job/Dockerfile'
    cpu_model = next(line.partition(':')[2].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                     if line.startswith('model name'))
    return {
        'type': 'environment', 'measured_at': datetime.now(timezone.utc),
        'plan': {'arms': order, 'jobs_per_arm': jobs, 'warmup_jobs_per_arm': 2,
                 'sample_interval_seconds': SAMPLE_INTERVAL, 'arm_deadline_seconds': ARM_TIMEOUT},
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, cwd=ROOT).strip(),
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'kernel': platform.release(), 'python': platform.python_version(), 'cpu_model': cpu_model,
        'host_logical_cpus': os.cpu_count(),
        'host_mem_kib': int(Path('/proc/meminfo').read_text().splitlines()[0].split()[1]),
        'docker_version': container.docker('version', '--format', '{{.Server.Version}}').stdout.strip(),
        'docker_environment': json.loads(container.docker(
            'info', '--format', '{"cpus":{{.NCPU}},"ram_bytes":{{.MemTotal}},"arch":{{json .Architecture}}}'
        ).stdout),
        'postgres_version': conn.execute('SHOW server_version').fetchone()['server_version'],
        'postgres_image': next(line.split()[1] for line in (ROOT / 'compose.yaml').read_text().splitlines()
                               if line.strip().startswith('image:')),
        'schema_version': conn.execute('SELECT max(version) AS version FROM schema_migrations').fetchone()['version'],
        'fixture_version': POLICY.workflow_version, 'fixture_sha256': commit_source.DIGEST,
        'fixture_base_image': dockerfile.read_text().splitlines()[0].split()[1],
        'dockerfile_sha256': hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
        'github_transport': 'fixed-test-responses',
        'docker_build_cache': 'retained; warm-up before each arm',
    }


def compare(output, pilot):
    order, jobs = ((2,), 2) if pilot else (ARM_ORDER, 100)

    def record(data):
        output.write(json.dumps(data, default=str, sort_keys=True) + '\n')
        output.flush()

    project = f'ci-runner-261117-p5-{uuid4().hex[:12]}'
    with disposable_database(project, 55418) as (env, _):
        subprocess.run([sys.executable, str(ROOT / 'scripts/migrate.py')], env=env, check=True, timeout=30)
        with psycopg.connect(env['DATABASE_URL'], connect_timeout=3, row_factory=dict_row, autocommit=True,
                             application_name='comparison-observer', options='-c statement_timeout=5000') as conn:
            data = environment(conn, order, jobs)
            if data['schema_version'] != SCHEMA_VERSION:
                raise InvalidComparison('comparison DB schema not ready')
            record(data)
            for index, count in enumerate(order, 1):
                print(f'Arm {index}/{len(order)}: {jobs} jobs, {count} Worker(s)', flush=True)
                result = batch(conn, env['DATABASE_URL'], index, count, jobs)
                record(result)
                print(f'Arm {index}: {result["official_successes"]}/{jobs} official successes; '
                      f'{result["elapsed_seconds"]} elapsed seconds', flush=True)
                if (result['unsuccessful_runs'] or result['attempts'] != jobs
                        or any(result['owned_resources_remaining'].values())):
                    raise InvalidComparison('batch failed outcome, attempt count or cleanup checks')
    record({'type': 'complete', 'arms': len(order), 'total_measured_jobs': len(order) * jobs})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new JSON Lines file for evidence')
    parser.add_argument('--pilot', action='store_true', help='smoke-test path with 2 jobs and 2 Workers')
    args = parser.parse_args()

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with args.output.open('x') as output:
            compare(output, args.pilot)
    except InvalidComparison as exc:
        parser.exit(1, f'{exc}\n')
    except (OSError, psycopg.Error, subprocess.SubprocessError, container.ContainerRejected) as exc:
        parser.exit(1, f'Comparison stopped: {type(exc).__name__}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Comparison interrupted\n')


if __name__ == '__main__':
    main()
