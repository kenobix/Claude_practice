from dataclasses import replace

import pytest

from ci_runner import commit_source, database, runs, worker
from test_commit_source import GitHubFixture, HEAD
from test_runs_postgres import dedicated_database

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('concurrent_claim', [False, True])
def test_source_verification_checks_real_queue_without_claiming(
    dedicated_database, monkeypatch, concurrent_claim,
):
    run = runs.create_run(database.connect, HEAD)[0]

    def github():
        if concurrent_claim:
            # A second DB connection can take the run during acquisition.
            assert worker.claim_next('concurrent-claimant').run_id == run.id
        return GitHubFixture(HEAD)

    monkeypatch.setattr(commit_source, '_GitHub', github)
    if concurrent_claim:
        with pytest.raises(commit_source.QueueCandidateChanged, match='run or SHA changed') as error:
            commit_source.verify_queued_source()
        assert error.value.target == (run.id, HEAD)
    else:
        result = commit_source.verify_queued_source()
        assert (result.run_id, result.commit_sha) == (run.id, HEAD)
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'] == int(concurrent_claim)
        assert conn.execute('SELECT state FROM runs WHERE id=%s', (run.id,)).fetchone()['state'] == (
            'running' if concurrent_claim else 'queued'
        )


def test_wrong_verified_identity_is_not_a_queue_change(dedicated_database, monkeypatch):
    runs.create_run(database.connect, HEAD)
    verify = commit_source._verify
    monkeypatch.setattr(commit_source, '_verify',
                        lambda run_id, sha, _: replace(verify(run_id, sha, GitHubFixture(sha)), commit_sha='f'*40))
    with pytest.raises(commit_source.SourceRejected, match='verified source does not match') as error:
        commit_source.verify_queued_source()
    assert not isinstance(error.value, commit_source.QueueCandidateChanged)
