"""Coherent synthetic saved reports; never claims real system readiness."""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache

import pandas as pd

from bike_mlops.capital_deployment_evidence import (
    CASES, READINESS_POLICY, READINESS_POLICY_SHA256, canonical_bytes, document,
    evidence_identity, prerequisite_identity, reference_tags,
)
from bike_mlops.candidate_artifacts import candidate_identity
from bike_mlops.capital_operational_plan import PLAN_POLICY, _digest
from bike_mlops.capital_operational_registry import _validate_completed_evidence
from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY as POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 as POLICY_SHA,
    BOOTSTRAP_SELECTION_RULE, protocol_sha256,
)
from bike_mlops.quality_gates import baseline_accuracy_gate


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def resources():
    return {"host": {"os": "Linux synthetic", "architecture": "x86_64", "python": "3.12",
                     "logical_cpu_count": 4, "memory_total_bytes": 8_000_000_000, "dependencies": {}},
            "capital_stack_containers": {"status": "captured", "docker_engine_version": "synthetic",
                                         "containers": ["synthetic"], "stats": ["synthetic"]}}


@lru_cache(maxsize=1)
def _fixture():
    protocol = {
        "schema_version": 2, "protocol_name": "capital-bikeshare-bootstrap-v1", "incumbent": None,
        "dataset": {"data_profile_id": "capital-bikeshare", "target_contract": "capital-trip-starts-hourly-v1",
                    "data_sha256": "d"*64, "source_manifest_sha256": "e"*64},
        "features": {}, "candidate_recipe": {"hyperparameters": {"max_iter": 1}},
        "training": {"cutoff": "2024-01-15 00:00:00", "latest_included_label_available_at": "2024-01-15 00:00:00", "rows": 300},
        "validation": {"start_inclusive": "2024-01-15 00:00:00", "end_exclusive": "2024-01-15 02:00:00",
                       "latest_target_hour": "2024-01-15 01:00:00", "latest_label_available_at": "2024-01-15 04:00:00", "rows": 2},
        "evaluation": {"start_inclusive": "2024-01-15 04:00:00", "end_exclusive": "2024-01-15 06:00:00", "rows": 2},
        "selection_decision_time": "2024-01-15 04:00:00", "label_availability": {"delay_hours": 3},
        "split_counts": {"train": 300, "validation": 2, "reserved_evaluation": 2},
        "selection_gate": {"policy_id": POLICY["policy_id"], "policy_sha256": POLICY_SHA, "policy": POLICY,
                           "rule": BOOTSTRAP_SELECTION_RULE, "final_evaluation_used_for_selection": False},
    }
    protocol["protocol_sha256"] = protocol_sha256(protocol)
    protocol_bytes = canonical_bytes(protocol)

    def rows(start):
        times = pd.date_range(start, periods=2, freq="h")
        return pd.DataFrame({"timestamp": times, "label_available_at": times + pd.Timedelta(hours=3),
                             "actual": [2, 2], "candidate_prediction": [1.0, 1.0],
                             "baseline_prediction": [4.0, 4.0]}).to_csv(index=False).encode()

    validation_rows, final_rows = rows("2024-01-15"), rows("2024-01-15 04:00")
    metrics, baseline = {"mae": 1.0, "rmse": 1.0}, {"mae": 2.0, "rmse": 2.0}
    selection = {
        "data_sha256": "d"*64, "data_source": {"source_manifest_sha256": "e"*64},
        "source_revision": {"source_tree_sha256": _sha(b""), "source_file_count": 0},
        "environment": {"direct_dependencies": {}}, "feature_contract_version": "bike-demand-features-v1",
        "feature_columns": ["hour"], "hyperparameters": {"max_iter": 1},
        "training_cutoff": "2024-01-15 00:00:00",
        "validation_window": {"start": protocol["validation"]["start_inclusive"], "end_exclusive": protocol["validation"]["end_exclusive"]},
        "comparison": {"reference": "historical_baseline_only", "incumbent_model_version": None,
                       "incumbent_artifact_sha256": None,
                       "validation_gate": baseline_accuracy_gate(metrics, baseline, period="validation_selection")},
        "evaluation_protocol_sha256": protocol["protocol_sha256"], "training_row_keys_sha256": "3"*64,
        "validation_row_keys_sha256": "4"*64, "candidate_artifact": {"path": "model.skops", "sha256": "a"*64},
        "validation_predictions": {"path": "validation-predictions.csv", "sha256": _sha(validation_rows)},
        "source_snapshot": {"path": "source-snapshot.tar.gz", "sha256": "8"*64},
        "evaluation_protocol_artifact": {"path": "evaluation-protocol.json", "sha256": _sha(protocol_bytes)},
        "quality_gate_passed": True, "quality_gate": "synthetic fixture", "validation_gate_status": "passed",
        "quality_gate_policy_id": POLICY["policy_id"], "quality_gate_policy_sha256": POLICY_SHA,
        "validation": {"model": metrics, "baseline": baseline}, "data_profile_id": "capital-bikeshare",
        "target_contract_id": "capital-trip-starts-hourly-v1", "split_counts": {"train": 300, "validation": 2},
    }
    selection["candidate_identity"] = candidate_identity(selection)
    cid = _sha(json.dumps(selection["candidate_identity"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    selection.update(candidate_id=cid, candidate_bundle=f"data/candidates/{cid}")
    selection_bytes = canonical_bytes(selection)
    final = {
        "candidate_id": cid, "candidate_artifact_sha256": selection["candidate_artifact"]["sha256"],
        "evaluation_protocol_sha256": protocol["protocol_sha256"], "data_profile_id": "capital-bikeshare",
        "target_contract_id": "capital-trip-starts-hourly-v1", "validation_gate_status": "passed",
        "quality_gate_policy_id": POLICY["policy_id"], "quality_gate_policy_sha256": POLICY_SHA,
        "selection_report": f"data/candidates/{cid}/candidate-report.json", "candidate": {"model": metrics},
        "historical_baseline": {"model": baseline},
        "data_source": {"csv_sha256": "d"*64, "source_manifest_sha256": "e"*64},
        "evaluation_window": {**protocol["evaluation"], "used_for_candidate_selection": False,
                              "row_level_predictions": "data/reports/capital-bikeshare/final.predictions.csv",
                              "row_level_predictions_sha256": _sha(final_rows)},
        "final_accuracy_gate": baseline_accuracy_gate(metrics, baseline, period="final_evaluation"),
        "operational_gate_status": "not_evaluated", "deployment_permission": {"status": "blocked"},
        "model_fit_performed_during_evaluation": False,
    }
    final_bytes = canonical_bytes(final)
    tags = {
        "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
        "feature_contract_version": "bike-demand-features-v1", "quality_gate": "passed",
        "validation_gate_status": "passed", "final_accuracy_gate_status": "passed",
        "quality_gate_policy_id": POLICY["policy_id"], "quality_gate_policy_sha256": POLICY_SHA,
        "operational_gate_status": "not_evaluated", "deployment_permission": "blocked",
        "candidate_id": cid, "candidate_model_sha256": "a"*64, "candidate_report_sha256": _sha(selection_bytes),
        "final_report_sha256": _sha(final_bytes), "evaluation_protocol_sha256": protocol["protocol_sha256"],
        "data_sha256": "d"*64, "artifact_sha256": "1"*64,
    }
    identity = {
        "candidate_id": cid, "candidate_report_sha256": tags["candidate_report_sha256"], "final_report_sha256": tags["final_report_sha256"],
        "evaluation_protocol_sha256": tags["evaluation_protocol_sha256"], "data_sha256": "d"*64, "manifest_sha256": "e"*64,
        "candidate_artifact_sha256": "a"*64, "registered_artifact_sha256": "1"*64, "model_name": "bike-demand-capital",
        "model_version": "2", "data_profile_id": tags["data_profile_id"], "target_contract_id": tags["target_contract_id"],
        "feature_contract_version": tags["feature_contract_version"],
    }
    window = {k: identity[k] for k in ("candidate_id", "candidate_report_sha256", "final_report_sha256",
                                      "evaluation_protocol_sha256", "data_sha256", "manifest_sha256")}
    window.update(start_inclusive="2024-01-07 22:00:00", end_exclusive="2024-01-14 22:00:00", hours=168,
                  training_cutoff=selection["training_cutoff"], label_delay_hours=3, training_rows=300,
                  training_row_keys_sha256="3"*64, latest_replay_label_available_at="2024-01-15 00:00:00")
    plan = {"schema_version": 2, "kind": "capital-operational-plan", "identity": identity,
            "input_window": window, "api_url": "http://127.0.0.1:8110", "registry_uri": "http://127.0.0.1:5001",
            "policy": dict(PLAN_POLICY), "resource_context_before_requests": resources(), "deployment_permission": "blocked"}
    plan["plan_sha256"] = _digest(plan)
    plan_bytes = canonical_bytes(plan)
    attempt = "11111111-1111-4111-8111-111111111111"
    measurements = []
    for pos in range(1050):
        phase = "warmup" if pos < 50 else "measured"
        index = pos if pos < 50 else pos-50
        measurements.append({"phase": phase, "index": index, "request_id": f"capital-op:{attempt}:{phase}:{index:04d}",
            "target_hour": (pd.Timestamp(window["start_inclusive"])+pd.Timedelta(hours=index % 168)).isoformat(sep=" "),
            "http_status": 200, "latency_ms": 100.0, "prediction": 1.0, "data_version_sha256": "f"*64, "error": None})
    requests_hash = _sha("\n".join(r["request_id"] for r in measurements).encode())
    report = {
        "completed": True, "operational_attempt_id": attempt, "benchmark_id": attempt,
        "operational_plan_sha256": plan["plan_sha256"], "operational_plan_file_sha256": _sha(plan_bytes),
        "model_identity": {"candidate_id": cid, "model_name": "bike-demand-capital", "model_version": "2",
                           "artifact_sha256": "1"*64, "feature_contract_version": tags["feature_contract_version"]},
        "data_profile_id": tags["data_profile_id"], "target_contract_id": tags["target_contract_id"],
        "api_url": plan["api_url"], "input_window": {k: window[k] for k in ("start_inclusive", "end_exclusive", "hours")},
        "operational_gate": {"status": "not_evaluated", "input_provenance_status": "passed", "measurement_status": "passed",
            "observed": {"warmup_requests": 50, "warmup_errors": 0, "measured_requests": 1000, "measured_errors": 0,
                         "unique_request_ids": 1050, "retry_count": 0, "latency_count": 1000,
                         "request_id_sha256": requests_hash, "p95_ms": 100.0}},
        "audit": {"status": "passed", "verified_rows": 1050, "request_id_sha256": requests_hash},
        "snapshot": {"status": "verified", "observation_count": 744},
        "deployment_permission": {"status": "blocked"}, "resource_context": resources(), "measurements": measurements,
    }
    report_bytes = canonical_bytes(report)
    tags.update(_validate_completed_evidence(plan, report, _sha(report_bytes), attempt, _sha(plan_bytes)))
    full_identity = evidence_identity(tags, "2", "synthetic-run")
    documents = {name: document(body) for name, body in {
        "candidate": selection_bytes, "final": final_bytes, "protocol": protocol_bytes, "plan": plan_bytes,
        "operational": report_bytes, "validation_rows": validation_rows, "final_rows": final_rows,
    }.items()}
    for category, cases in CASES.items():
        checks = {case_id: document(canonical_bytes({
            "case_id": case_id, "identity": prerequisite_identity(full_identity), "completed": True,
            "observed_outcome": outcome, "observations": {"fixture": "synthetic only", "observed": outcome},
        })) for case_id, outcome in cases.items()}
        documents[category] = document(canonical_bytes({
            "schema_version": 1, "kind": "capital-deployment-prerequisite", "category": category,
            "identity": prerequisite_identity(full_identity), "policy_sha256": READINESS_POLICY_SHA256,
            "completed": True, "status": "passed", "evidence_state": "confirmed",
            "started_at_utc": "2024-02-01T00:00:00+00:00", "completed_at_utc": "2024-02-01T01:00:00+00:00",
            "checks": checks,
        }))
    bundle = {"schema_version": 1, "kind": "capital-deployment-evidence", "policy": READINESS_POLICY,
              "identity": full_identity, "reports": documents}
    body = canonical_bytes(bundle)
    tags.update(reference_tags(body, "synthetic-run"))
    return tags, bundle, body


def fixture():
    return copy.deepcopy(_fixture())
