"""Read-only inspection of operational attempt evidence; never adopt or repair it."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops.config import PROJECT_ROOT

_REPORT_PATTERN = re.compile(r"^operational-([0-9a-f-]{36})(-failure)?\.json$")


@contextmanager
def _existing_lock(path: Path):
    """Acquire an existing cooperative lock without creating or modifying it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise RuntimeError(f"Cannot open existing inspection lock: {path.name}") from error
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Inspection lock is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Inspection refused: candidate or ledger is locked") from error
        yield
    finally:
        os.close(fd)


def _canonical_attempt(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _plan_check(attempt: dict, candidate_id: str) -> tuple[dict | None, str]:
    try:
        plan, _, file_hash = ledger._read_plan(PROJECT_ROOT / attempt["plan_path"])
        if (plan["identity"]["candidate_id"] != candidate_id or plan["plan_sha256"] != attempt["plan_sha256"]
                or file_hash != attempt["plan_file_sha256"]):
            return None, "changed_or_mismatched"
    except (KeyError, OSError, TypeError, ValueError):
        return None, "unreadable_or_invalid"
    return plan, "matched"


def _read_report(path: Path) -> tuple[dict | None, str | None, str | None]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None, "missing"
    except OSError:
        return None, None, "unreadable_or_symlink"
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, None, "not_regular_file"
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path, follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)):
            return None, None, "file_changed_during_inspection"
    except OSError:
        return None, None, "unreadable_or_changed"
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > 16 * 1024 * 1024:
        return None, None, "report_too_large"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, digest, "invalid_json"
    if not isinstance(body, dict):
        return None, digest, "invalid_json_object"
    return body, digest, None


def _report_error(body: dict, attempt: dict, candidate_id: str, plan: dict | None, *, linked: bool) -> str | None:
    if (body.get("operational_attempt_id") != attempt["attempt_id"]
            or body.get("operational_plan_sha256") != attempt["plan_sha256"]
            or body.get("operational_plan_file_sha256") != attempt["plan_file_sha256"]):
        return "attempt_or_plan_mismatch"
    if "candidate_id" in body and body["candidate_id"] != candidate_id:
        return "candidate_mismatch"
    if plan is not None and any(key in body and body[key] != plan["identity"][key]
                                for key in ("data_profile_id", "target_contract_id")):
        return "profile_or_target_mismatch"
    identity = body.get("model_identity")
    if identity is not None:
        if not isinstance(identity, dict) or identity.get("candidate_id") != candidate_id:
            return "candidate_mismatch"
        if plan is not None:
            expected = plan["identity"]
            if any(identity.get(key) != value for key, value in {
                "model_name": expected["model_name"], "model_version": expected["model_version"],
                "artifact_sha256": expected["registered_artifact_sha256"],
            }.items()):
                return "model_identity_mismatch"
    if linked:
        status = attempt["status"]
        gate = body.get("operational_gate")
        if not isinstance(gate, dict):
            return "invalid_gate"
        if status == "completed" and (body.get("completed") is not True or identity is None
                                      or gate.get("status") not in {"not_evaluated", "passed"}):
            return "outcome_mismatch"
        if status == "failed" and (body.get("completed") is not False or gate.get("status") != "failed"):
            return "outcome_mismatch"
        if status not in {"completed", "failed"}:
            return "nonterminal_attempt_has_report_link"
        recorded = attempt.get("report") or {}
        if gate.get("status") != recorded.get("gate_status"):
            return "gate_status_mismatch"
    return None


