"""Record operational measurement attempts before requests can be sent."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from bike_mlops.capital_operational_plan import validate_operational_plan
from bike_mlops.config import PROJECT_ROOT

DEFAULT_LEDGER = PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-attempts.json"
DEFAULT_LOCK_DIR = PROJECT_ROOT / "data/evaluations/capital-bikeshare/operational-locks"
_SHA = re.compile(r"[0-9a-f]{64}")
_ATTEMPT_STATES = {"started", "completed", "failed", "interrupted"}
_RECORD_STATES = _ATTEMPT_STATES | {"reviewed_reopen"}
_FAILURE_CODES = {"preflight_failed", "snapshot_lost", "request_failed", "audit_mismatch", "interrupted", "other"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_bytes(path: Path, description: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _project_file(path: Path, directory: str) -> tuple[Path, str]:
    root = PROJECT_ROOT.resolve()
    allowed = (root / directory).resolve()
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(allowed) or resolved.suffix != ".json":
        raise ValueError(f"Evidence must be a JSON file under {directory}")
    return resolved, resolved.relative_to(root).as_posix()


def _read_plan(path: Path, *, allow_legacy: bool = False) -> tuple[dict, str, str]:
    resolved, relative = _project_file(path, "data/evaluations/capital-bikeshare/operational-plans")
    plan, file_sha = _read_json_bytes(resolved, "operational plan")
    validate_operational_plan(plan, allow_legacy=allow_legacy)
    return plan, relative, file_sha


def read_operational_ledger(path: Path = DEFAULT_LEDGER) -> dict:
    if not path.exists():
        return {"schema_version": 1, "records": []}
    ledger, _ = _read_json_bytes(path, "operational attempt ledger")
    if type(ledger.get("schema_version")) is not int or ledger["schema_version"] != 1 or not isinstance(ledger.get("records"), list):
        raise ValueError("Operational attempt ledger has an unsupported schema")
    candidates, attempt_ids = set(), set()
    for record in ledger["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("candidate_id"), str) or not _SHA.fullmatch(record["candidate_id"]):
            raise ValueError("Operational ledger has an invalid candidate")
        if record["candidate_id"] in candidates or record.get("status") not in _RECORD_STATES:
            raise ValueError("Operational ledger has duplicate candidates or invalid state")
        candidates.add(record["candidate_id"])
        attempts = record.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("Operational ledger record has no attempts")
        reviews = record.get("reviews")
        if not isinstance(reviews, list):
            raise ValueError("Operational ledger reviews must be a list")
        for index, attempt in enumerate(attempts):
            if (not isinstance(attempt, dict) or not isinstance(attempt.get("attempt_id"), str)
                    or attempt["attempt_id"] in attempt_ids or attempt.get("status") not in _ATTEMPT_STATES
                    or not isinstance(attempt.get("plan_sha256"), str) or not _SHA.fullmatch(attempt["plan_sha256"])
                    or not isinstance(attempt.get("plan_file_sha256"), str) or not _SHA.fullmatch(attempt["plan_file_sha256"])
                    or (index < len(attempts) - 1 and attempt.get("status") == "started")):
                raise ValueError("Operational ledger contains an invalid or duplicate attempt")
            attempt_ids.add(attempt["attempt_id"])
        if record["status"] != "reviewed_reopen" and record["status"] != attempts[-1]["status"]:
            raise ValueError("Operational ledger record and latest attempt states differ")
        if record["status"] == "reviewed_reopen":
            review = reviews[-1] if reviews else None
            if (attempts[-1]["status"] not in {"failed", "interrupted"} or not isinstance(review, dict)
                    or review.get("attempt_id") != attempts[-1]["attempt_id"]
                    or not isinstance(review.get("operator"), str) or not review["operator"].strip()
                    or not isinstance(review.get("reason"), str) or not review["reason"].strip()
                    or not isinstance(review.get("next_plan_sha256"), str) or not _SHA.fullmatch(review["next_plan_sha256"])
                    or not isinstance(review.get("next_plan_file_sha256"), str) or not _SHA.fullmatch(review["next_plan_file_sha256"])):
                raise ValueError("Operational ledger retry state lacks a valid review")
    return ledger


def _write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(ledger, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Operational measurement is locked by another process") from error
        yield fd
    finally:
        os.close(fd)


def _mutate(path: Path, action):
    with _exclusive_lock(path.with_suffix(path.suffix + ".lock")):
        ledger = read_operational_ledger(path)
        result = action(ledger)
        _write_ledger(path, ledger)
        return result


class MeasurementAttempt:
    """Attempt identity held while the per-candidate process lock is open."""

    def __init__(self, attempt_id: str, candidate_id: str, plan_sha256: str, plan_file_sha256: str,
                 ledger_path: Path):
        self.attempt_id = attempt_id
        self.candidate_id = candidate_id
        self.plan_sha256 = plan_sha256
        self.plan_file_sha256 = plan_file_sha256
        self.ledger_path = ledger_path
        self._active = True
        self._finished = False
        self._finish_action = None

    @property
    def request_prefix(self) -> str:
        return f"capital-op:{self.attempt_id}:"

    def mark_requests_started(self) -> None:
        """Persist intent before the first prediction request is sent."""
        if not self._active or self._finished:
            raise ValueError("Operational attempt is no longer active")

        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt can mark requests")
            attempt = record["attempts"][-1]
            if attempt.get("requests_started_at_utc") is not None:
                raise ValueError("Operational attempt has already marked request start")
            attempt["requests_started_at_utc"] = _now()

        _mutate(self.ledger_path, change)

    def finish(self, *, status: str, report_path: Path | None = None, failure_code: str | None = None) -> None:
        if not self._active or self._finished:
            raise ValueError("Operational attempt is no longer active")
        if status not in {"completed", "failed"}:
            raise ValueError("Operational attempt must be completed or failed")
        if status == "completed" and (report_path is None or failure_code is not None):
            raise ValueError("A completed operational attempt requires a report and no failure code")
        if status == "failed" and failure_code not in _FAILURE_CODES:
            raise ValueError("A failed operational attempt requires a recognized failure code")
        report = None
        if report_path is not None:
            resolved, relative = _project_file(report_path, "data/reports/capital-bikeshare")
            body, report_hash = _read_json_bytes(resolved, "operational report")
            gate = body.get("operational_gate")
            if (body.get("operational_attempt_id") != self.attempt_id
                    or body.get("operational_plan_sha256") != self.plan_sha256
                    or body.get("operational_plan_file_sha256") != self.plan_file_sha256
                    or not isinstance(gate, dict) or gate.get("status") not in {"passed", "failed", "not_evaluated"}
                    or type(body.get("completed")) is not bool
                    or (status == "completed" and (body["completed"] is not True or gate["status"] not in {"passed", "not_evaluated"}))
                    or (status == "failed" and body["completed"] is True and gate["status"] == "passed")):
                raise ValueError("Operational report does not match the attempt, plan or outcome")
            report = {"path": relative, "sha256": report_hash, "gate_status": gate["status"]}

        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt may be finished")
            attempt = record["attempts"][-1]
            if status == "failed" and attempt.get("requests_started_at_utc") and report is None:
                raise ValueError("A failed attempt after request start requires a partial report")
            if status == "completed" and not attempt.get("requests_started_at_utc"):
                raise ValueError("A completed attempt must record request start first")
            if status == "completed":
                plan, _, plan_file_sha = _read_plan(PROJECT_ROOT / attempt["plan_path"])
                identity = plan["identity"]
                if (plan["plan_sha256"] != self.plan_sha256 or plan_file_sha != self.plan_file_sha256
                        or not isinstance(body.get("model_identity"), dict)
                        or any(body["model_identity"].get(key) != value for key, value in {
                            "candidate_id": self.candidate_id, "model_name": identity["model_name"],
                            "model_version": identity["model_version"],
                            "artifact_sha256": identity["registered_artifact_sha256"],
                        }.items())):
                    raise ValueError("Operational plan or report model identity changed during the attempt")
            if report is not None:
                _, current_hash = _read_json_bytes(PROJECT_ROOT / report["path"], "operational report")
                if current_hash != report["sha256"]:
                    raise ValueError("Operational report changed before ledger commit")
            attempt.update({"status": status, "finished_at_utc": _now(), "failure_code": failure_code, "report": report})
            record["status"] = status

        # Validate before accepting the result, but defer the terminal ledger
        # write until the surrounding measurement context exits successfully.
        change(read_operational_ledger(self.ledger_path))
        self._finish_action = change
        self._finished = True

    def _interrupt_without_report(self) -> None:
        def change(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == self.candidate_id), None)
            if not record or record["status"] != "started" or record["attempts"][-1]["attempt_id"] != self.attempt_id:
                raise ValueError("Only the current started attempt can be interrupted")
            attempt = record["attempts"][-1]
            attempt.update({"status": "interrupted", "finished_at_utc": _now(), "failure_code": "interrupted",
                            "partial_evidence_missing": bool(attempt.get("requests_started_at_utc"))})
            record["status"] = "interrupted"

        _mutate(self.ledger_path, change)


@contextmanager
def measurement_attempt(plan_path: Path, *, ledger_path: Path = DEFAULT_LEDGER,
                        lock_dir: Path = DEFAULT_LOCK_DIR):
    """Record a started attempt durably before yielding; retain its lock until finished."""
    first, _, first_file_sha = _read_plan(plan_path)
    candidate_id = first["identity"]["candidate_id"]
    with _exclusive_lock(lock_dir / f"{candidate_id}.lock"):
        plan, relative, file_sha = _read_plan(plan_path)
        if file_sha != first_file_sha or plan["identity"]["candidate_id"] != candidate_id:
            raise ValueError("Operational plan changed while acquiring the candidate lock")
        attempt_id = str(uuid.uuid4())
        attempt = {"attempt_id": attempt_id, "status": "started", "plan_path": relative,
                   "plan_sha256": plan["plan_sha256"], "plan_file_sha256": file_sha,
                   "model_version": plan["identity"]["model_version"], "registry_uri": plan["registry_uri"],
                   "started_at_utc": _now(), "requests_started_at_utc": None, "report": None, "failure_code": None}

        def start(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                record = {"candidate_id": candidate_id, "status": "started", "attempts": [], "reviews": []}
                ledger["records"].append(record)
            elif record["status"] != "reviewed_reopen":
                raise ValueError(f"Candidate already has an operational attempt (status={record['status']})")
            elif (record["reviews"][-1]["next_plan_sha256"] != plan["plan_sha256"]
                  or record["reviews"][-1]["next_plan_file_sha256"] != file_sha):
                raise ValueError("Operational retry plan differs from the reviewed plan")
            record["attempts"].append(attempt)
            record["status"] = "started"

        _mutate(ledger_path, start)
        lease = MeasurementAttempt(attempt_id, candidate_id, plan["plan_sha256"], file_sha, ledger_path)
        try:
            yield lease
        except BaseException as error:
            try:
                lease._interrupt_without_report()
            except Exception as ledger_error:
                error.add_note(f"Could not persist interrupted attempt: {type(ledger_error).__name__}")
            raise
        else:
            if lease._finished:
                try:
                    _mutate(ledger_path, lease._finish_action)
                except BaseException:
                    # If validation or the write fails, never claim success.
                    # An atomic replace may already have succeeded before a
                    # directory fsync error; preserve the original exception.
                    try:
                        lease._interrupt_without_report()
                    except Exception:
                        pass
                    raise
            else:
                lease._interrupt_without_report()
        finally:
            lease._active = False


def review_operational_retry(candidate_id: str, operator: str, reason: str, *, next_plan_path: Path,
                             ledger_path: Path = DEFAULT_LEDGER, lock_dir: Path = DEFAULT_LOCK_DIR) -> None:
    """Require reasoned review and prove no cooperative runner still owns the lock."""
    if not isinstance(candidate_id, str) or not _SHA.fullmatch(candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not isinstance(operator, str) or not operator.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError("A named operator and a reason are required for retry review")
    if len(operator) > 128 or len(reason) > 1000:
        raise ValueError("Retry review operator or reason is too long")
    plan, relative, file_sha = _read_plan(next_plan_path)
    if plan["identity"]["candidate_id"] != candidate_id:
        raise ValueError("Retry plan belongs to a different candidate")
    with _exclusive_lock(lock_dir / f"{candidate_id}.lock"):
        current, _, current_file_sha = _read_plan(next_plan_path)
        if current_file_sha != file_sha or current["plan_sha256"] != plan["plan_sha256"]:
            raise ValueError("Retry plan changed while acquiring the candidate lock")
        def review(ledger):
            record = next((item for item in ledger["records"] if item["candidate_id"] == candidate_id), None)
            if not record or record["status"] not in {"failed", "started", "interrupted"}:
                raise ValueError("Only a failed or interrupted operational attempt can be reviewed")
            previous = record["attempts"][-1]
            if previous["status"] == "started":
                previous.update({"status": "interrupted", "finished_at_utc": _now(), "failure_code": "interrupted",
                                 "partial_evidence_missing": bool(previous.get("requests_started_at_utc"))})
            record.setdefault("reviews", []).append({"attempt_id": previous["attempt_id"], "operator": operator.strip(),
                                                     "reason": reason.strip(), "reviewed_at_utc": _now(),
                                                     "next_plan_path": relative, "next_plan_sha256": plan["plan_sha256"],
                                                     "next_plan_file_sha256": file_sha})
            record["status"] = "reviewed_reopen"

        _mutate(ledger_path, review)
