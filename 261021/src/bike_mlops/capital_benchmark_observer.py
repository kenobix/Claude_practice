"""Connect a measurement attempt to snapshot and response provenance checks.

This component does not establish an operational gate or authorize deployment.
The caller must validate and bind the candidate and plan before constructing it.
"""
from __future__ import annotations

from copy import deepcopy

from bike_mlops.capital_measurement_guard import verify_prediction_audit_batch
from bike_mlops.operational_benchmark import MEASURED_REQUESTS, WARMUP_REQUESTS


class CapitalBenchmarkObserver:
    def __init__(self, attempt, snapshot, sources, *, pinned_identity: dict, journal=None):
        self.attempt = attempt
        self.snapshot = snapshot
        self.sources = sources
        self.pinned_identity = dict(pinned_identity)
        self.exchanges: list[tuple[dict, dict]] = []
        self._ids: set[str] = set()
        self._pending = None
        self._marked = False
        self.journal = journal

    def before_request(self, payload: dict) -> None:
        self.snapshot.assert_held()
        request_id = payload.get("request_id")
        if (not isinstance(request_id, str) or not request_id.startswith(self.attempt.request_prefix)
                or request_id in self._ids or self._pending is not None):
            raise ValueError("Request ID is invalid, duplicated, or another request is pending")
        if not self._marked:
            self.attempt.mark_requests_started()
            self._marked = True
        self.snapshot.assert_held()
        if self.journal is not None:
            self.journal.sending(payload)
        self._ids.add(request_id)
        self._pending = deepcopy(payload)

    def after_response(self, payload: dict, response: dict) -> None:
        self.snapshot.assert_held()
        if self._pending is None or payload != self._pending:
            raise ValueError("Response does not match the pending request")
        self.sources.validate_response(payload["target_hour"], response)
        self.exchanges.append((deepcopy(payload), deepcopy(response)))
        self._pending = None

    def request_result(self, payload: dict, *, http_status, latency_ms, error) -> None:
        if self.journal is not None:
            self.journal.outcome(payload, http_status=http_status, latency_ms=latency_ms,
                                 error=None if error is None else "request_not_verified")

    def verify_audits(self) -> dict:
        self.snapshot.assert_held()
        if self._pending is not None or len(self.exchanges) != WARMUP_REQUESTS + MEASURED_REQUESTS:
            raise ValueError("A complete fixed-size request sequence is required for audit verification")
        return verify_prediction_audit_batch(self.snapshot, self.exchanges, self.sources,
                                             request_prefix=self.attempt.request_prefix, **self.pinned_identity)