def inspect_operational_reports(candidate_id: str, *, ledger_path: Path = ledger.DEFAULT_LEDGER,
                                lock_dir: Path = ledger.DEFAULT_LOCK_DIR) -> dict:
    """List evidence states while both existing candidate and ledger locks are held.

    Files and ledger remain unchanged. Even a linked or matching orphan is not
    an operational gate decision. An unrelated unassociated filename is listed
    separately because its candidate identity cannot be established safely.
    """
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not ledger_path.is_file():
        raise ValueError("Operational ledger does not exist")
    report_root = PROJECT_ROOT / "data/reports/capital-bikeshare"
    if not report_root.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("Operational report directory is outside the project")
    with _existing_lock(lock_dir / f"{candidate_id}.lock"):
        with _existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational ledger record")
            all_ids = {item["attempt_id"] for rec in state["records"] for item in rec["attempts"]}
            attempts, examined = [], set()
            for attempt in record["attempts"]:
                attempt_id = attempt["attempt_id"]
                if not _canonical_attempt(attempt_id):
                    raise ValueError("Ledger contains a noncanonical attempt ID")
                plan, plan_status = _plan_check(attempt, candidate_id)
                names = [f"operational-{attempt_id}.json", f"operational-{attempt_id}-failure.json"]
                examined.update(names)
                item = {"attempt_id": attempt_id, "status": attempt["status"], "plan_status": plan_status,
                        "requests_started": bool(attempt.get("requests_started_at_utc")),
                        "partial_evidence_missing": attempt.get("partial_evidence_missing"),
                        "linked_report": {"status": "not_recorded"}, "orphans": []}
                descriptor = attempt.get("report")
                linked_name = None
                if descriptor is not None:
                    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
                        item["linked_report"] = {"status": "invalid", "reason": "invalid_descriptor"}
                    else:
                        relative = descriptor["path"]
                        expected_relatives = {f"data/reports/capital-bikeshare/{name}": name for name in names}
                        linked_name = expected_relatives.get(relative)
                        if linked_name is None:
                            item["linked_report"] = {"status": "invalid", "reason": "unexpected_report_path"}
                        else:
                            body, digest, error = _read_report(report_root / linked_name)
                            if error:
                                item["linked_report"] = {"status": "missing" if error == "missing" else "invalid", "reason": error}
                            elif digest != descriptor.get("sha256"):
                                item["linked_report"] = {"status": "invalid", "reason": "file_sha256_mismatch", "sha256": digest}
                            else:
                                issue = _report_error(body, attempt, candidate_id, plan, linked=True)
                                if plan is None:
                                    issue = issue or "plan_not_matched"
                                item["linked_report"] = {"status": "invalid" if issue else "matched",
                                                         "reason": issue, "sha256": digest, "path": relative}
                                item["linked_report"]["candidate_identity_source"] = (
                                    "report_and_plan" if isinstance(body.get("model_identity"), dict)
                                    else "ledger_and_plan") if plan is not None else "unverified"
                for name in names:
                    if name == linked_name:
                        continue
                    body, digest, error = _read_report(report_root / name)
                    if error == "missing":
                        continue
                    issue = error or _report_error(body, attempt, candidate_id, plan, linked=False)
                    if plan is None:
                        issue = issue or "plan_not_matched"
                    item["orphans"].append({"path": f"data/reports/capital-bikeshare/{name}",
                                            "status": "invalid" if issue else "matches_attempt_not_adopted",
                                            "reason": issue, "sha256": digest})
                item["unresolved"] = (attempt["status"] in {"started", "interrupted"}
                                      or (attempt["status"] in {"completed", "failed"}
                                          and item["linked_report"]["status"] == "not_recorded")
                                      or item["linked_report"]["status"] in {"missing", "invalid"}
                                      or bool(item["orphans"]) or plan_status != "matched")
                item["evidence_state"] = (
                    "unfinished" if attempt["status"] in {"started", "interrupted"}
                    else "incomplete_or_corrupt" if item["linked_report"]["status"] != "matched" or plan_status != "matched"
                    else "linked_with_orphans" if item["orphans"]
                    else "linked")
                attempts.append(item)
            unassociated = []
            if report_root.is_dir():
                for path in sorted(report_root.glob("operational-*.json")):
                    if path.name in examined:
                        continue
                    match = _REPORT_PATTERN.fullmatch(path.name)
                    if not match or not _canonical_attempt(match.group(1)):
                        unassociated.append({"name": path.name, "reason": "unrecognized_filename"})
                    elif match.group(1) not in all_ids:
                        unassociated.append({"name": path.name, "reason": "unknown_attempt_id"})
            return {"candidate_id": candidate_id, "ledger_status": record["status"], "attempts": attempts,
                    "unassociated_report_files": unassociated, "decision": "inspection_only_no_approval"}
