from dataclasses import replace
from uuid import uuid4

import pytest

from ci_runner.execution import ExecutionResult, EXPECTED_LIMITS
from ci_runner.finalization import FinalizationDenied, finalize_fixture
from test_container import permit


def report(claim, reason='completed', exit_code=0):
    return ExecutionResult(exit_code, reason, b'', b'', 0, 1.0, True,
                           EXPECTED_LIMITS.copy(), 'sha256:' + 'a' * 64,
                           claim.run_id, claim.attempt_id)


@pytest.mark.parametrize('changes', [
    {'run_id': uuid4()}, {'attempt_id': uuid4()}, {'released': False},
    {'verified_limits': {}}, {'image_id': 'arbitrary:latest'},
    {'exit_code': True}, {'exit_code': 1}, {'reason': 'lease_expired'},
    {'reason': 'runtime_preflight_rejected'}, {'reason': 'unknown'},
    {'reason': 'timeout', 'exit_code': 0}, {'reason': 'log_limit', 'exit_code': 0},
])
def test_invalid_result_never_connects(changes):
    claim = permit()
    def forbidden():
        pytest.fail('invalid report connected to DB')
    with pytest.raises(FinalizationDenied):
        finalize_fixture(claim, replace(report(claim), **changes), forbidden)
