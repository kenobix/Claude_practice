"""Bounded natural-clock probe using only FakeRegistry and a temporary lock root."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from failure_diagnostics import FailureTrace, trace_decision_clocks
from test_capital_deployment_decisions import eligible_registry, FakeRegistry
from bike_mlops import capital_operational_registry as registry
from bike_mlops.capital_deployment_decisions import record_operational_review, record_deployment_approval


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=160)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    if not 1 <= args.samples <= 200 or not 0 <= args.interval <= 1:
        parser.error("samples must be 1..200 and interval 0..1")
    args.output.mkdir(parents=True, exist_ok=False)
    trace = FailureTrace(args.output, "natural-decision")
    template = eligible_registry()
    status = "no_rejection_in_bounded_observation"
    outcome = None
    with tempfile.TemporaryDirectory(prefix="natural-clock-fake-registry-") as root:
        with patch.object(registry, "PROJECT_ROOT", Path(root)), trace_decision_clocks(trace):
            for index in range(args.samples):
                client = FakeRegistry(template.tags, body=template.body)
                trace.emit("probe_iteration", index=index)
                stage = "review"
                try:
                    record_operational_review(client, "2", "synthetic-reviewer", "passed", "natural-clock probe only")
                    before = dict(client.tags)
                    review = datetime.fromisoformat(before["capital_operational_review_at_utc"])
                    time.sleep(args.interval)
                    observed = datetime.now(timezone.utc)
                    trace.emit("probe_after_wait", review_utc=review.isoformat(), now_utc=observed.isoformat())
                    if observed >= review:
                        continue
                    stage = "approval"
                    record_deployment_approval(client, "2", "synthetic-approver", "natural-clock probe only")
                    # The clock may have caught up during evidence checks; retain the observation.
                    trace.emit("clock_caught_up_before_validation")
                except ValueError as error:
                    if str(error) != "Evidence timestamp must not be in the future":
                        raise
                    if stage == "approval" and client.tags != before:
                        raise AssertionError("Clock failure unexpectedly wrote approval tags")
                    if client.tags.get("deployment_permission") != "blocked":
                        raise AssertionError("Clock failure unblocked a synthetic candidate")
                    status = "observed_natural_future_rejection"
                    outcome = {"stage": stage, "index": index, "notes": getattr(error, "__notes__", []),
                               "deployment_permission": "blocked", "approval_tags_unchanged": stage == "approval"}
                    (args.output / "future-rejection-traceback.txt").write_text("".join(traceback.format_exception(error)))
                    trace.emit("natural_future_rejection", **outcome)
                    break
    result = {"status": status, "scope": "synthetic_fake_registry_only", "clock_mocked": False,
              "actual_database_registry_or_deployment_used": False, "samples_attempted": index + 1,
              "outcome": outcome,
              "limitation": "A present-day natural failure does not establish the historical failure's clock readings."}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
